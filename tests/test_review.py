"""Promise-review plumbing: queue selection, verdict upsert, summary."""

from pipeline import db
from tests.test_extract_promises import DOCUMENT_TEXT, _make_document
from tests.test_finance_schema import _seed_candidate


def _seed_promise(conn: db.Connection) -> int:
    politician_id, source_id = _seed_candidate(conn)
    doc_id = _make_document(conn, politician_id, source_id)
    quote = "i will never cut social security benefits"
    start = DOCUMENT_TEXT.index(quote)
    db.insert_verified_promise(
        conn, politician_id=politician_id, document_id=doc_id,
        verbatim_quote=quote, char_start=start, char_end=start + len(quote),
        topic="social_security", specificity="measurable",
        model_name="test-model", prompt_version="extract_v2",
    )
    row = conn.execute("SELECT promise_id FROM promises").fetchone()
    assert row is not None
    return int(row[0])


def test_queue_shows_unreviewed_with_context_then_empties(conn: db.Connection) -> None:
    promise_id = _seed_promise(conn)
    queue = db.promises_for_review(conn, context_chars=50)
    assert len(queue) == 1
    item = queue[0]
    assert item.promise_id == promise_id
    assert item.verbatim_quote == "i will never cut social security benefits"
    assert item.context_before.endswith("dollars for every mainer and ")
    assert item.context_after.startswith(" we also talked")
    assert item.prompt_version == "extract_v2"

    db.upsert_promise_review(
        conn, promise_id=promise_id, verdict="correct", note=None,
        prompt_version=item.prompt_version, model_name=item.model_name,
    )
    assert db.promises_for_review(conn) == []


def test_review_upsert_overwrites_and_summarizes(conn: db.Connection) -> None:
    promise_id = _seed_promise(conn)
    db.upsert_promise_review(
        conn, promise_id=promise_id, verdict="opinion", note="stance only",
        prompt_version="extract_v2", model_name="test-model",
    )
    db.upsert_promise_review(
        conn, promise_id=promise_id, verdict="correct", note=None,
        prompt_version="extract_v2", model_name="test-model",
    )
    assert db.count_rows(conn, "promise_reviews") == 1
    assert db.review_summary(conn) == [("extract_v2", "correct", 1)]
