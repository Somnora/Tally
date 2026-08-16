"""Evidence validation: the anti-fabrication gate for evaluations.

verify.py asks "did the candidate really say this?". promise_gate.py asks "is
what they said a promise?". This module asks the last question: "does the
record the model cited actually say what the model claims it says?".

Invariant 2 is the reason it exists. An evaluation may ship only when every
citation points at a real record that genuinely supports the stated
direction. A model that names a plausible vote_id is indistinguishable, in
prose, from one that looked it up. The difference is only visible if code
checks, so code checks. Nothing here trusts the model's own account of a
record; the facts arrive from the database and the claim is tested against
them.

Pure by design: no DB, no LLM, no network. Facts are passed in. That keeps
every rule below exhaustively testable without a model endpoint or fixtures.

What a citation must survive:

  1. The record exists.       A missing row means the id was invented.
  2. It belongs to this member. Every vote_id is a valid vote_id, so a
     foreign key cannot catch a citation of someone else's vote. Only an
     ownership check can, and cross-member citation is exactly the mistake a
     model makes when a promise mentions an opponent.
  3. It was actually offered. A real vote by the right member is still not
     admissible if it was never in the prompt. The prompt says "cite only ids
     from the list below"; without this check that sentence is advice, and
     the model could reach into any of the member's several hundred other
     votes on any topic and still look correct.
  4. The model read it correctly. Each citation states the position it saw,
     and that is compared against the stored position. A misread row yields a
     confident sentence just like a correct one, so the only way to tell them
     apart is to make the model commit to a checkable fact first.
  5. The direction is one the record can carry:
       - an omnibus vote is contextual only. A yea on a 153-subject
         appropriations act is not clean support for any one promise in it.
       - a procedural vote is contextual only. Recommittal and previous
         question are positions on process, not on policy.
       - a donation is contextual only. "Took money from X, therefore broke
         the promise" is an inference, not evidence, and the editorial line
         is that money is shown as context for the reader to weigh, never
         used to drive a verdict.

Statuses then have to agree with what survived. A verdict of 'broken' with
nothing contradictory cited is not a finding, it is an assertion; a verdict
of 'unverifiable' that cites evidence is incoherent in the other direction.
The caller stores a failing evaluation with is_current = FALSE so it is kept
for review but can never reach the export view.
"""

from dataclasses import dataclass
from typing import Literal

Direction = Literal["supports", "contradicts", "contextual"]
Kind = Literal["vote", "donation", "lobbying_filing", "document"]

# Statuses whose verdict rests on the record, and the direction each needs.
STATUS_REQUIRES: dict[str, Direction] = {
    "completed": "supports",
    "in_progress": "supports",
    "broken": "contradicts",
}
# Statuses that mean "the record did not settle this", so nothing may be cited.
UNEVIDENCED_STATUSES = frozenset({"pending", "unverifiable"})

# Records that can supply context but must never carry a verdict's weight.
CONTEXTUAL_ONLY_KINDS = frozenset({"donation", "lobbying_filing"})


@dataclass(frozen=True)
class ClaimedEvidence:
    """One citation exactly as the model returned it.

    `claimed_position` is the position the model says it read ("yea"/"nay").
    Requiring it is the same trick verify.py plays with verbatim quotes: make
    the model commit to a fact that can be checked mechanically. A model that
    misreads the vote list writes a confident sentence either way, and the
    misreading is invisible in prose but obvious against the database.
    """

    kind: str
    record_id: int
    direction: str
    claimed_position: str | None = None


@dataclass(frozen=True)
class VoteFact:
    """What the database actually knows about a cited vote."""

    vote_id: int
    politician_id: int
    position: str
    vote_question: str
    bill_key: str | None
    is_omnibus: bool
    is_procedural: bool


@dataclass(frozen=True)
class CitationCheck:
    """Verdict on one citation. `reason` is a slug, so stats can count it."""

    claim: ClaimedEvidence
    accepted: bool
    reason: str


# Outcomes where the cited vote genuinely exists, so the citation can still be
# stored against its foreign key with validated = FALSE. Anything else has no
# row to point at (or would violate a CHECK) and belongs in
# evaluation_citation_rejects instead.
STORABLE_REASONS = frozenset({
    "ok",
    "omnibus_not_contextual",
    "procedural_not_contextual",
    "wrong_politician",
    "not_offered",
    "position_mismatch",
})


