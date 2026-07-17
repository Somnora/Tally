"""Thin repository module: the only place Python talks to Postgres.

Rules enforced here:
  * SQL lives in db/sql/*.sql files, loaded by name — never f-strings.
  * Every write is an idempotent upsert keyed on a natural id.
  * Callers manage transactions: `with db.connect() as conn:` commits on
    success and rolls back on exception (psycopg connection semantics).
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
from psycopg import sql as pgsql
from psycopg.types.json import Jsonb

from pipeline.config import get_settings

SQL_DIR = Path(__file__).resolve().parent.parent / "db" / "sql"

Connection = psycopg.Connection[tuple[Any, ...]]


def connect(database_url: str | None = None) -> Connection:
    """Open a connection to the app database (or an explicit URL, for tests)."""
    return psycopg.connect(database_url or get_settings().database_url)


@cache
def load_sql(name: str) -> LiteralString:
    """Load db/sql/<name>.sql (cached — files are immutable at runtime).

    psycopg types queries as LiteralString to discourage building SQL from
    user input. These files are static, version-controlled assets — the one
    cast below is the trusted boundary that makes that guarantee.
    """
    return cast(LiteralString, (SQL_DIR / f"{name}.sql").read_text(encoding="utf-8"))


def _returned_id(cur: psycopg.Cursor[tuple[Any, ...]]) -> int:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("query was expected to RETURNING an id but returned no row")
    return int(row[0])


# -- sources (provenance) ----------------------------------------------------

def insert_source(
    conn: Connection,
    *,
    source_type: str,
    url: str,
    content_hash: str,
    raw_payload: bytes | None = None,
    raw_path: str | None = None,
) -> int:
    """Record a retrieved payload; returns the existing row's id on re-download."""
    params: dict[str, Any] = {
        "source_type": source_type,
        "url": url,
        "content_hash": content_hash,
        "raw_payload": raw_payload,
        "raw_path": raw_path,
    }
    cur = conn.execute(load_sql("source_insert"), params)
    row = cur.fetchone()
    if row is not None:
        return int(row[0])
    return _returned_id(conn.execute(load_sql("source_get"), params))


# -- id_crosswalk ------------------------------------------------------------

def upsert_crosswalk(
    conn: Connection,
    *,
    bioguide_id: str,
    full_name: str,
    fec_candidate_ids: list[str],
    govtrack_id: int | None,
    icpsr_id: int | None,
    opensecrets_id: str | None,
    lis_id: str | None,
    source_id: int,
) -> None:
    conn.execute(
        load_sql("crosswalk_upsert"),
        {
            "bioguide_id": bioguide_id,
            "full_name": full_name,
            "fec_candidate_ids": fec_candidate_ids,
            "govtrack_id": govtrack_id,
            "icpsr_id": icpsr_id,
            "opensecrets_id": opensecrets_id,
            "lis_id": lis_id,
            "source_id": source_id,
        },
    )


def lookup_crosswalk_by_fec_id(conn: Connection, fec_candidate_id: str) -> tuple[str, str] | None:
    """Return (bioguide_id, full_name) for a known FEC candidate id, else None."""
    cur = conn.execute(load_sql("crosswalk_lookup_fec_id"), {"fec_candidate_id": fec_candidate_id})
    row = cur.fetchone()
    return None if row is None else (str(row[0]), str(row[1]))


# -- industry_codes ----------------------------------------------------------

def upsert_industry_code(
    conn: Connection,
    *,
    catcode: str,
    catname: str,
    catorder: str | None,
    industry: str | None,
    sector: str | None,
    sector_long: str | None,
    source_id: int,
) -> None:
    conn.execute(
        load_sql("industry_code_upsert"),
        {
            "catcode": catcode,
            "catname": catname,
            "catorder": catorder,
            "industry": industry,
            "sector": sector,
            "sector_long": sector_long,
            "source_id": source_id,
        },
    )


# -- politicians -------------------------------------------------------------

def upsert_politician_by_bioguide(
    conn: Connection,
    *,
    full_name: str,
    party: str | None,
    state: str | None,
    bioguide_id: str,
    source_id: int,
) -> int:
    return _returned_id(
        conn.execute(
            load_sql("politician_upsert_bioguide"),
            {
                "full_name": full_name,
                "party": party,
                "state": state,
                "bioguide_id": bioguide_id,
                "source_id": source_id,
            },
        )
    )


def upsert_politician_by_fec_id(
    conn: Connection,
    *,
    full_name: str,
    party: str | None,
    state: str | None,
    fec_candidate_id: str,
    source_id: int,
) -> int:
    return _returned_id(
        conn.execute(
            load_sql("politician_upsert_fec"),
            {
                "full_name": full_name,
                "party": party,
                "state": state,
                "fec_candidate_id": fec_candidate_id,
                "source_id": source_id,
            },
        )
    )


# -- committees --------------------------------------------------------------

