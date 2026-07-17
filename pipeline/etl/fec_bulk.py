"""FEC bulk load: candidate master (cn) + committee master (cm) for a cycle.

Downloads the pipe-delimited bulk files and their official header CSVs from
fec.gov, stores every downloaded artifact as a `sources` row, then:

  1. creates the structural races for the cycle (435 House districts from
     2020-census apportionment + the Senate class up for regular election
     + any known special Senate elections);
  2. upserts all committees;
  3. upserts one politician + candidacy per House/Senate candidate whose
     CAND_ELECTION_YR matches the cycle — linked through id_crosswalk where
     possible, otherwise created provisionally and flagged needs_linkage.

Run:
    uv run python -m pipeline.etl.fec_bulk            # cycle 2026
    uv run python -m pipeline.etl.fec_bulk --cycle 2028

Idempotent: committees upsert on cmte_id, politicians on bioguide/fec id,
races on their natural key, candidacies on (race_id, fec_candidate_id).
Itemized contributions are Milestone 2 — this module deliberately stops at
the masters.
"""

import argparse
import csv
import hashlib
import io
import logging
import zipfile
from typing import Any

import httpx

from pipeline import db
from pipeline.config import get_settings

logger = logging.getLogger(__name__)

BULK_BASE = "https://www.fec.gov/files/bulk-downloads"
# Polite, identifiable client per CLAUDE.md scraping etiquette.
USER_AGENT = "tally-civic-transparency/0.1 (nonpartisan transparency project; local ingestion)"

# House seats per state, 2020-census apportionment (sums to 435). FEC uses
# district '00' for at-large seats. DC and the territories elect non-voting
# delegates — out of scope for now (CLAUDE.md scopes to the 435 + Senate).
HOUSE_SEATS: dict[str, int] = {
    "AL": 7, "AK": 1, "AZ": 9, "AR": 4, "CA": 52, "CO": 8, "CT": 5, "DE": 1,
    "FL": 28, "GA": 14, "HI": 2, "ID": 2, "IL": 17, "IN": 9, "IA": 4, "KS": 4,
    "KY": 6, "LA": 6, "ME": 2, "MD": 8, "MA": 9, "MI": 13, "MN": 8, "MS": 4,
    "MO": 8, "MT": 2, "NE": 3, "NV": 4, "NH": 2, "NJ": 12, "NM": 3, "NY": 26,
    "NC": 14, "ND": 1, "OH": 15, "OK": 5, "OR": 6, "PA": 17, "RI": 2, "SC": 7,
    "SD": 1, "TN": 9, "TX": 38, "UT": 4, "VT": 1, "VA": 11, "WA": 10, "WV": 2,
    "WI": 8, "WY": 1,
}

# Senate class whose regular election falls in each cycle: 2026 -> class 2.
SENATE_CLASS_FOR_CYCLE: dict[int, int] = {2024: 1, 2026: 2, 2028: 3, 2030: 1}

# The 33 states with a class-2 seat (regular 2026 races).
SENATE_CLASS_2_STATES = frozenset({
    "AL", "AK", "AR", "CO", "DE", "GA", "ID", "IL", "IA", "KS", "KY", "LA",
    "ME", "MA", "MI", "MN", "MS", "MT", "NE", "NH", "NJ", "NM", "NC", "OK",
    "OR", "RI", "SC", "SD", "TN", "TX", "VA", "WV", "WY",
})
SENATE_STATES_FOR_CLASS: dict[int, frozenset[str]] = {2: SENATE_CLASS_2_STATES}

# Known special Senate elections: (cycle, state) -> seat class. Maintained by
# hand from fec.gov special-election notices; candidates in states not listed
# here and not in the regular class are skipped with a loud warning so a new
# special can't slip through silently.
# 2026: FL (Rubio vacancy) and OH (Vance vacancy), both class-3 seats.
SPECIAL_SENATE_RACES: dict[tuple[int, str], int] = {
    (2026, "FL"): 3,
    (2026, "OH"): 3,
}


