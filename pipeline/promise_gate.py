"""Promise selectivity gate: a deterministic keep-or-drop screen for quotes.

pipeline/verify.py answers "did the candidate really say this?". This module
answers the next question: "is what they said a promise?".

Human review of 118 extract_v2 promises found 47 extraction errors (60.2%
precision), and the failure taxonomy (data/review/failure_taxonomy.md) showed
the dominant class is LEXICALLY MARKED: the candidate frames a policy view as
a belief ("I think", "Chellie believes") or a hope ("I'm hoping to") instead
of committing to do anything. 26 of the 47 leaks are that one pattern.

Like verify.py this is pure (no DB, no LLM, no network), so the screen is
versioned in git, model independent, and exhaustively testable. A prompt is
advice to a model; this is a rule the model cannot talk its way past.

The gate is deliberately FAIL-OPEN. Anything it does not recognise is kept,
because dropping a real promise is the expensive error: a leaked non-promise
is visible to a human reviewer and to the evaluation stage's evidence
validation, while a dropped promise is silently gone.

Decision ladder, first rule that fires wins:

  Structural rules (never escapable, the quote is disqualified as an object):
    1. fragment             not a complete proposition: too short to carry
                            one, ends mid-word, is prose cut off before its
                            sentence ended, or is a headless gerund phrase
                            lifted out of a bulleted list ("reforming the tax
                            code to rebuild the middle class") where the
                            heading, not the bullet, carried the commitment.
    2. reported_speech      a broadcast caption or reporter summary ABOUT a
                            candidate ("RUSSELL SAYS HE'LL SUPPORT ..."),
                            not the candidate speaking.
    3. past_action          a completed act or a counterfactual about the
                            past ("I did that because ...", "I would have
                            renewed them").
    4. constituent_casework an office-help offer ("call our office and we
                            will do our best"), not a governing commitment.

  Frame rules (ESCAPED by an explicit commitment in the same quote):
    5. candidacy_motivation why the person is running, not what they would
                            do in office ("Matt is running to ...").
    6. campaign_bio         self-identification and credentials ("I am an
                            Airborne Ranger ...").
    7. ongoing_activity     a status report on work already under way
                            ("Chellie is leading the fight ...").
    8. third_party_belief   campaign copy attributing a belief rather than a
                            commitment ("Chellie believes ...").
    9. normative_claim      an impersonal "X should be Y" with no actor who
                            has agreed to do anything.
   10. hedged_opinion       the statement is framed as the speaker's belief
                            ("I think ...", "I don't think ...").
   11. aspirational_hope    a hope or an exploration, not a decision ("I'm
                            hoping to ...", "we're looking at ...").

Rules 5-11 test the FRAME, not the presence of a phrase. They are skipped
entirely when the quote also contains an explicit commitment by the candidate
("I will ...", "we'll ...", "Paul will ..."), so

    "I will vote against any cut to Medicaid, because I think families
     deserve better"

is KEPT: the epistemic marker is subordinate to a commitment. That escape is
the whole reason this is a gate on frames and not a stoplist.

Measured against data/review/gold_v2.jsonl by pipeline/eval_extraction.py:
41 of 47 leaks dropped, 1 of 71 real promises lost, precision 60.2% -> 92.1%,
retention 98.6%. Those numbers are in-sample: the rules were written while
looking at that set, so treat them as a development-set ceiling, not a
forecast.

WHAT THIS DOES NOT CATCH, by design. Three of the taxonomy's nine patterns
are semantic, not lexical, and the prompt has to carry them:
  - third-party speech that is not marked as reported ("We need a senator
    who...", said by an opponent). Nothing in the text says who is talking.
  - simple past record ("I voted for that bill"). Distinguishing tense
    reliably needs a parser, and none of the 47 leaks were this, so no rule
    was written for something that could not be measured.
  - procedural commitments ("I'll get back to you"), which look exactly
    like pledges and are escaped by the commitment rule on purpose.
"""

import re
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Literal

# v2 added the headless-gerund case to rule 1. The stamp has to move with the
# rules: two different rulesets both claiming "gate_v1" would make a stored
# verdict impossible to reproduce from the version that names it.
GATE_VERSION = "gate_v2"

# Below this a span cannot carry a whole proposition ("we'll we'll fight
# back"). Deliberately shorter than any plausible complete promise: the
# shortest real one we can construct, "I will vote no on H.R. 1.", is 25.
MIN_QUOTE_CHARS = 25

