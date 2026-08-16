"""Evidence validation tests.

This module is the last thing standing between a confident sentence and a
fabricated citation, so the cases below are written as attacks: invent an id,
cite someone else's vote, dress an omnibus vote up as clean support, claim a
verdict nothing backs. Every one must be caught in code, because none of them
is visible in the model's prose.
"""

from pipeline.evidence import (
    ClaimedEvidence,
    VoteFact,
    check_citation,
    check_citations,
    status_is_supported,
)

PINGREE = 101
SOMEONE_ELSE = 202


def _fact(vote_id: int, *, politician_id: int = PINGREE, omnibus: bool = False,
          procedural: bool = False) -> VoteFact:
    return VoteFact(
        vote_id=vote_id, politician_id=politician_id, position="nay",
        vote_question="On Passage", bill_key="HR-1181",
        is_omnibus=omnibus, is_procedural=procedural,
    )


def _check(claim: ClaimedEvidence, facts: dict[int, VoteFact]):
    return check_citation(claim, politician_id=PINGREE, vote_facts=facts)


# -- fabrication ---------------------------------------------------------------

def test_real_vote_is_accepted() -> None:
    result = _check(ClaimedEvidence("vote", 1, "contradicts"), {1: _fact(1)})
    assert result.accepted
    assert result.reason == "ok"


def test_invented_vote_id_is_rejected() -> None:
    # The single most dangerous failure: a plausible id that was never in the
    # prompt. Nothing in the reasoning text would reveal it.
    result = _check(ClaimedEvidence("vote", 999999, "supports"), {1: _fact(1)})
    assert not result.accepted
    assert result.reason == "unknown_record"


def test_another_members_vote_is_rejected() -> None:
    # Every vote_id is a valid vote_id, so the foreign key cannot catch this.
    facts = {5: _fact(5, politician_id=SOMEONE_ELSE)}
    result = _check(ClaimedEvidence("vote", 5, "contradicts"), facts)
    assert not result.accepted
    assert result.reason == "wrong_politician"


# -- what a record is allowed to carry -----------------------------------------

def test_omnibus_vote_may_only_be_contextual() -> None:
    facts = {7: _fact(7, omnibus=True)}
    assert not _check(ClaimedEvidence("vote", 7, "supports"), facts).accepted
    assert _check(ClaimedEvidence("vote", 7, "supports"), facts).reason == (
        "omnibus_not_contextual"
    )
    assert _check(ClaimedEvidence("vote", 7, "contextual"), facts).accepted


def test_procedural_vote_may_only_be_contextual() -> None:
    facts = {8: _fact(8, procedural=True)}
    assert not _check(ClaimedEvidence("vote", 8, "contradicts"), facts).accepted
    assert _check(ClaimedEvidence("vote", 8, "contextual"), facts).accepted


def test_donation_may_never_carry_a_verdict() -> None:
    # The editorial line: money is context for the reader, never the thing
    # that makes a promise "broken".
    assert _check(ClaimedEvidence("donation", 3, "contradicts"), {}).reason == (
        "donation_not_contextual"
    )


def test_garbage_direction_is_rejected() -> None:
    assert _check(ClaimedEvidence("vote", 1, "proves"), {1: _fact(1)}).reason == (
        "bad_direction"
    )


def test_duplicate_citations_are_counted_once() -> None:
    claims = [ClaimedEvidence("vote", 1, "supports")] * 3
    checks = check_citations(claims, politician_id=PINGREE, vote_facts={1: _fact(1)})
    assert len(checks) == 1, "repeating one vote is not three pieces of evidence"


# -- the verdict must match what survived --------------------------------------

def _accepted(direction: str) -> list:
    return check_citations(
        [ClaimedEvidence("vote", 1, direction)],
        politician_id=PINGREE, vote_facts={1: _fact(1)},
    )


def test_broken_needs_something_contradictory() -> None:
    ok, reason = status_is_supported("broken", _accepted("contradicts"))
    assert ok and reason == "ok"

    ok, reason = status_is_supported("broken", _accepted("supports"))
    assert not ok, "a broken verdict citing only support is an assertion"
    assert reason == "no_contradicts_evidence"


def test_completed_needs_support() -> None:
    assert status_is_supported("completed", _accepted("supports"))[0]
    assert status_is_supported("completed", _accepted("contextual"))[1] == (
        "no_supports_evidence"
    )


def test_verdict_resting_only_on_rejected_citations_fails() -> None:
    checks = check_citations(
        [ClaimedEvidence("vote", 999, "contradicts")],
        politician_id=PINGREE, vote_facts={},
    )
    ok, reason = status_is_supported("broken", checks)
    assert not ok
    assert reason == "no_valid_evidence"


def test_unverifiable_must_cite_nothing() -> None:
    assert status_is_supported("unverifiable", [])[0]
    ok, reason = status_is_supported("unverifiable", _accepted("supports"))
    assert not ok, "citing evidence contradicts the claim that none settles it"
    assert reason == "unevidenced_status_with_citations"


def test_pending_must_cite_nothing() -> None:
    assert status_is_supported("pending", [])[0]
    assert not status_is_supported("pending", _accepted("contextual"))[0]


def test_unknown_status_is_refused() -> None:
    assert status_is_supported("mostly_kept", _accepted("supports"))[1] == "unknown_status"
