"""documents + media_assets repository behavior."""

from pipeline import db
from tests.test_finance_schema import _seed_candidate


def test_document_insert_dedupes_on_content(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    kwargs = dict(
        politician_id=politician_id, source_id=source_id, doc_type="campaign_site",
        title="Issues", url="https://example.test/issues", published_at=None,
        full_text="We will cap insulin prices at $35.", content_hash="doc-hash-1",
    )
    first = db.insert_document(conn, **kwargs)   # type: ignore[arg-type]
    second = db.insert_document(conn, **kwargs)  # type: ignore[arg-type]
    assert first == second
    assert db.count_rows(conn, "documents") == 1


def test_media_asset_upsert_preserves_document_link(conn: db.Connection) -> None:
    politician_id, source_id = _seed_candidate(conn)
    doc_id = db.insert_document(
        conn, politician_id=politician_id, source_id=source_id,
        doc_type="youtube_transcript", title="Town hall", url="https://youtube.test/v",
        published_at=None, full_text="transcript text goes here",
        content_hash="doc-hash-2", transcribed_by="youtube_captions",
    )
    first = db.upsert_media_asset(
        conn, politician_id=politician_id, external_id="vid123", title="Town hall",
        channel_title="News", url="https://youtube.test/v", published_at=None,
        has_captions=True, document_id=doc_id, source_id=source_id,
    )
    # Re-discovery without caption info must not erase the transcript link.
    second = db.upsert_media_asset(
        conn, politician_id=politician_id, external_id="vid123", title="Town hall",
        channel_title="News", url="https://youtube.test/v", published_at=None,
        has_captions=None, document_id=None, source_id=source_id,
    )
    assert first == second
    row = conn.execute(
        "SELECT has_captions, document_id FROM media_assets WHERE asset_id = %s", (first,)
    ).fetchone()
    assert row is not None
    assert row[0] is True
    assert row[1] == doc_id
