"""Loader idempotency: running a loader twice must not duplicate rows."""

from pathlib import Path

from pipeline import db
from pipeline.etl.seed_crosswalk import seed as seed_crosswalk
from pipeline.etl.seed_industry_codes import decode, parse_catcodes
from pipeline.etl.seed_industry_codes import seed as seed_industry_codes

FIXTURES = Path(__file__).parent / "fixtures"

LEGISLATORS_YAML = b"""
- id:
    bioguide: T000487
    govtrack: 456897
    fec: [H2HI02581]
    opensecrets: N00051049
  name:
    first: Jill
    last: Tokuda
    official_full: Jill N. Tokuda
- id:
    bioguide: C001080
    govtrack: 412379
    icpsr: 21133
    fec: [H0CA32101]
    opensecrets: N00030600
  name:
    first: Judy
    last: Chu
    official_full: Judy Chu
"""


def _fixture_source(conn: db.Connection, source_type: str) -> int:
    return db.insert_source(
        conn,
        source_type=source_type,
        url="https://example.test/fixture",
        content_hash=f"fixture-{source_type}",
        raw_payload=b"fixture",
    )


def test_industry_codes_loader_is_idempotent(conn: db.Connection) -> None:
    raw = (FIXTURES / "crp_categories_sample.txt").read_bytes()
    rows = parse_catcodes(decode(raw))
    source_id = _fixture_source(conn, "test_catcodes")

    first = seed_industry_codes(conn, rows, source_id)
    assert first == {"upserted": 4}
    assert db.count_rows(conn, "industry_codes") == 4

    second = seed_industry_codes(conn, rows, source_id)
    assert second == {"upserted": 4}
    assert db.count_rows(conn, "industry_codes") == 4  # same rows, not 8


def test_crosswalk_loader_is_idempotent(conn: db.Connection) -> None:
    source_id = _fixture_source(conn, "test_legislators")

    first = seed_crosswalk(conn, LEGISLATORS_YAML, source_id)
    assert first == {"upserted": 2, "skipped": 0}
    assert db.count_rows(conn, "id_crosswalk") == 2

    second = seed_crosswalk(conn, LEGISLATORS_YAML, source_id)
    assert second == {"upserted": 2, "skipped": 0}
    assert db.count_rows(conn, "id_crosswalk") == 2

    found = db.lookup_crosswalk_by_fec_id(conn, "H0CA32101")
    assert found == ("C001080", "Judy Chu")


def test_source_rows_dedupe_on_content_hash(conn: db.Connection) -> None:
    first = _fixture_source(conn, "test_dedupe")
    second = _fixture_source(conn, "test_dedupe")
    assert first == second
