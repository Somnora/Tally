"""Snapshot export: what reaches the public file, and what must never.

The snapshot IS the public product. Nothing else about the database is
reachable from a static client, so a row that lands here is published and a
row that does not is invisible. These tests are written from that angle:
each one asserts that something which must not ship, does not.
"""

import json
import sqlite3
from pathlib import Path

import pytest

from export.build_snapshot import build
from pipeline import db
from tests.test_evaluate_promises import (
    _run,
    _seed,
    _sign_off,
    _substantive_vote_id,
)

# Columns that would mean the export leaked donor identities, whole source
# documents, or raw API payloads into a file anyone can download.
FORBIDDEN_COLUMNS = frozenset({
    "full_text", "raw_payload", "contributor_name", "employer", "occupation",
    "donor_city", "donor_zip", "memo_text", "content_hash",
})


def _snapshot(tmp_path: Path, conn: db.Connection, **kwargs: object) -> tuple[Path, dict]:
    out = tmp_path / "tally.sqlite"
    manifest = build(out, cycle=2026, conn=conn, **kwargs)  # type: ignore[arg-type]
    return out, manifest


def _tables(path: Path) -> dict[str, list[str]]:
    con = sqlite3.connect(path)
    try:
        names = [r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        return {n: [c[1] for c in con.execute(f"PRAGMA table_info({n})")] for n in names}
    finally:
        con.close()


def _rows(path: Path, sql: str) -> list[tuple]:
    con = sqlite3.connect(path)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


# -- what must never ship -------------------------------------------------------

def test_snapshot_carries_no_donor_identities_or_source_text(
    conn: db.Connection, tmp_path: Path
) -> None:
    _, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    out, _ = _snapshot(tmp_path, conn)
    leaked = {
        (table, column)
        for table, columns in _tables(out).items()
        for column in columns
        if column in FORBIDDEN_COLUMNS
    }
    assert not leaked, f"snapshot would publish {leaked}"


def test_promise_rejected_by_a_reviewer_never_reaches_the_snapshot(
    conn: db.Connection, tmp_path: Path
) -> None:
    politician_id, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    out, manifest = _snapshot(tmp_path, conn)
    assert manifest["row_counts"]["promises"] == 1

    db.upsert_promise_review(
        conn, promise_id=promise_id, verdict="opinion", note=None,
        prompt_version="extract_v2", model_name="test-model",
    )
    out, manifest = _snapshot(tmp_path, conn)
    assert manifest["row_counts"]["promises"] == 0, (
        "a reviewer said this was not a promise; publishing it anyway would be "
        "an accurate quote presented as a false claim about what was pledged"
    )


def test_promise_dropped_by_the_gate_never_reaches_the_snapshot(
    conn: db.Connection, tmp_path: Path
) -> None:
    _, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=False,
                        reason="hedged_opinion", gate_version="gate_v1")
    _, manifest = _snapshot(tmp_path, conn)
    assert manifest["row_counts"]["promises"] == 0


def test_build_refuses_while_any_promise_is_unscreened(
    conn: db.Connection, tmp_path: Path
) -> None:
    _, promise_id = _seed(conn)
    # State the precondition rather than inheriting it. _seed now screens its
    # promise, because evaluation ignores anything the gate has not judged, so
    # "unscreened" has to be created on purpose here or this test silently
    # stops testing the refusal it exists for.
    conn.execute(
        "UPDATE promises SET gate_keep = NULL, gate_reason = NULL, "
        "gate_version = NULL WHERE promise_id = %s", (promise_id,)
    )
    with pytest.raises(RuntimeError, match="never been screened"):
        _snapshot(tmp_path, conn)


def test_build_refuses_to_publish_an_oversized_snapshot(
    conn: db.Connection, tmp_path: Path
) -> None:
    _, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    out = tmp_path / "tally.sqlite"
    with pytest.raises(RuntimeError, match="ceiling"):
        build(out, cycle=2026, conn=conn, max_bytes=1)
    assert not out.exists(), "an oversized snapshot must not be left on disk"


def test_unvalidated_citation_keeps_its_evaluation_out(
    conn: db.Connection, tmp_path: Path
) -> None:
    politician_id, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    _run(conn, politician_id, {
        "status": "broken", "consistency_score": 15,
        "llm_reasoning": "Cites a vote that does not exist.",
        "evidence": [{"kind": "vote", "id": 987654321, "position": "nay",
                      "bill_effect": "advances"}],
    })
    _, manifest = _snapshot(tmp_path, conn)
    assert manifest["row_counts"]["evaluations"] == 0
    assert manifest["row_counts"]["evidence"] == 0


# -- what must ship, intact -----------------------------------------------------

def test_valid_evaluation_ships_with_its_receipts(
    conn: db.Connection, tmp_path: Path
) -> None:
    politician_id, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    vote_id = _substantive_vote_id(conn, politician_id)
    _run(conn, politician_id, {
        "status": "broken", "consistency_score": 20,
        "llm_reasoning": "Voted against HR 1181 on passage.",
        "evidence": [{"kind": "vote", "id": vote_id, "position": "nay",
                      "bill_effect": "advances"}],
    })
    # Broken verdicts need a human signature before they publish; this test is
    # about the snapshot carrying receipts, so it signs off explicitly.
    _sign_off(conn, promise_id)
    out, manifest = _snapshot(tmp_path, conn)
    assert manifest["row_counts"]["evaluations"] == 1
    assert manifest["row_counts"]["evidence"] == 1

    # The receipt has to survive the trip, or the reader cannot check it.
    receipts = _rows(out, "SELECT congress_gov_url FROM evidence")
    assert receipts and str(receipts[0][0]).startswith("http")


def test_manifest_hash_matches_the_file_on_disk(
    conn: db.Connection, tmp_path: Path
) -> None:
    import hashlib
    _, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    out, manifest = _snapshot(tmp_path, conn)
    assert manifest["sha256"] == hashlib.sha256(out.read_bytes()).hexdigest()
    assert manifest["size_bytes"] == out.stat().st_size
    assert manifest["format_version"] >= 1


def test_every_table_exists_even_when_empty(conn: db.Connection, tmp_path: Path) -> None:
    """A client's queries must parse against a snapshot built from a sparse
    database, so empty tables still get a real schema rather than being
    omitted."""
    _, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    out, _ = _snapshot(tmp_path, conn)
    tables = _tables(out)
    for expected in ("races", "candidates", "finance", "top_donors",
                     "promises", "evaluations", "evidence"):
        assert expected in tables, f"{expected} missing from snapshot"
        assert tables[expected], f"{expected} has no columns"


def test_manifest_is_json_serializable(conn: db.Connection, tmp_path: Path) -> None:
    """The manifest is written as JSON, so a Postgres Decimal or date leaking
    into it would break the build after the snapshot had already been written."""
    _, promise_id = _seed(conn)
    db.set_gate_verdict(conn, promise_id=promise_id, keep=True,
                        reason="kept", gate_version="gate_v1")
    _, manifest = _snapshot(tmp_path, conn)
    round_tripped = json.loads(json.dumps(manifest))
    assert round_tripped["row_counts"]["promises"] == 1
    assert isinstance(round_tripped["generated_at"], str)
