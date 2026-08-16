"""Tests for the promise selectivity gate (pipeline/promise_gate.py).

Two jobs. First, every rule the gate claims to catch has a test taken from
the real failure taxonomy (data/review/failure_taxonomy.md), so a regex edit
that silently stops catching a pattern fails here. Second, and more
important, the NEAR MISSES: real promises that contain an epistemic marker
must survive. A gate that mangles real promises is worse than no gate, so
those cases are asserted explicitly rather than left to the aggregate score.

Quote fixtures are verbatim from data/review/gold_v2.jsonl, with the gold
promise_id in the comment so a disputed label can be traced back.
"""

from pipeline.promise_gate import GATE_VERSION, RULE_NAMES, has_commitment, screen_promise


def _drop_reason(quote: str) -> str:
    decision = screen_promise(quote)
    assert not decision.keep, f"expected DROP, got keep: {quote!r}"
    return decision.reason


def _assert_kept(quote: str) -> None:
    decision = screen_promise(quote)
    assert decision.keep, f"expected KEEP, dropped as {decision.reason}: {quote!r}"


# -- pattern 1: hedged opinions (26 of the 47 leaks) ---------------------------

def test_first_person_belief_is_dropped() -> None:
    # gold 234
    assert _drop_reason("I think that's exactly the way it should be.") == "hedged_opinion"


def test_negated_belief_is_dropped() -> None:
    # gold 217
    assert _drop_reason(
        "I don't think that we should prevent them from being able to trade. We certainly "
        "they ought to be arrested and thrown in jail if they are trading using insider "
        "information."
    ) == "hedged_opinion"


def test_belief_hedging_a_support_statement_is_dropped() -> None:
    # gold 228: "I think I do support" is a stance, not a commitment
    assert _drop_reason(
        "I think I do support a universal health care option, but allowing people to keep "
        "relationship with their current doctor, current provider, and everything else."
    ) == "hedged_opinion"


def test_belief_opening_a_later_clause_is_dropped() -> None:
    # gold 219: the belief frame starts after a comma, not at the quote start
    assert _drop_reason(
        "members of Congress, I don't think should be allowed to trade stocks while in "
        "office, and I think whether that's a something that goes into a blindly managed "
        "fund or a public sell-off, I think we do need some some real reform in Congress."
    ) == "hedged_opinion"


def test_i_believe_in_list_of_values_is_dropped() -> None:
    # gold 211
    assert _drop_reason(
        "I believe in free education, ending hunger, housing first solutions, and "
        "universal healthcare."
    ) == "hedged_opinion"


# -- NEAR MISSES: real promises that contain an epistemic marker ---------------

def test_commitment_containing_i_think_is_kept() -> None:
    # The central design case: the pledge governs, the belief is its reasoning.
    quote = "I will vote against any cut to Medicaid, because I think families deserve better"
    _assert_kept(quote)
    # Kept even with the escape switched off: "because I think" is a
    # subordinate reason clause, so the frame rule never fires in the first
    # place. Two independent defences, not one.
    assert screen_promise(quote, escape_on_commitment=False).keep


def test_belief_frame_followed_by_a_pledge_is_kept() -> None:
    # Here the belief DOES open the sentence, so only the commitment escape
    # saves it. This is the case a phrase stoplist gets wrong.
    _assert_kept("I think we do need some real reform in Congress, and I will fight for it.")


def test_parenthetical_i_think_inside_a_commitment_is_kept() -> None:
    # gold 272: "I think" interrupts a noun phrase, it does not frame the claim.
    _assert_kept(
        "So our number one priority I think as a representative to Congress is saying this "
        "program needs to be updated and change um and changed and uh modified in such ways "
        "that we both continue to make sure that social security is available that it's "
        "solvent"
    )


def test_belief_frame_with_a_first_person_pledge_is_kept() -> None:
    # gold 195: hedged, but the speaker is describing their own future conduct.
    _assert_kept(
        "I don't think that I'm going down to Washington, D.C. to be a rubber stamp for "
        "what President Trump wants."
    )


def test_bare_support_statement_is_kept() -> None:
    # gold 167 / 231: a stance with no belief frame is a commitment.
    _assert_kept("I support clean energy deployment where it makes sense.")
    _assert_kept(
        "I certainly don't support sending billions overseas where we have people in this "
        "country that are struggling right now."
    )


