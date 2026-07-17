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
- id:
    bioguide: C001035
    govtrack: 300025
    lis: S252
    fec: [S6ME00159]
    opensecrets: N00000491
  name:
    first: Susan
    last: Collins
    official_full: Susan M. Collins
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
    assert first == {"upserted": 3, "skipped": 0}
    assert db.count_rows(conn, "id_crosswalk") == 3

    second = seed_crosswalk(conn, LEGISLATORS_YAML, source_id)
    assert second == {"upserted": 3, "skipped": 0}
    assert db.count_rows(conn, "id_crosswalk") == 3

    found = db.lookup_crosswalk_by_fec_id(conn, "H0CA32101")
    assert found == ("C001080", "Judy Chu")

    # Senators carry a LIS id (senate.gov roll-call key); House members don't.
    lis = conn.execute(
        "SELECT bioguide_id, lis_id FROM id_crosswalk ORDER BY bioguide_id"
    ).fetchall()
    assert dict(lis) == {"C001035": "S252", "C001080": None, "T000487": None}


def test_source_rows_dedupe_on_content_hash(conn: db.Connection) -> None:
    first = _fixture_source(conn, "test_dedupe")
    second = _fixture_source(conn, "test_dedupe")
    assert first == second