def can_store_as_evidence(check: "CitationCheck") -> bool:
    """Can this citation be written to evaluation_evidence at all?

    A fabricated id has no row for the foreign key to reference, which is
    precisely the property that makes fabrication detectable. Those are kept
    in the rejects table instead of being silently dropped.
    """
    return check.claim.kind == "vote" and check.reason in STORABLE_REASONS


def check_citation(
    claim: ClaimedEvidence,
    *,
    politician_id: int,
    vote_facts: dict[int, VoteFact],
    offered_vote_ids: frozenset[int] | None = None,
) -> CitationCheck:
    """Test one citation against the facts. Never raises; every rejection
    carries a reason so a bad prompt is diagnosable from the stats alone.

    offered_vote_ids is the set of votes actually shown to the model. Pass
    None to skip that check, which is what revalidation does: the offered set
    was never stored, so a later pass cannot re-derive it.
    """
    if claim.direction not in ("supports", "contradicts", "contextual"):
        return CitationCheck(claim, False, "bad_direction")

    if claim.kind in CONTEXTUAL_ONLY_KINDS:
        if claim.direction != "contextual":
            return CitationCheck(claim, False, f"{claim.kind}_not_contextual")
        # v1 prompts cite votes only; a donation citation is well-formed but
        # unverified until the finance validator exists, so it is not accepted.
        return CitationCheck(claim, False, "kind_not_validatable_yet")

    if claim.kind != "vote":
        return CitationCheck(claim, False, "unsupported_kind")

    fact = vote_facts.get(claim.record_id)
    if fact is None:
        return CitationCheck(claim, False, "unknown_record")
    if fact.politician_id != politician_id:
        return CitationCheck(claim, False, "wrong_politician")
    if offered_vote_ids is not None and claim.record_id not in offered_vote_ids:
        # A real vote by the right member that was never in the prompt. The
        # prompt says "cite only ids from the list below", and without this
        # the model could reach into any of the member's several hundred other
        # votes, on any topic, and the citation would still look valid.
        return CitationCheck(claim, False, "not_offered")
    if claim.claimed_position is not None and claim.claimed_position != fact.position:
        # The model read the row wrong. Its direction is then untrustworthy
        # even if it happens to be right.
        return CitationCheck(claim, False, "position_mismatch")
    if fact.is_omnibus and claim.direction != "contextual":
        return CitationCheck(claim, False, "omnibus_not_contextual")
    if fact.is_procedural and claim.direction != "contextual":
        return CitationCheck(claim, False, "procedural_not_contextual")
    return CitationCheck(claim, True, "ok")


def check_citations(
    claims: list[ClaimedEvidence],
    *,
    politician_id: int,
    vote_facts: dict[int, VoteFact],
    offered_vote_ids: frozenset[int] | None = None,
) -> list[CitationCheck]:
    """Check every citation, dropping exact duplicates.

    A model that cites the same vote three times has not found three pieces
    of evidence, and letting the repeats through would inflate any count of
    how well supported a verdict is.
    """
    seen: set[tuple[str, int, str]] = set()
    checks: list[CitationCheck] = []
    for claim in claims:
        key = (claim.kind, claim.record_id, claim.direction)
        if key in seen:
            continue
        seen.add(key)
        checks.append(
            check_citation(
                claim, politician_id=politician_id, vote_facts=vote_facts,
                offered_vote_ids=offered_vote_ids,
            )
        )
    return checks


def status_is_supported(status: str, checks: list[CitationCheck]) -> tuple[bool, str]:
    """Does the verdict agree with the citations that survived?

    Returns (ok, reason). The caller stores a failing evaluation with
    is_current = FALSE: kept for review, never exported.
    """
    accepted = [c for c in checks if c.accepted]

    if status in UNEVIDENCED_STATUSES:
        # These mean the record could not settle the question. Citing
        # something contradicts the verdict itself.
        if checks:
            return False, "unevidenced_status_with_citations"
        return True, "ok"

    required = STATUS_REQUIRES.get(status)
    if required is None:
        return False, "unknown_status"
    if not accepted:
        return False, "no_valid_evidence"
    if not any(c.claim.direction == required for c in accepted):
        return False, f"no_{required}_evidence"
    return True, "ok"
