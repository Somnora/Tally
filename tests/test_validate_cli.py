"""Revalidation: keeping invariant 2 true after the facts move.

Validating a citation at write time is a snapshot. Bill metadata arrives and
gets corrected, so a vote nobody knew was buried in an omnibus can turn out to
be one, and an evaluation that was sound in July is wrong in September without
anything about it changing. These tests cover the case that matters: a live,
exportable evaluation must stop being exportable once the record no longer
supports it, without destroying the reasoning behind it.
"""

from pipeline import db
from pipeline.validate_cli import revalidate
from tests.test_evaluate_promises import (
    MODEL,
    _exported,
    _run,
    _seed,
    _sign_off,
    _substantive_vote_id,
)


def _make_omnibus(conn: db.Connection) -> None:
    """Backfill later discovers the bill bundles far more than we knew."""
    conn.execute(
        "UPDATE bills SET subjects = %s WHERE bill_key = 'HR-1181'",
        ([f"Subject {n}" for n in range(60)],),
    )


def _validated_flags(conn: db.Connection) -> list[bool]:
    cur = conn.execute("SELECT validated FROM evaluation_evidence ORDER BY evidence_id")
    return [bool(r[0]) for r in cur.fetchall()]


def _seed_live_evaluation(conn: db.Connection) -> tuple[int, int]:
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    _run(conn, politician_id, {
        "status": "broken", "consistency_score": 20,
        "llm_reasoning": "Voted against HR 1181 on passage.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "advances"}],
    })
    # Broken verdicts need a signature to publish (migration 0018); this
    # fixture is about revalidation, so it signs off and moves on.
    _sign_off(conn, promise_id)
    assert _exported(conn, promise_id) == 1, "precondition: starts exportable"
    return politician_id, promise_id


def test_nothing_changes_when_the_facts_have_not(conn: db.Connection) -> None:
    _, promise_id = _seed_live_evaluation(conn)
    stats = revalidate(conn)
    assert stats["validated_flipped"] == 0
    assert stats["evaluations_demoted"] == 0
    assert _exported(conn, promise_id) == 1


def test_evaluation_is_demoted_once_its_bill_turns_out_to_be_omnibus(
    conn: db.Connection,
) -> None:
    _, promise_id = _seed_live_evaluation(conn)
    _make_omnibus(conn)

    stats = revalidate(conn)
    assert stats["citation_omnibus_not_contextual"] == 1
    assert stats["validated_flipped"] == 1
    assert stats["evaluations_demoted"] == 1
    assert _validated_flags(conn) == [False]
    assert _exported(conn, promise_id) == 0, (
        "a verdict resting on an omnibus vote must stop being public"
    )

    # The reasoning and score survive: only is_current moved.
    cur = conn.execute(
        "SELECT status, consistency_score, llm_reasoning, is_current "
        "FROM promise_evaluations WHERE promise_id = %s", (promise_id,)
    )
    row = cur.fetchone()
    assert row is not None
    assert (row[0], row[1]) == ("broken", 20)
    assert row[2] == "Voted against HR 1181 on passage."
    assert row[3] is False


def test_dry_run_reports_without_writing(conn: db.Connection) -> None:
    _, promise_id = _seed_live_evaluation(conn)
    _make_omnibus(conn)

    stats = revalidate(conn, apply_changes=False)
    assert stats["evaluations_demoted"] == 1, "the problem is still reported"
    assert _validated_flags(conn) == [True], "but nothing was written"
    assert _exported(conn, promise_id) == 1


def test_revalidation_never_promotes(conn: db.Connection) -> None:
    """A failed evaluation must not come back to life just because the two
    write-time checks cannot be re-run."""
    politician_id, promise_id = _seed(conn)
    vote_id = _substantive_vote_id(conn, politician_id)
    # Misread position: rejected at write time, and unrecoverable afterwards
    # because the claimed position was never stored.
    _run(conn, politician_id, {
        "status": "completed", "consistency_score": 90,
        "llm_reasoning": "Claimed a yea on a vote the record shows as nay.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "yea",
                      "bill_effect": "reverses"}],
    })
    assert _exported(conn, promise_id) == 0

    revalidate(conn)
    cur = conn.execute(
        "SELECT is_current FROM promise_evaluations WHERE promise_id = %s", (promise_id,)
    )
    row = cur.fetchone()
    assert row is not None
    assert row[0] is False, "a demoted evaluation stays demoted"
    assert _exported(conn, promise_id) == 0


def test_summary_counts_exportable_separately_from_current(conn: db.Connection) -> None:
    _seed_live_evaluation(conn)
    rows = db.evaluation_summary(conn)
    assert rows, "summary should report the stored evaluation"
    prompt_version, model_name, status, evaluations, current, exportable = rows[0]
    assert (model_name, status) == (MODEL, "broken")
    assert (evaluations, current, exportable) == (1, 1, 1)
    assert prompt_version