def download(url: str) -> bytes:
    logger.info("downloading %s", url)
    response = httpx.get(
        url, timeout=120, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    response.raise_for_status()
    return response.content


def store_download(conn: db.Connection, source_type: str, url: str, raw: bytes) -> int:
    """Save a copy under data/raw/ and record the sources row."""
    settings = get_settings()
    settings.raw_data_dir.mkdir(parents=True, exist_ok=True)
    local_copy = settings.raw_data_dir / url.rsplit("/", 1)[-1]
    local_copy.write_bytes(raw)
    return db.insert_source(
        conn,
        source_type=source_type,
        url=url,
        content_hash=hashlib.sha256(raw).hexdigest(),
        raw_payload=raw,
        raw_path=str(local_copy),
    )


def parse_header(raw: bytes) -> list[str]:
    """FEC header files are one-line CSVs naming the pipe-file's columns."""
    reader = csv.reader(io.StringIO(raw.decode("utf-8-sig")))
    return [column.strip().upper() for column in next(reader)]


def parse_pipe_file(zip_bytes: bytes, header: list[str]) -> list[dict[str, str]]:
    """Extract the single .txt member of an FEC zip into dict rows.

    FEC bulk files are Latin-1, pipe-delimited, unquoted.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        member = archive.namelist()[0]
        text = archive.read(member).decode("latin-1")
    reader = csv.reader(io.StringIO(text), delimiter="|", quoting=csv.QUOTE_NONE)
    rows: list[dict[str, str]] = []
    for values in reader:
        if not values:
            continue
        rows.append(dict(zip(header, (v.strip() for v in values), strict=False)))
    return rows


def normalize_district(state: str, raw_district: str) -> str | None:
    """Map FEC's district field onto our '00'-for-at-large convention.

    Returns None for districts that don't exist under current apportionment
    (FEC keeps whatever candidates typed on their filing paperwork).
    """
    seats = HOUSE_SEATS[state]
    district = raw_district.strip()
    if seats == 1:
        # At-large: FEC data shows both '00' and '01'; normalize to '00'.
        return "00" if district in ("", "0", "00", "1", "01") else None
    try:
        number = int(district)
    except ValueError:
        return None
    return f"{number:02d}" if 1 <= number <= seats else None


def ensure_structural_races(
    conn: db.Connection, cycle: int
) -> tuple[dict[tuple[str, str], int], dict[str, int]]:
    """Create/fetch the cycle's House and regular-Senate races.

    Returns ({(state, district): race_id}, {state: race_id}).
    """
    house: dict[tuple[str, str], int] = {}
    for state, seats in HOUSE_SEATS.items():
        districts = ["00"] if seats == 1 else [f"{n:02d}" for n in range(1, seats + 1)]
        for district in districts:
            house[(state, district)] = db.upsert_race(
                conn, cycle=cycle, state=state, office="house",
                district=district, senate_class=None,
            )

    senate_class = SENATE_CLASS_FOR_CYCLE[cycle]
    senate: dict[str, int] = {
        state: db.upsert_race(
            conn, cycle=cycle, state=state, office="senate",
            district=None, senate_class=senate_class,
        )
        for state in sorted(SENATE_STATES_FOR_CLASS[senate_class])
    }
    return house, senate


def load_committees(
    conn: db.Connection, rows: list[dict[str, str]], cycle: int, source_id: int
) -> set[str]:
    """Upsert the committee master; returns the set of loaded cmte_ids."""
    params: list[dict[str, Any]] = []
    for row in rows:
        cmte_id = row.get("CMTE_ID", "")
        if not cmte_id:
            continue
        params.append({
            "cmte_id": cmte_id,
            "name": row.get("CMTE_NM") or cmte_id,  # name is NOT NULL; a handful are blank
            "cmte_type": row.get("CMTE_TP") or None,
            "cmte_designation": row.get("CMTE_DSGN") or None,
            "party": row.get("CMTE_PTY_AFFILIATION") or None,
            "connected_org": row.get("CONNECTED_ORG_NM") or None,
            "cand_id": row.get("CAND_ID") or None,
            "state": (row.get("CMTE_ST") or None) if len(row.get("CMTE_ST", "")) == 2 else None,
            "cycle": cycle,
            "source_id": source_id,
        })
    db.upsert_committees_bulk(conn, params)
    return {p["cmte_id"] for p in params}


def load_candidates(
    conn: db.Connection,
    rows: list[dict[str, str]],
    cycle: int,
    source_id: int,
    house_races: dict[tuple[str, str], int],
    senate_races: dict[str, int],
    known_committees: set[str],
) -> dict[str, int]:
    stats = {
        "candidacies": 0, "politicians_linked": 0, "politicians_provisional": 0,
        "skipped_other_office": 0, "skipped_other_year": 0, "skipped_territory": 0,
        "skipped_bad_district": 0, "skipped_unmapped_senate": 0,
        "skipped_duplicate_person": 0, "pcc_not_in_master": 0,
    }
    special_race_ids: dict[str, int] = {}
    seen_person_race: set[tuple[int, int]] = set()

    for row in rows:
        office = row.get("CAND_OFFICE", "")
        if office not in ("H", "S"):
            stats["skipped_other_office"] += 1
            continue
        if row.get("CAND_ELECTION_YR") != str(cycle):
            stats["skipped_other_year"] += 1
            continue
        state = row.get("CAND_OFFICE_ST", "")

        if office == "H":
            if state not in HOUSE_SEATS:
                stats["skipped_territory"] += 1
                continue
            district = normalize_district(state, row.get("CAND_OFFICE_DISTRICT", ""))
            if district is None:
                stats["skipped_bad_district"] += 1
                continue
            race_id = house_races[(state, district)]
        else:
            if state in senate_races:
                race_id = senate_races[state]
            elif (cycle, state) in SPECIAL_SENATE_RACES:
                if state not in special_race_ids:
                    special_race_ids[state] = db.upsert_race(
                        conn, cycle=cycle, state=state, office="senate", district=None,
                        senate_class=SPECIAL_SENATE_RACES[(cycle, state)],
                        is_special=True, source_id=source_id,
                    )
                race_id = special_race_ids[state]
            else:
                logger.warning(
                    "senate candidate in %s doesn't match class %d or a known special — "
                    "update SPECIAL_SENATE_RACES if a new special election was called",
                    state, SENATE_CLASS_FOR_CYCLE[cycle],
                )
                stats["skipped_unmapped_senate"] += 1
                continue

        fec_candidate_id = row.get("CAND_ID", "")
        party = row.get("CAND_PTY_AFFILIATION") or None
        crosswalk = db.lookup_crosswalk_by_fec_id(conn, fec_candidate_id)
        if crosswalk is not None:
            bioguide_id, full_name = crosswalk
            politician_id = db.upsert_politician_by_bioguide(
                conn, full_name=full_name, party=party, state=state,
                bioguide_id=bioguide_id, source_id=source_id,
            )
            stats["politicians_linked"] += 1
        else:
            # CAND_NAME is 'LAST, FIRST' as filed; stored verbatim rather than
            # guessed into display case (McConnell vs Mcconnell).
            politician_id = db.upsert_politician_by_fec_id(
                conn, full_name=row.get("CAND_NAME") or fec_candidate_id,
                party=party, state=state,
                fec_candidate_id=fec_candidate_id, source_id=source_id,
            )
            stats["politicians_provisional"] += 1

        if (race_id, politician_id) in seen_person_race:
            # Same person holding two FEC ids for one race (stale filings);
            # first id wins, duplicates are counted, not stored.
            stats["skipped_duplicate_person"] += 1
            continue
        seen_person_race.add((race_id, politician_id))

        principal_cmte_id = row.get("CAND_PCC") or None
        if principal_cmte_id is not None and principal_cmte_id not in known_committees:
            stats["pcc_not_in_master"] += 1
            principal_cmte_id = None

        db.upsert_candidacy(
            conn, race_id=race_id, politician_id=politician_id,
            fec_candidate_id=fec_candidate_id, party=party,
            incumbent_challenger=row.get("CAND_ICI") or None,
            cand_status=row.get("CAND_STATUS") or None,
            principal_cmte_id=principal_cmte_id, source_id=source_id,
        )
        stats["candidacies"] += 1

    return stats


def load(cycle: int) -> dict[str, int]:
    suffix = str(cycle)[-2:]
    cn_url = f"{BULK_BASE}/{cycle}/cn{suffix}.zip"
    cm_url = f"{BULK_BASE}/{cycle}/cm{suffix}.zip"
    cn_header_url = f"{BULK_BASE}/data_dictionaries/cn_header_file.csv"
    cm_header_url = f"{BULK_BASE}/data_dictionaries/cm_header_file.csv"

    cn_zip, cm_zip = download(cn_url), download(cm_url)
    cn_header_raw, cm_header_raw = download(cn_header_url), download(cm_header_url)

    with db.connect() as conn:
        run_id = db.start_run(conn, "fec_bulk_load")
        conn.commit()  # keep the run row even if the load below fails
        try:
            store_download(conn, "fec_bulk_cn_header", cn_header_url, cn_header_raw)
            store_download(conn, "fec_bulk_cm_header", cm_header_url, cm_header_raw)
            cn_source_id = store_download(conn, "fec_bulk_cn", cn_url, cn_zip)
            cm_source_id = store_download(conn, "fec_bulk_cm", cm_url, cm_zip)

            committee_rows = parse_pipe_file(cm_zip, parse_header(cm_header_raw))
            known_committees = load_committees(conn, committee_rows, cycle, cm_source_id)
            logger.info("committees upserted: %d", len(known_committees))

            house_races, senate_races = ensure_structural_races(conn, cycle)
            logger.info(
                "structural races ensured: %d house, %d senate",
                len(house_races), len(senate_races),
            )

            candidate_rows = parse_pipe_file(cn_zip, parse_header(cn_header_raw))
            stats = load_candidates(
                conn, candidate_rows, cycle, cn_source_id,
                house_races, senate_races, known_committees,
            )
            stats["committees"] = len(known_committees)
            stats["house_races"] = len(house_races)
            stats["senate_races_regular"] = len(senate_races)

            db.finish_run(conn, run_id, "succeeded", stats)
        except Exception as exc:
            conn.rollback()
            db.finish_run(conn, run_id, "failed", {}, error=str(exc))
            conn.commit()
            raise
    return stats


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", type=int, default=2026)
    args = parser.parse_args()
    stats = load(args.cycle)
    for key in sorted(stats):
        logger.info("%-28s %d", key, stats[key])


if __name__ == "__main__":
    main()
