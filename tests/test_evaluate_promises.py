"""Evaluation stage: the agent -> validation -> storage loop.

pydantic-ai's TestModel plays the model, so these run with no GPU. What is
under test is our machinery: the topical vote pre-filter, what happens to a
fabricated citation, the score/status pairing, append-only supersession, and
above all whether a bad evaluation can reach app_export_evaluations. It must
not, and the last test here is the one that would catch a regression that
let it.
"""

from typing import Any
from unittest.mock import patch

import psycopg
import pytest
from pydantic_ai.models.test import TestModel

from pipeline import db
from pipeline.promise_gate import GATE_VERSION, screen_promise
from pipeline.stages import evaluate_promises as ep
from pipeline.stages.evaluate_promises import (
    PROMPT_VERSION,
    QUARANTINED_PROMPT_VERSIONS,
    EvidenceItem,
    build_agent,
    evaluate_promises,
    render_votes,
)

MODEL = "test-model"
QUOTE = "I will vote against every gun safety rollback."


def _seed(conn: db.Connection) -> tuple[int, int]:
    """A member with one gun-related bill, two votes on it, and one promise."""
    source_id = db.insert_source(
        conn, source_type="test_eval", url="https://example.test/eval",
        content_hash="eval-fixture", raw_payload=b"{}",
    )
    cur = conn.execute(
        "INSERT INTO politicians (full_name, bioguide_id, source_id) "
        "VALUES ('Test Member', 'T000999', %s) RETURNING politician_id",
        (source_id,),
    )
    row = cur.fetchone()
    assert row is not None
    politician_id = int(row[0])

    db.upsert_bill(
        conn, congress=119, bill_key="HR-1181", bill_type="hr", bill_number=1181,
        title="Protecting Privacy in Purchases Act", policy_area="Crime and Law Enforcement",
        subjects=["Firearms and explosives"], summary_text="A bill about firearms.",
        introduced_date=None, latest_action=None, latest_action_date=None,
        sponsor_bioguide=None, congress_gov_url="https://example.test/b", source_id=source_id,
    )
    # Two roll calls on one bill, voted differently: the pre-filter must
    # surface only the substantive one.
    for roll, question, position in (
        (239, "On Motion to Recommit", "yea"),
        (240, "On Passage", "nay"),
    ):
        conn.execute(
            "INSERT INTO voting_records (politician_id, congress, chamber, session, "
            "roll_call_number, bill_number, vote_question, position, congress_gov_url, "
            "source_id) VALUES (%s, 119, 'house', 1, %s, 'HR 1181', %s, %s, "
            "'https://example.test/v', %s)",
            (politician_id, roll, question, position, source_id),
        )

    document_id = db.insert_document(
        conn, politician_id=politician_id, source_id=source_id,
        doc_type="campaign_site", title="Issues", url="https://example.test/issues",
        published_at=None, full_text=QUOTE,
        content_hash="eval-doc", transcribed_by=None,
    )
    db.insert_verified_promise(
        conn, politician_id=politician_id, document_id=document_id,
        verbatim_quote=QUOTE,
        char_start=0, char_end=45, topic="guns", specificity="directional",
        model_name=MODEL, prompt_version="extract_v2",
    )
    cur = conn.execute(
        "SELECT promise_id FROM promises WHERE politician_id = %s", (politician_id,)
    )
    row = cur.fetchone()
    assert row is not None
    promise_id = int(row[0])

    # Evaluation only considers promises the selectivity gate kept, so the
    # fixture has to carry a verdict or every test here evaluates nothing.
    # Screen it with the real gate rather than forcing TRUE: that way a
    # fixture quote the gate would actually reject fails here loudly instead
    # of quietly testing a row production could never produce.
    decision = screen_promise(QUOTE)
    assert decision.keep, f"fixture quote is not gate-clean: {decision.reason}"
    db.set_gate_verdict(
        conn, promise_id=promise_id, keep=decision.keep,
        reason=decision.reason, gate_version=GATE_VERSION,
    )
    return politician_id, promise_id


def _run(conn: db.Connection, politician_id: int, output: dict[str, Any]) -> dict[str, int]:
    agent = build_agent(TestModel(custom_output_args=output))
    return evaluate_promises(conn, politician_id, MODEL, agent)


def _substantive_vote_id(conn: db.Connection, politician_id: int) -> int:
    votes = db.votes_for_promise(conn, politician_id, "guns", 25)
    assert len(votes) == 1, "two roll calls on one bill must collapse to one"
    assert votes[0].vote_question == "On Passage"
    return votes[0].vote_id


def _stored(conn: db.Connection, promise_id: int) -> tuple[str, int | None, bool]:
    cur = conn.execute(
        "SELECT status, consistency_score, is_current FROM promise_evaluations "
        "WHERE promise_id = %s ORDER BY evaluation_id DESC LIMIT 1", (promise_id,)
    )
    row = cur.fetchone()
    assert row is not None
    return str(row[0]), row[1], bool(row[2])