DropReason = Literal[
    "fragment",
    "reported_speech",
    "past_action",
    "constituent_casework",
    "candidacy_motivation",
    "campaign_bio",
    "ongoing_activity",
    "third_party_belief",
    "normative_claim",
    "hedged_opinion",
    "aspirational_hope",
]
GateReason = Literal["kept"] | DropReason

STRUCTURAL_RULES: tuple[DropReason, ...] = (
    "fragment",
    "reported_speech",
    "past_action",
    "constituent_casework",
)
FRAME_RULES: tuple[DropReason, ...] = (
    "candidacy_motivation",
    "campaign_bio",
    "ongoing_activity",
    "third_party_belief",
    "normative_claim",
    "hedged_opinion",
    "aspirational_hope",
)
RULE_NAMES: tuple[DropReason, ...] = STRUCTURAL_RULES + FRAME_RULES


@dataclass(frozen=True)
class GateDecision:
    """Outcome of screening one extracted quote.

    reason is "kept", or the name of the single rule that dropped it, so a
    rejection can be stored and audited the way extraction_rejects stores
    quote-verification failures.
    """

    keep: bool
    reason: GateReason


# -- commitment: the escape hatch for every frame rule -------------------------

# First person. "we'll" needs its apostrophe or "well-being" reads as a pledge.
_SELF_COMMITMENT = re.compile(
    r"""
      \b(?:i|we)\s+(?:will|shall)\b
    | \b(?:i|we)['’]ll\b
    | \b(?:i|we)\s+(?:intend|pledge|vow|promise|commit)\b
    | \b(?:i['’]m|i\s+am|we['’]re|we\s+are)\s+(?:going|gonna)\b
    | \b(?:my|our)\s+(?:first|number\s+one)\b
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Third person, the campaign-site voice the prompt explicitly blesses as the
# candidate's own commitment ("Paul will fight to...", "Chellie favors...").
# Case sensitive: the capitalised name is the signal.
_NAMED_COMMITMENT = re.compile(
    r"\b[A-Z][a-z]+\s+(?:will|would|favors|favours|supports|opposes|backs|has\s+always)\b"
    r"|\b[A-Z][a-z]+\s+is\s+opposed\b"
)
_PRONOUN_COMMITMENT = re.compile(r"\b(?:he|she|they)\s+will\b|\b(?:he|she|they)['’]ll\b", re.I)


def has_commitment(quote: str) -> bool:
    """True when the quote contains an explicit pledge by the candidate.

    This is what separates "I think families deserve better" (a belief) from
    "I will vote against any cut to Medicaid, because I think families
    deserve better" (a commitment that happens to contain a belief).
    """
    return bool(
        _SELF_COMMITMENT.search(quote)
        or _NAMED_COMMITMENT.search(quote)
        or _PRONOUN_COMMITMENT.search(quote)
    )


# -- structural rules ----------------------------------------------------------

_TERMINAL_PUNCTUATION = (".", "!", "?", '"', "”", "'", "’", ":", ";")
_ENDS_MID_WORD = re.compile(r"(?:^|\s)[b-hj-z]\s*$", re.IGNORECASE)  # a lone letter, not "a"/"I"
_INTERNAL_SENTENCE_END = re.compile(r"[.!?][\"'”’)]?\s+\S")

# An -ing word opening the span. Official-site issue pages are built out of
# bulleted priority lists ("My plan will: Doubling the child tax credit, ...")
# and the extractor lifts the bullets out from under the heading that supplied
# the commitment, leaving a headless phrase behind.
_GERUND_HEAD = re.compile(r"^(?:and\s+|by\s+)?(\w+ing)\b", re.IGNORECASE)
# -ing words that open a sentence without being a gerund, so the head test
# does not misread an ordinary sentence as a list bullet.
_NOT_GERUNDS = frozenset(
    {"bring", "thing", "nothing", "something", "everything", "anything", "king",
     "ring", "sing", "spring", "string", "wing", "during", "ceiling", "willing",
     "morning", "evening", "sibling"}
)
# Any finite verb: its presence means the span has a real clause, whatever it
# starts with. Deliberately generous, because the fail-open bias means a miss
# here costs a leaked bullet while a false hit costs a real promise.
_FINITE_VERB = re.compile(
    r"\b(?:will|would|shall|must|should|can|could|may|might"
    r"|is|are|am|was|were|has|have|had|does|do|did"
    # "costs", "needs" and "matters" are left out on purpose: in this genre
    # they are far more often nouns ("the high costs facing parents", "the
    # needs of veterans") than verbs, and admitting them would let a bullet
    # keep itself by naming one. Bare "need" stays, for "we need to".
    r"|means|need|remains|deserves|requires|includes|continues"
    r"|allows|provides|ensures|helps|makes|gives|takes|works|belongs)\b",
    re.IGNORECASE,
)


def _is_headless_gerund(quote: str) -> bool:
    """A bullet lifted out of the list whose heading carried the commitment.

    "reforming the tax code to rebuild the middle class" states a topic, not a
    proposition: nobody in it has agreed to do anything. A gerund SUBJECT is a
    different thing and stays ("Promoting transparency ... is crucial",
    "growing an economy ... means making investments"), which is why this asks
    for a finite verb anywhere rather than parsing the phrase.
    """
    head = _GERUND_HEAD.match(quote)
    if not head or head.group(1).lower() in _NOT_GERUNDS:
        return False
    return not _FINITE_VERB.search(quote)


def _is_fragment(quote: str) -> bool:
    if len(quote) < MIN_QUOTE_CHARS:
        return True
    if _ENDS_MID_WORD.search(quote):
        return True
    if _is_headless_gerund(quote):
        return True
    # Prose that already ended one sentence but stops mid-way through the
    # next one was cut off ("...which essentially transform semi-autom").
    # Unpunctuated auto-captions have no internal terminator, so they are
    # untouched by this.
    return not quote.endswith(_TERMINAL_PUNCTUATION) and bool(_INTERNAL_SENTENCE_END.search(quote))


_REPORTED_SPEECH = re.compile(
    r"\b(?:says|said|claims|claimed|pledges|pledged|vows|vowed)\s+"
    r"(?:he|she|they|that\s+he|that\s+she|that\s+they)\b",
    re.IGNORECASE,
)


def _is_reported_speech(quote: str) -> bool:
    return bool(_REPORTED_SPEECH.search(quote))


_PAST_ACTION = re.compile(
    r"\b(?:i|we)\s+(?:would|could|should)\s+have\b"  # counterfactual about the past
    r"|^\W*(?:i|we)\s+did\s+that\b",
    re.IGNORECASE,
)


def _is_past_action(quote: str) -> bool:
    return bool(_PAST_ACTION.search(quote))


_CASEWORK = re.compile(
    r"\b(?:call|contact|reach\s+out\s+to|email|write)\s+(?:our|my|the)\s+office\b"
    r"|\b(?:our|my)\s+office\s+(?:can|will)\s+help\b",
    re.IGNORECASE,
)


def _is_constituent_casework(quote: str) -> bool:
    return bool(_CASEWORK.search(quote))


# -- frame rules ---------------------------------------------------------------

_CANDIDACY_MOTIVATION = re.compile(
    r"\b(?:is|am|are|['’]s|['’]m|['’]re)\s+running\s+(?:to|for|because)\b"
    r"|\b(?:i|we)\s+want\s+to\s+serve\b"
    r"|\bwhy\s+(?:i|we)['’]?(?:m|re)?\s*running\b",
    re.IGNORECASE,
)


def _is_candidacy_motivation(quote: str) -> bool:
    return bool(_CANDIDACY_MOTIVATION.search(quote))


# Identity predication with an indefinite article is a resume line. "I am the
# only candidate who has always voted pro-choice" uses the definite article
# and is not caught, which is the intended split.
_CAMPAIGN_BIO = re.compile(
    r"^\W*(?:i\s+am|i['’]m)\s+an?\s+"
    r"|^\W*[A-Z][a-z]+\s+(?:is|was)\s+an?\s+(?:lifelong|proud|former|retired|small|third|fourth)?",
    re.IGNORECASE,
)


def _is_campaign_bio(quote: str) -> bool:
    return bool(_CAMPAIGN_BIO.search(quote))


_ONGOING_ACTIVITY = re.compile(
    r"\b(?:is|are|['’]s|['’]re)\s+(?:currently\s+)?(?:leading|negotiating|working\s+on)\b"
    r"|\b(?:our|the)\s+negotiations?\s+(?:is|are)\b",
    re.IGNORECASE,
)


def _is_ongoing_activity(quote: str) -> bool:
    return bool(_ONGOING_ACTIVITY.search(quote))


# Case sensitive: a capitalised name plus a belief verb is campaign copy
# reporting what the candidate holds, not what they have agreed to do.
_THIRD_PARTY_BELIEF = re.compile(r"\b[A-Z][a-z]+\s+(?:believes|thinks|feels|hopes)\b")


def _is_third_party_belief(quote: str) -> bool:
    return bool(_THIRD_PARTY_BELIEF.search(quote))


_FIRST_PERSON = re.compile(r"\b(?:i|we|my|our|me|us|mine|ours)\b", re.IGNORECASE)
_NORMATIVE_PREDICATE = re.compile(
    r"\bshould\b|\bought\s+to\b|\bmust\s+be\b"
    r"|\b(?:it['’]?s|is)\s+past\s+time\b"
    r"|\b(?:is|are)\s+(?:critical|essential|vital|imperative|non-negotiable|unacceptable)\b",
    re.IGNORECASE,
)


def _is_normative_claim(quote: str) -> bool:
    """An impersonal ought-claim: nobody in the sentence agreed to do it.

    Requires the absence of any first person marker, so "we need to restore
    the ACA tax credits" (the speaker includes themselves in the actor) is
    untouched, while "Care should not be dictated by pharmaceutical
    interests" is dropped.
    """
    if _FIRST_PERSON.search(quote):
        return False
    return bool(_NORMATIVE_PREDICATE.search(quote))


_BELIEF_VERB = r"(?:think|believe|feel|suspect)"
_HEDGE_ADVERB = (
    r"(?:do|don['’]?t|do\s+not|really|certainly|honestly|personally|would|also|just"
    r"|firmly|strongly|truly|necessarily|still|guess|actually)\s+"
)
_FILLER = r"(?:and|so|but|or|um+|uh+|well|you\s+know|now|then|again|like\s+\w+)[,\s]+"
# The belief verb must OPEN a clause (quote start, after terminal or comma
# punctuation, or after a coordinator). A parenthetical "our number one
# priority I think as a representative is ..." is not a belief frame.
_HEDGED_OPINION = re.compile(
    rf"(?:^|[.,;:!?]\s*|\b(?:and|but|so)\s+)(?:{_FILLER})*"
    rf"(?:i|we)\s+(?:{_HEDGE_ADVERB})*{_BELIEF_VERB}\b",
    re.IGNORECASE,
)


def _is_hedged_opinion(quote: str) -> bool:
    return bool(_HEDGED_OPINION.search(quote))


_ASPIRATIONAL_HOPE = re.compile(
    r"\b(?:hoping|hope)\s+to\b"
    r"|\b(?:i|we)\s*['’]?(?:m|re)\s+(?:looking\s+at|exploring|considering)\b"
    r"|\b(?:i\s+am|we\s+are)\s+(?:looking\s+at|exploring|considering)\b",
    re.IGNORECASE,
)


def _is_aspirational_hope(quote: str) -> bool:
    return bool(_ASPIRATIONAL_HOPE.search(quote))


_Check = tuple[DropReason, Callable[[str], bool]]

_STRUCTURAL_CHECKS: tuple[_Check, ...] = (
    ("fragment", _is_fragment),
    ("reported_speech", _is_reported_speech),
    ("past_action", _is_past_action),
    ("constituent_casework", _is_constituent_casework),
)
_FRAME_CHECKS: tuple[_Check, ...] = (
    ("candidacy_motivation", _is_candidacy_motivation),
    ("campaign_bio", _is_campaign_bio),
    ("ongoing_activity", _is_ongoing_activity),
    ("third_party_belief", _is_third_party_belief),
    ("normative_claim", _is_normative_claim),
    ("hedged_opinion", _is_hedged_opinion),
    ("aspirational_hope", _is_aspirational_hope),
)


def screen_promise(
    quote: str,
    disabled_rules: Collection[str] = (),
    escape_on_commitment: bool = True,
) -> GateDecision:
    """Decide whether an extracted quote should be kept as a promise.

    Args:
        quote: the verbatim quote, already through verify_quote. Leading and
            trailing whitespace is ignored.
        disabled_rules: rule names to skip, for ablation runs in
            pipeline/eval_extraction.py. Production passes nothing.
        escape_on_commitment: keep the frame rules subordinate to an explicit
            pledge. Only ever False in the harness, to measure what turning
            the frame rules into a phrase stoplist would cost.

    Returns the first rule that fires, or ("kept", True) when none does.
    """
    text = quote.strip()
    if not text:
        return GateDecision(False, "fragment")
    for name, fires in _STRUCTURAL_CHECKS:
        if name not in disabled_rules and fires(text):
            return GateDecision(False, name)
    if escape_on_commitment and has_commitment(text):
        return GateDecision(True, "kept")
    for name, fires in _FRAME_CHECKS:
        if name not in disabled_rules and fires(text):
            return GateDecision(False, name)
    return GateDecision(True, "kept")
