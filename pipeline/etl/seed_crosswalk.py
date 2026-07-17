"""Seed id_crosswalk from unitedstates/congress-legislators.

Downloads legislators-current.yaml — the "ID Rosetta Stone" mapping
bioguide <-> FEC candidate ids <-> govtrack <-> ICPSR <-> OpenSecrets — and
upserts one id_crosswalk row per current member of Congress.

Run:
    uv run python -m pipeline.etl.seed_crosswalk

Idempotent: rows upsert on bioguide_id; re-downloading identical bytes
dedupes against sources on (source_type, content_hash).
"""

import hashlib
import logging
from typing import Any

import httpx
import yaml

from pipeline import db
from pipeline.config import get_settings

logger = logging.getLogger(__name__)

LEGISLATORS_URL = (
    "https://raw.githubusercontent.com/unitedstates/congress-legislators"
    "/main/legislators-current.yaml"
)
SOURCE_TYPE = "congress_legislators_current"


def full_name_of(legislator: dict[str, Any]) -> str:
    name: dict[str, Any] = legislator.get("name", {})
    official = name.get("official_full")
    if official:
        return str(official)
    return f"{name.get('first', '')} {name.get('last', '')}".strip()


def seed(conn: db.Connection, raw: bytes, source_id: int) -> dict[str, int]:
    """Upsert one crosswalk row per legislator; returns count stats."""
    legislators: list[dict[str, Any]] = yaml.safe_load(raw)
    upserted = 0
    skipped = 0
    for legislator in legislators:
        ids: dict[str, Any] = legislator.get("id", {})
        bioguide = ids.get("bioguide")
        if not bioguide:
            # Everyone in this file should have a bioguide; count, don't crash.
            skipped += 1
            continue
        db.upsert_crosswalk(
            conn,
            bioguide_id=str(bioguide),
            full_name=full_name_of(legislator),
            fec_candidate_ids=[str(f) for f in ids.get("fec", [])],
            govtrack_id=ids.get("govtrack"),
            icpsr_id=ids.get("icpsr"),
            opensecrets_id=ids.get("opensecrets"),
            source_id=source_id,
        )
        upserted += 1
    return {"upserted": upserted, "skipped": skipped}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    settings = get_settings()

    logger.info("downloading %s", LEGISLATORS_URL)
    response = httpx.get(LEGISLATORS_URL, timeout=60, follow_redirects=True)
    response.raise_for_status()
    raw = response.content
    digest = hashlib.sha256(raw).hexdigest()

    # Keep a local copy for inspection; the DB row is the provenance record.
    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    local_copy = settings.raw_data_dir / "legislators-current.yaml"
    local_copy.write_bytes(raw)

    with db.connect() as conn:
        run_id = db.start_run(conn, "seed_crosswalk")
        conn.commit()  # keep the run row even if the load below fails
        try:
            source_id = db.insert_source(
                conn,
                source_type=SOURCE_TYPE,
                url=LEGISLATORS_URL,
                content_hash=digest,
                raw_payload=raw,
                raw_path=str(local_copy),
            )
            stats = seed(conn, raw, source_id)
            db.finish_run(conn, run_id, "succeeded", stats)
        except Exception as exc:
            conn.rollback()
            db.finish_run(conn, run_id, "failed", {}, error=str(exc))
            conn.commit()
            raise
    logger.info("id_crosswalk: %(upserted)d upserted, %(skipped)d skipped", stats)


if __name__ == "__main__":
    main()