def _sign_off(conn: db.Connection, promise_id: int) -> None:
    """Approve every broken verdict on this promise.

    Broken verdicts do not publish without a human signature (migration 0018),
    so a test asserting one exports has to say who approved it. Tests that
    forget will fail, which is the gate working rather than a nuisance.
    """
    rows = conn.execute(
        "SELECT evaluation_id FROM promise_evaluations "
        "WHERE promise_id = %s AND status = 'broken' AND is_current", (promise_id,)
    ).fetchall()
    for (evaluation_id,) in rows:
        db.record_evaluation_review(
            conn, evaluation_id=int(evaluation_id), approved=True,
            reviewed_by="test-reviewer", review_note=None,
        )


def _exported(conn: db.Connection, promise_id: int) -> int:
    cur = conn.execute(
        "SELECT count(*) FROM app_export_evaluations WHERE promise_id = %s", (promise_id,)
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


# -- the pre-filter -------------------------------------------------------------

def test_only_the_substantive_vote_reaches_the_prompt(conn: db.Connection) -> None:
    politician_id, _ = _seed(conn)
    _substantive_vote_id(conn, politician_id)


def test_a_quarantined_prompt_version_cannot_be_run_in_production() -> None:
    """Withdrawing v3's rows is what makes this guard necessary.

    promises_for_evaluation skips a promise only while a CURRENT evaluation
    exists for this model and prompt version. Setting is_current = FALSE to
    take the bad verdicts out of the product therefore made every promise
    eligible again, so the very act of quarantining them is what would let the
    next --all run regenerate them. The refusal has to live where the agent is
    built, because that is the one place a run cannot skip.
    """
    assert QUARANTINED_PROMPT_VERSIONS, "the quarantine set must never be emptied"
    # PROMPT_VERSION has moved on to v4, so the guard is exercised against a
    # withdrawn version directly rather than by breaking the live one.
    with patch.object(ep, "PROMPT_VERSION", next(iter(QUARANTINED_PROMPT_VERSIONS))):
        with pytest.raises(RuntimeError, match="quarantined"):
            build_agent()


def test_the_live_prompt_version_is_not_quarantined() -> None:
    assert PROMPT_VERSION not in QUARANTINED_PROMPT_VERSIONS


def test_an_injected_model_still_runs_under_quarantine() -> None:
    """The guard gates production, not the machinery. Blocking injected models
    would only tempt someone to empty the set to get the suite green."""
    agent = build_agent(TestModel(custom_output_args={
        "status": "pending", "consistency_score": None,
        "llm_reasoning": "none", "evidence": [],
    }))
    assert agent is not None


def test_rendered_votes_carry_what_the_bill_does(conn: db.Connection) -> None:
    """The bug this pins cost us real published verdicts.

    render_votes showed only a bill's TITLE. Congressional titles are written
    to persuade and often name the opposite of the effect: HR-4758, the
    "Homeowner Energy Freedom Act", repeals home energy efficiency rebates. A
    member who voted nay PROTECTED those rebates, and we published that he had
    broken a promise to protect them. The summary was in the SQL and on
    VoteContext throughout; only the renderer dropped it, which is precisely
    the kind of omission no other test could see.
    """
    politician_id, _ = _seed(conn)
    rendered = render_votes(db.votes_for_promise(conn, politician_id, "guns", 25))
    assert "A bill about firearms." in rendered, "the summary must reach the model"
    assert "WHAT IT DOES" in rendered


def test_a_vote_with_no_summary_says_so(conn: db.Connection) -> None:
    """Silence would let the model treat a title-only entry as a summarised
    one and infer the effect from the title, which is the original bug."""
    politician_id, _ = _seed(conn)
    conn.execute("UPDATE bills SET summary_text = NULL WHERE bill_key = 'HR-1181'")
    rendered = render_votes(db.votes_for_promise(conn, politician_id, "guns", 25))
    assert "no summary available" in rendered


def test_rendered_position_matches_the_token_the_schema_accepts(
    conn: db.Connection,
) -> None:
    """The model is told to copy the position it sees. If the list renders
    NAY while EvidenceItem.position only accepts "nay", every citation costs
    a validation retry, so the rendered token has to be the accepted one."""
    politician_id, _ = _seed(conn)
    rendered = render_votes(db.votes_for_promise(conn, politician_id, "guns", 25))
    assert "voted nay" in rendered
    assert "voted NAY" not in rendered
    accepted = EvidenceItem.model_fields["position"].annotation
    assert "nay" in getattr(accepted, "__args__", ())


def test_unmatched_topic_yields_no_votes(conn: db.Connection) -> None:
    politician_id, _ = _seed(conn)
    assert db.votes_for_promise(conn, politician_id, "housing", 25) == []
    assert db.votes_for_promise(conn, politician_id, "other", 25) == []


def test_alias_topic_resolves_to_the_filter_it_points_at(conn: db.Connection) -> None:
    """An alias row stores no filter of its own, so if the resolving join is
    ever dropped it degrades silently: the topic still exists, matches nothing,
    and every promise under it records 'pending' as though the member had
    simply never voted on it. Pin the two to the same result."""
    politician_id, _ = _seed(conn)
    # gun_violence -> guns is seeded by migration 0016, so this exercises the
    # shipped mapping rather than one the test invented.
    canonical = db.votes_for_promise(conn, politician_id, "guns", 25)
    alias = db.votes_for_promise(conn, politician_id, "gun_violence", 25)
    assert alias == canonical
    assert alias, "the fixture must yield votes, or this proves nothing"


def test_alias_row_cannot_also_carry_a_filter(conn: db.Connection) -> None:
    """Half an alias is the drift the pointer exists to prevent."""
    # Specifically the CHECK, not any error: a blind Exception here would also
    # pass on a typo in the statement and prove nothing.
    with pytest.raises(psycopg.errors.CheckViolation):
        conn.execute(
            "INSERT INTO topic_vote_filters (topic, policy_areas, canonical_topic) "
            "VALUES ('bogus', ARRAY['Health'], 'guns')"
        )


# -- the happy path -------------------------------------------------------------

def test_valid_citation_is_stored_current_and_exportable(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    stats = _run(conn, politician_id, {
        "status": "broken", "consistency_score": 20,
        "llm_reasoning": "Voted against HR 1181 on passage.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "advances"}],
    })
    assert stats["evaluations_validated"] == 1
    assert _stored(conn, promise_id) == ("broken", 20, True)
    _sign_off(conn, promise_id)
    assert _exported(conn, promise_id) == 1


# -- attacks that must never reach the public snapshot --------------------------

def test_invented_vote_id_never_exports(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    stats = _run(conn, politician_id, {
        "status": "broken", "consistency_score": 15,
        "llm_reasoning": "Voted against a bill that does not exist.",
        "evidence": [{"kind": "vote", "id": 987654321, "position": "nay",
                      "bill_effect": "advances"}],
    })
    assert stats["citation_unknown_record"] == 1
    assert stats["evaluations_failed_validation"] == 1
    _, _, is_current = _stored(conn, promise_id)
    assert not is_current, "a fabricated citation must not produce a live evaluation"
    assert _exported(conn, promise_id) == 0

    # The fabrication itself is kept. It cannot live in evaluation_evidence
    # (no row for the foreign key), and losing it to a log line would hide
    # the clearest signal that a prompt or model is unfit.
    cur = conn.execute(
        "SELECT cited_record_id, reason FROM evaluation_citation_rejects"
    )
    assert cur.fetchall() == [(987654321, "unknown_record")]


def test_real_vote_that_was_never_offered_is_refused(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    # Roll call 239 is a genuine vote by this member, but the pre-filter
    # offers only the substantive one, so the model was never shown it.
    cur = conn.execute(
        "SELECT vote_id FROM voting_records WHERE politician_id = %s "
        "AND roll_call_number = 239", (politician_id,)
    )
    row = cur.fetchone()
    assert row is not None
    unoffered = int(row[0])

    stats = _run(conn, politician_id, {
        "status": "broken", "consistency_score": 25,
        "llm_reasoning": "Cited a vote that exists but was not supplied.",
        "evidence": [{"kind": "vote", "id": unoffered, "position": "yea",
                      "bill_effect": "advances"}],
    })
    assert stats["citation_not_offered"] == 1
    assert _exported(conn, promise_id) == 0, (
        "a vote outside the supplied list is inadmissible even though it is real"
    )


def test_misread_position_is_refused(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    # The record says nay. A model claiming yea has misread the row, so its
    # direction cannot be trusted even if it happens to land correctly.
    stats = _run(conn, politician_id, {
        "status": "completed", "consistency_score": 90,
        "llm_reasoning": "Claimed a yea on a vote the record shows as nay.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "yea",
                      "bill_effect": "reverses"}],
    })
    assert stats["citation_position_mismatch"] == 1
    assert _exported(conn, promise_id) == 0


def test_verdict_with_no_citations_never_exports(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    _run(conn, politician_id, {
        "status": "broken", "consistency_score": 10,
        "llm_reasoning": "Asserted without citing anything.", "evidence": [],
    })
    assert _exported(conn, promise_id) == 0


def test_score_is_dropped_when_the_record_settled_nothing(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    _run(conn, politician_id, {
        "status": "unverifiable", "consistency_score": 50,
        "llm_reasoning": "The listed votes do not address this promise.",
        "evidence": [],
    })
    status, score, _ = _stored(conn, promise_id)
    assert status == "unverifiable"
    assert score is None, "50 reads as 'mixed evidence', which is not 'no evidence'"


def test_no_related_votes_records_pending_without_evidence(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    conn.execute("UPDATE promises SET topic = 'housing' WHERE promise_id = %s", (promise_id,))
    stats = _run(conn, politician_id, {
        "status": "broken", "consistency_score": 10,
        "llm_reasoning": "should never be used", "evidence": [],
    })
    assert stats["no_related_votes"] == 1
    status, score, _ = _stored(conn, promise_id)
    assert (status, score) == ("pending", None)
    assert _exported(conn, promise_id) == 0, "pending cites nothing, so it cannot export"


# -- append-only ----------------------------------------------------------------

def test_re_evaluation_supersedes_without_destroying_history(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    good = {
        "status": "broken", "consistency_score": 20,
        "llm_reasoning": "First evaluation.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "advances"}],
    }
    _run(conn, politician_id, good)
    # A promise already evaluated by this model and prompt is not redone.
    assert _run(conn, politician_id, good)["eligible"] == 0

    conn.execute(
        "UPDATE promise_evaluations SET is_current = FALSE WHERE promise_id = %s",
        (promise_id,),
    )
    _run(conn, politician_id, {**good, "consistency_score": 35,
                               "llm_reasoning": "Second evaluation."})
    cur = conn.execute(
        "SELECT count(*), count(*) FILTER (WHERE is_current) FROM promise_evaluations "
        "WHERE promise_id = %s", (promise_id,)
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] == 2, "the earlier evaluation is kept"
    assert row[1] == 1, "exactly one is live"


def test_promise_reviewed_as_junk_is_never_evaluated(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    db.upsert_promise_review(
        conn, promise_id=promise_id, verdict="opinion", note=None,
        prompt_version="extract_v2", model_name=MODEL,
    )
    assert db.promises_for_evaluation(
        conn, politician_id, model_name=MODEL, prompt_version=PROMPT_VERSION
    ) == []


def test_broken_needs_sign_off_before_it_can_export(conn: db.Connection) -> None:
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    _run(conn, politician_id, {
        "status": "broken", "consistency_score": 20,
        "llm_reasoning": "Voted against a bill that would have advanced it.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "advances"}],
    })
    assert _stored(conn, promise_id)[0] == "broken"
    assert _exported(conn, promise_id) == 0, "unsigned broken must not publish"
    _sign_off(conn, promise_id)
    assert _exported(conn, promise_id) == 1, "a signature releases it"


def test_a_rejected_broken_verdict_stays_withdrawn(conn: db.Connection) -> None:
    """Rejecting keeps the row and the reason, and drops it from the product
    by the same is_current mechanism that withdrew evaluate_v3 wholesale."""
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    _run(conn, politician_id, {
        "status": "broken", "consistency_score": 20,
        "llm_reasoning": "Voted against a bill that would have advanced it.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "advances"}],
    })
    row = conn.execute(
        "SELECT evaluation_id FROM promise_evaluations WHERE promise_id = %s",
        (promise_id,)
    ).fetchone()
    assert row is not None
    db.record_evaluation_review(
        conn, evaluation_id=int(row[0]), approved=False,
        reviewed_by="test-reviewer", review_note="cited vote is a repeal",
    )
    assert _exported(conn, promise_id) == 0
    kept = conn.execute(
        "SELECT review_note FROM promise_evaluations WHERE evaluation_id = %s",
        (int(row[0]),)
    ).fetchone()
    assert kept is not None and kept[0] == "cited vote is a repeal"


def test_invented_bill_number_in_prose_keeps_a_verdict_unpublished(
    conn: db.Connection,
) -> None:
    """Citations were always validated; the sentence never was, and it is what
    a reader actually reads. Ten verdicts reached the public site naming bills
    like "HR-322423" while their citations validated perfectly."""
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    _run(conn, politician_id, {
        "status": "in_progress", "consistency_score": 60,
        "llm_reasoning": "The legislator voted yea on HR-322423, which helps.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "reverses"}],
    })
    assert _stored(conn, promise_id)[2] is False, "invented bill number must not be current"
    assert _exported(conn, promise_id) == 0


def test_model_scratchpad_in_prose_keeps_a_verdict_unpublished(
    conn: db.Connection,
) -> None:
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    _run(conn, politician_id, {
        "status": "in_progress", "consistency_score": 60,
        "llm_reasoning": "Vote 1: nay. Wait, let me re-check the votes. Let's say 75.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "reverses"}],
    })
    assert _stored(conn, promise_id)[2] is False, "scratchpad must not be current"
