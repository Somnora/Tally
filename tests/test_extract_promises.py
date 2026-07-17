"""Extraction stage tests: chunking + the full agent->gate->store loop.

pydantic-ai's TestModel plays the LLM, so these run offline. What's under
test is OUR machinery: chunk offsets, the verify_quote gate deciding what
gets stored, rejection counting, and extraction idempotency.
"""

from typing import Any

from pydantic_ai.models.test import TestModel

from pipeline import db
from pipeline.stages.extract_promises import (
    CHUNK_CHARS,
    OVERLAP_CHARS,
    build_agent,
    chunk_document,
    extract_promises,
)
from tests.test_finance_schema import _seed_candidate

DOCUMENT_TEXT = (
    "thank you all for coming out tonight i will vote to cap insulin prices "
    "at thirty five dollars for every mainer and i will never cut social "
    "security benefits we also talked about the weather"
)


def _make_document(conn: db.Connection, politician_id: int, source_id: int) -> int:
    return db.insert_document(
        conn, politician_id=politician_id, source_id=source_id,
        doc_type="youtube_transcript", title="Town hall", url="https://example.test/v",
        published_at=None, full_text=DOCUMENT_TEXT, content_hash="extract-fixture",
        transcribed_by="youtube_captions",
    )


def _agent_returning(promises: list[dict[str, Any]]) -> Any:
    return build_agent(TestModel(custom_output_args={"promises": promises}))


# -- chunking -----------------------------------------------------------------

def test_short_document_is_one_chunk() -> None:
    assert chunk_document("short text") == [(0, "short text")]


def test_long_document_chunks_overlap_and_cover_everything() -> None:
    text = "x" * (CHUNK_CHARS * 3)
    chunks = chunk_document(text)
    assert chunks[0][0] == 0
    for (prev_off, prev_chunk), (next_off, _) in zip(chunks, chunks[1:], strict=False):
        assert next_off == prev_off + CHUNK_CHARS - OVERLAP_CHARS  # overlap held
        assert prev_off + len(prev_chunk) > next_off               # no gaps
    last_offset, last_chunk = chunks[-1]
    assert last_offset + len(last_chunk) == len(text)              # full coverage


# -- the gate, end to end -------------------------------------------------------

def test_verified_promise_is_stored_with_exact_offsets(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    doc_id = _make_document(conn, politician_id, source_id)
    quote = "i will never cut social security benefits"
    start = DOCUMENT_TEXT.index(quote)
    stats = extract_promises(
        conn, politician_id, model_name="test-model",
        agent=_agent_returning([{
            "verbatim_quote": quote, "char_start": start,
            "char_end": start + len(quote),
            "topic": "Social_Security", "specificity": "measurable",
        }]),
    )
    assert stats["promises_stored"] == 1
    assert stats["quotes_exact"] == 1
    row = conn.execute(
        "SELECT verbatim_quote, char_start, char_end, quote_verified, topic, "
        "is_scoreable, prompt_version FROM promises WHERE document_id = %s", (doc_id,)
    ).fetchone()
    assert row is not None
    assert row[0] == quote
    assert DOCUMENT_TEXT[row[1]:row[2]] == quote
    assert row[3] is True
    assert row[4] == "social_security"  # normalized lowercase
    assert row[5] is True
    assert row[6] == "extract_v1"


def test_hallucinated_quote_is_rejected_never_stored(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    _make_document(conn, politician_id, source_id)
    stats = extract_promises(
        conn, politician_id, model_name="test-model",
        agent=_agent_returning([{
            "verbatim_quote": "I will abolish the federal reserve",
            "char_start": 0, "char_end": 34,
            "topic": "economy", "specificity": "measurable",
        }]),
    )
    assert stats["quotes_rejected"] == 1
    assert stats["promises_stored"] == 0
    assert db.count_rows(conn, "promises") == 0


def test_drifted_offsets_are_relocated(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    doc_id = _make_document(conn, politician_id, source_id)
    quote = "i will vote to cap insulin prices at thirty five dollars"
    stats = extract_promises(
        conn, politician_id, model_name="test-model",
        agent=_agent_returning([{
            "verbatim_quote": quote, "char_start": 3, "char_end": 3 + len(quote),
            "topic": "healthcare", "specificity": "measurable",
        }]),
    )
    assert stats["quotes_relocated"] == 1
    row = conn.execute(
        "SELECT char_start, char_end FROM promises WHERE document_id = %s", (doc_id,)
    ).fetchone()
    assert row is not None
    assert DOCUMENT_TEXT[row[0]:row[1]] == quote


def test_rhetorical_promises_stored_but_never_scoreable(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    quote = "thank you all for coming out tonight"
    _make_document(conn, politician_id, source_id)
    extract_promises(
        conn, politician_id, model_name="test-model",
        agent=_agent_returning([{
            "verbatim_quote": quote, "char_start": 0, "char_end": len(quote),
            "topic": "other", "specificity": "rhetorical",
        }]),
    )
    row = conn.execute("SELECT is_scoreable FROM promises").fetchone()
    assert row is not None
    assert row[0] is False


def test_extraction_is_idempotent_per_prompt_and_model(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    _make_document(conn, politician_id, source_id)
    quote = "i will never cut social security benefits"
    start = DOCUMENT_TEXT.index(quote)
    agent = _agent_returning([{
        "verbatim_quote": quote, "char_start": start, "char_end": start + len(quote),
        "topic": "social_security", "specificity": "measurable",
    }])
    first = extract_promises(conn, politician_id, agent=agent, model_name="test-model")
    second = extract_promises(conn, politician_id, agent=agent, model_name="test-model")
    assert first["documents_extracted"] == 1
    assert second["documents_extracted"] == 0  # already done under this prompt+model
    assert db.count_rows(conn, "promises") == 1