def test_first_person_plural_need_is_kept() -> None:
    # gold 204 / 241: "we need to X" puts the speaker among the actors.
    _assert_kept("We need to restore the Affordable Care Act tax credits that Trump took away.")
    _assert_kept(
        "We need to strengthen Social Security, Medicare and Medicaid so they are around for "
        "generations of Americans to come."
    )


def test_third_person_campaign_copy_is_kept() -> None:
    # gold 159 / 253 / 248: the campaign-site voice the prompt blesses.
    _assert_kept("Paul will fight to finish the wall, end illegal immigration, and restore "
                 "law and order.")
    _assert_kept("Chellie favors common sense gun safety measures to keep weapons of war out "
                 "of the hands of dangerous people.")
    _assert_kept("Chellie is opposed to opening our coastal waters to offshore oil drilling.")


def test_well_being_is_not_read_as_we_will() -> None:
    # "well-being" must not satisfy the commitment escape, or every normative
    # claim containing it walks straight through the gate.
    assert not has_commitment("private profits over people's financial well-being")
    assert has_commitment("we'll fight for people's financial well-being")


# -- pattern 2: normative desiderata ------------------------------------------

def test_impersonal_should_claim_is_dropped() -> None:
    # gold 184
    assert _drop_reason(
        "Safe access to reproductive health care should be a constitutional right."
    ) == "normative_claim"


def test_impersonal_is_critical_claim_is_dropped() -> None:
    # gold 244
    assert _drop_reason(
        "Increasing access to treatment is critical to reaching people with substance use "
        "disorder, many of whom don't have the means to afford private programs."
    ) == "normative_claim"


def test_past_time_claim_is_dropped() -> None:
    # gold 178
    assert _drop_reason(
        "It's past time to move towards a Medicare-for-all system that puts people's health "
        "and financial well-being over private profits for health insurance companies and "
        "executives."
    ) == "normative_claim"


# -- pattern 3: aspirational hopes --------------------------------------------

def test_hoping_to_is_dropped() -> None:
    # gold 202
    assert _drop_reason("we're hoping to get that launched early in '26.") == "aspirational_hope"


def test_looking_at_exploration_is_dropped() -> None:
    # gold 201: note the embedded "which will provide" is not a self-commitment
    assert _drop_reason(
        "We're looking at putting together a program that we call the MITT program, MITT, "
        "which is a master illegal trafficking tracker, which will provide information for "
        "ICE and federal authorities when they come to the state of Maine."
    ) == "aspirational_hope"


# -- pattern 4: past actions and counterfactuals -------------------------------

def test_past_counterfactual_is_dropped() -> None:
    # gold 227
    assert _drop_reason("I would have actually renewed them as a bridge.") == "past_action"


def test_i_did_that_is_dropped() -> None:
    # gold 262, opening a broadcast caption
    assert _drop_reason(
        "I DID THAT BECAUSE I DIDN'T FEEL THAT MEMBERS OF CONGRESS, IF THEY ARE NOT ABLE TO "
        "RESOLVE THE SITUATION, SHOULD RECEIVE OUR PAY."
    ) == "past_action"


# -- pattern 5: campaign bio and candidacy motivation --------------------------

def test_running_to_motivation_is_dropped() -> None:
    # gold 157
    assert _drop_reason(
        "Matt is running to take on the powerful interests that have rigged our economy "
        "against working class Mainers."
    ) == "candidacy_motivation"


def test_want_to_serve_motivation_is_dropped() -> None:
    # gold 210
    assert _drop_reason(
        "I want to serve in Congress to bend the arc of our democracy back to the "
        "aspirational goal of building a better future for our families."
    ) == "candidacy_motivation"


def test_resume_line_is_dropped() -> None:
    # gold 154
    assert _drop_reason(
        "I am an Airborne Ranger Green Beret, who is looking to continue that service as "
        "your representative to the United States Congress."
    ) == "campaign_bio"


def test_i_am_the_only_candidate_with_a_pledge_is_kept() -> None:
    # gold 207: definite article plus a pledge, not a resume line.
    _assert_kept(
        "I am the only candidate in this race who can say that I have always voted "
        "pro-choice and to protect a woman's right to choose, and I always will."
    )


# -- pattern 6: constituent casework ------------------------------------------

