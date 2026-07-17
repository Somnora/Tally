"""Seed industry_codes from the OpenSecrets CRP category codes ("catcodes") file.

Where to get the file: OpenSecrets bulk data requires an approved account
(educational license). Log in at https://www.opensecrets.org/open-data/bulk-data
and download the "CRP Industry Codes" file (CRP_Categories.txt). There is no
API — OpenSecrets discontinued theirs in 2025.

Run:
    uv run python -m pipeline.etl.seed_industry_codes path/to/CRP_Categories.txt

The file is tab-delimited with a free-text documentation preamble; this loader
skips to the header row, validates the expected columns, and upserts on
catcode. Idempotent. OpenSecrets is credited in docs/methodology.md and in-app.
"""

import argparse
import csv
import hashlib
import logging
from pathlib import Path

from pipeline import db

logger = logging.getLogger(__name__)

SOURCE_TYPE = "opensecrets_crp_categories"
# Manual download: we record the portal it came from; content_hash pins the bytes.
PORTAL_URL = "https://www.opensecrets.org/open-data/bulk-data"

EXPECTED_COLUMNS = {"catcode", "catname", "catorder", "industry", "sector"}


def decode(raw: bytes) -> str:
    """CRP files are usually UTF-8 but historically shipped as Latin-1."""
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return raw.decode("latin-1")


def parse_catcodes(text: str) -> list[dict[str, str]]:
    """Skip any preamble, find the header row, and return normalized dict rows.

    Column names are lowercased and 'sector long' -> 'sector_long' so callers
    can rely on stable keys regardless of the file's exact header casing.
    """
    lines = text.splitlines()
    header_index = next(
        (i for i, line in enumerate(lines) if line.lower().lstrip().startswith("catcode")),
        None,
    )
    if header_index is None:
        raise ValueError(
            "no header row starting with 'Catcode' found — is this CRP_Categories.txt?"
        )

    header_line = lines[header_index]
    delimiter = "\t" if "\t" in header_line else ","
    reader = csv.reader(lines[header_index:], delimiter=delimiter)
    header = [col.strip().lower().replace(" ", "_") for col in next(reader)]

    missing = EXPECTED_COLUMNS - set(header)
    if missing:
        raise ValueError(f"missing expected columns: {sorted(missing)} (found {header})")

    rows: list[dict[str, str]] = []
    for values in reader:
        if not values or not values[0].strip():
            continue
        row = dict(zip(header, (v.strip() for v in values), strict=False))
        rows.append(row)
    return rows


def seed(conn: db.Connection, rows: list[dict[str, str]], source_id: int) -> dict[str, int]:
    upserted = 0
    for row in rows:
        db.upsert_industry_code(
            conn,
            catcode=row["catcode"],
            catname=row["catname"],
            catorder=row.get("catorder") or None,
            industry=row.get("industry") or None,
            sector=row.get("sector") or None,
            sector_long=row.get("sector_long") or None,
            source_id=source_id,
        )
        upserted += 1
    return {"upserted": upserted}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "path", type=Path, help="local CRP_Categories.txt downloaded from OpenSecrets"
    )
    args = parser.parse_args()

    path: Path = args.path
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    rows = parse_catcodes(decode(raw))

    with db.connect() as conn:
        run_id = db.start_run(conn, "seed_industry_codes")
        conn.commit()  # keep the run row even if the load below fails
        try:
            source_id = db.insert_source(
                conn,
                source_type=SOURCE_TYPE,
                url=PORTAL_URL,
                content_hash=digest,
                raw_payload=raw,
                raw_path=str(path.resolve()),
            )
            stats = seed(conn, rows, source_id)
            db.finish_run(conn, run_id, "succeeded", stats)
        except Exception as exc:
            conn.rollback()
            db.finish_run(conn, run_id, "failed", {}, error=str(exc))
            conn.commit()
            raise
    logger.info("industry_codes: %(upserted)d upserted", stats)


if __name__ == "__main__":
    main()