def upsert_committee(
    conn: Connection,
    *,
    cmte_id: str,
    name: str,
    cmte_type: str | None,
    cmte_designation: str | None,
    party: str | None,
    connected_org: str | None,
    cand_id: str | None,
    state: str | None,
    cycle: int,
    source_id: int,
) -> None:
    conn.execute(
        load_sql("committee_upsert"),
        {
            "cmte_id": cmte_id,
            "name": name,
            "cmte_type": cmte_type,
            "cmte_designation": cmte_designation,
            "party": party,
            "connected_org": connected_org,
            "cand_id": cand_id,
            "state": state,
            "cycle": cycle,
            "source_id": source_id,
        },
    )


def upsert_committees_bulk(conn: Connection, rows: list[dict[str, Any]]) -> None:
    """Upsert many committees in one round trip (executemany pipelines these)."""
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(load_sql("committee_upsert"), rows)


# -- races / candidacies -----------------------------------------------------

def upsert_race(
    conn: Connection,
    *,
    cycle: int,
    state: str,
    office: str,
    district: str | None,
    senate_class: int | None,
    is_special: bool = False,
    source_id: int | None = None,
) -> int:
    return _returned_id(
        conn.execute(
            load_sql("race_upsert"),
            {
                "cycle": cycle,
                "state": state,
                "office": office,
                "district": district,
                "senate_class": senate_class,
                "is_special": is_special,
                "source_id": source_id,
            },
        )
    )


def upsert_candidacy(
    conn: Connection,
    *,
    race_id: int,
    politician_id: int,
    fec_candidate_id: str,
    party: str | None,
    incumbent_challenger: str | None,
    cand_status: str | None,
    principal_cmte_id: str | None,
    source_id: int,
) -> int:
    return _returned_id(
        conn.execute(
            load_sql("candidacy_upsert"),
            {
                "race_id": race_id,
                "politician_id": politician_id,
                "fec_candidate_id": fec_candidate_id,
                "party": party,
                "incumbent_challenger": incumbent_challenger,
                "cand_status": cand_status,
                "principal_cmte_id": principal_cmte_id,
                "source_id": source_id,
            },
        )
    )


# -- finance (Milestone 2) ---------------------------------------------------

@dataclass(frozen=True)
class Candidacy:
    """One row of select_state_candidacies — the unit of finance sync work."""

    candidacy_id: int
    politician_id: int
    fec_candidate_id: str
    principal_cmte_id: str | None
    full_name: str
    office: str
    district: str | None
    is_special: bool


def state_candidacies(conn: Connection, state: str, cycle: int) -> list[Candidacy]:
    cur = conn.execute(load_sql("select_state_candidacies"), {"state": state, "cycle": cycle})
    return [
        Candidacy(
            candidacy_id=int(r[0]),
            politician_id=int(r[1]),
            fec_candidate_id=str(r[2]),
            principal_cmte_id=None if r[3] is None else str(r[3]),
            full_name=str(r[4]),
            office=str(r[5]),
            district=None if r[6] is None else str(r[6]),
            is_special=bool(r[7]),
        )
        for r in cur.fetchall()
    ]


def state_committee_map(conn: Connection, state: str, cycle: int) -> dict[str, str]:
    """cmte_id -> fec_candidate_id for a state's candidates (indiv-file filter)."""
    cur = conn.execute(load_sql("select_state_committee_map"), {"state": state, "cycle": cycle})
    return {str(r[0]): str(r[1]) for r in cur.fetchall()}


def all_committee_ids(conn: Connection) -> set[str]:
    """Every known cmte_id (guards FK references while loading itemized rows)."""
    cur = conn.execute(load_sql("select_all_committee_ids"))
    return {str(r[0]) for r in cur.fetchall()}


def upsert_donations_bulk(conn: Connection, rows: list[dict[str, Any]]) -> None:
    """Upsert many itemized donation rows (keyed on fec_sub_id)."""
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(load_sql("donation_upsert"), rows)


def upsert_candidate_totals(conn: Connection, totals: dict[str, Any]) -> None:
    conn.execute(load_sql("candidate_totals_upsert"), totals)


def refresh_finance_views(conn: Connection) -> None:
    conn.execute(load_sql("refresh_finance_views"))


# -- ingestion_runs ----------------------------------------------------------

def start_run(conn: Connection, run_type: str, politician_id: int | None = None) -> int:
    return _returned_id(
        conn.execute(load_sql("run_start"), {"run_type": run_type, "politician_id": politician_id})
    )


def finish_run(
    conn: Connection,
    run_id: int,
    status: str,
    stats: dict[str, Any],
    error: str | None = None,
) -> None:
    conn.execute(
        load_sql("run_finish"),
        {"run_id": run_id, "status": status, "stats": Jsonb(stats), "error": error},
    )


# -- reporting ---------------------------------------------------------------

def count_rows(conn: Connection, table: str) -> int:
    """Row count for status reports. Identifier is safely quoted, not interpolated."""
    query = pgsql.SQL("SELECT count(*) FROM {}").format(pgsql.Identifier(table))
    return _returned_id(conn.execute(query))