def test_casework_offer_is_dropped_despite_we_will() -> None:
    # gold 264: contains "WE WILL DO OUR BEST", which is why casework is a
    # structural rule the commitment escape does not reach.
    assert _drop_reason(
        "IF ANY OF YOU HAVE CONCERNS, CALL OUR OFFICE AND WE WILL DO OUR BEST TO LOOK AT "
        "PEOPLE TO GIVE HIM THE DUE PROCESS."
    ) == "constituent_casework"


# -- pattern 7: ongoing activity ----------------------------------------------

def test_is_leading_the_fight_is_dropped() -> None:
    # gold 249
    assert _drop_reason(
        "Chellie is leading the fight in Congress to face this crisis head-on with bold, "
        "decisive action."
    ) == "ongoing_activity"


def test_ongoing_negotiation_status_is_dropped() -> None:
    # gold 275
    assert _drop_reason(
        "Now, these cuts could be rolled back by Congress. And so, part of our negotiation "
        "is uh dealing with the subsidies under the Affordable Care Act, which impact people "
        "until they qualify for Medicare."
    ) == "ongoing_activity"


# -- pattern 8: third-party speech and media overlays --------------------------

def test_broadcast_caption_about_a_candidate_is_dropped() -> None:
    # gold 252: contains "HE'LL SUPPORT", so this too must outrank the escape.
    assert _drop_reason(
        "RUSSELL SAYS HE'LL SUPPORT\nTHE POLICIES OF PRESIDENT\nTRUMP TO TACKLE THE\n"
        "AFFORDABILITY CRISIS"
    ) == "reported_speech"


def test_candidates_own_caption_is_kept() -> None:
    # gold 189: same all-caps broadcast style, but it is the campaign's own ad.
    _assert_kept("HE WILL STANDUP TO\nDONALD TRUMP... AND FIGHT\nFOR EQUAL PAY... "
                 "AFFORDABLE CHILDCARE... AND HEALTHCARE.")


# -- pattern 9: fragments ------------------------------------------------------

def test_thin_scrap_is_dropped() -> None:
    # gold 268
    assert _drop_reason("we'll we'll fight back") == "fragment"


def test_quote_ending_mid_word_is_dropped() -> None:
    # gold 221: "...gaming the m"
    assert _drop_reason("I don't think they need to be gaming the m") == "fragment"


def test_prose_cut_off_before_its_sentence_ended_is_dropped() -> None:
    # gold 250: a real promise truncated mid-word, which is why the fragment
    # rule outranks the "Chellie favors" commitment escape.
    assert _drop_reason(
        "That's why Chellie favors common sense gun safety measures to keep weapons of war "
        "out of the hands of dangerous people. That includes a ban on bumpstocks, which "
        "essentially transform semi-autom"
    ) == "fragment"


def test_unpunctuated_transcript_promise_is_not_a_fragment() -> None:
    # Auto-captions have no terminal punctuation; that alone is not truncation.
    _assert_kept(
        "one of the things I want to do when I'm a congressman is get the federal government "
        "to help fight the budworm because the current administration won't let us spray"
    )


def test_multi_sentence_promise_ending_cleanly_is_kept() -> None:
    # gold 246: internal sentence ends are fine when the quote closes properly.
    _assert_kept(
        "We need an economy that works for everyone. Where anyone who works hard at a full "
        "time job is paid a livable wage. We need a tax code that asks the rich to pay their "
        "share."
    )


def test_empty_quote_is_dropped() -> None:
    assert _drop_reason("   ") == "fragment"


# -- mechanics -----------------------------------------------------------------

def test_disabled_rule_is_skipped() -> None:
    quote = "I think that's exactly the way it should be."
    assert not screen_promise(quote).keep
    assert screen_promise(quote, disabled_rules=["hedged_opinion"]).keep


def test_commitment_escape_can_be_turned_off_for_measurement() -> None:
    quote = "I think we do need some real reform in Congress, and I will fight for it."
    assert screen_promise(quote).keep
    assert not screen_promise(quote, escape_on_commitment=False).keep


def test_every_declared_rule_is_reachable() -> None:
    """RULE_NAMES is what the harness ablates; a name with no check behind it
    would silently score as a rule that catches nothing."""
    assert len(set(RULE_NAMES)) == len(RULE_NAMES)
    for rule in RULE_NAMES:
        assert isinstance(rule, str) and rule


def test_gate_is_versioned() -> None:
    assert GATE_VERSION.startswith("gate_")
