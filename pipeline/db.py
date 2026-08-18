"""Thin repository module: the only place Python talks to Postgres.

Rules enforced here:
  * SQL lives in db/sql/*.sql files, loaded by name — never f-strings.
  * Every write is an idempotent upsert keyed on a natural id.
  * Callers manage transactions: `with db.connect() as conn:` commits on
    success and rolls back on exception (psycopg connection semantics).
"""

import logging
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
from psycopg import sql as pgsql
from psycopg.types.json import Jsonb

from pipeline import evidence
from pipeline.config import get_settings

logger = logging.getLogger(__name__)

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


@dataclass(frozen=True)
class IdentityPair:
    """Our own evidence about a candidate-identity pair the FEC proposed."""

    incumbent_politician_id: int
    other_politician_id: int
    incumbent_bioguide: str | None
    incumbent_votes: int
    other_votes: int
    already_linked: bool


def resolve_identity_pair(
    conn: Connection, incumbent_fec_id: str, other_fec_id: str, cycle: int
) -> IdentityPair | None:
    """None when either id has no candidacy here, or both resolve to one person."""
    row = conn.execute(load_sql("resolve_identity_pair"), {
        "incumbent_fec_id": incumbent_fec_id, "other_fec_id": other_fec_id,
        "cycle": cycle,
    }).fetchone()
    if row is None:
        return None
    return IdentityPair(
        incumbent_politician_id=int(row[0]), other_politician_id=int(row[1]),
        incumbent_bioguide=row[2], incumbent_votes=int(row[3]),
        other_votes=int(row[4]), already_linked=bool(row[5]),
    )


def apply_identity_link(
    conn: Connection, *, incumbent_fec_id: str, other_fec_id: str,
    politician_id: int, superseded_politician_id: int, basis: str,
    source_id: int, cycle: int,
) -> tuple[int, int]:
    """Record the link, then move the candidacy and its money onto the person.

    The superseded politician row is left in place. It cost nothing to keep
    and it is what makes this reversible, which matters for an edit whose
    failure mode is one member's votes appearing under another member's name.
    """
    conn.execute(load_sql("identity_link_insert"), {
        "incumbent_fec_id": incumbent_fec_id, "other_fec_id": other_fec_id,
        "politician_id": politician_id,
        "superseded_politician_id": superseded_politician_id,
        "basis": basis, "source_id": source_id,
    })
    params = {"politician_id": politician_id, "other_fec_id": other_fec_id,
              "cycle": cycle}
    candidacies = conn.execute(
        load_sql("identity_link_repoint_candidacy"), params).rowcount
    donations = conn.execute(
        load_sql("identity_link_repoint_donations"),
        {"politician_id": politician_id, "other_fec_id": other_fec_id}).rowcount
    return candidacies, donations


def state_committee_map(conn: Connection, state: str, cycle: int) -> dict[str, str]:
    """cmte_id -> fec_candidate_id for a state's candidates (indiv-file filter).

    Collisions are reported, never silently resolved. A dict comprehension
    over this query used to keep whichever row arrived last, and that is how
    Chris Pappas's $5.6 million came to sit on a House seat he is not running
    for while his Senate campaign showed nothing: one committee, two
    candidacies, no complaint. The query now disambiguates through the FEC's
    own committee linkage, so anything still colliding is a case it cannot
    settle and a human should see it.
    """
    cur = conn.execute(load_sql("select_state_committee_map"), {"state": state, "cycle": cycle})
    mapping: dict[str, str] = {}
    collisions: dict[str, list[str]] = {}
    for row in cur.fetchall():
        cmte_id, cand_id = str(row[0]), str(row[1])
        if cmte_id in mapping and mapping[cmte_id] != cand_id:
            collisions.setdefault(cmte_id, [mapping[cmte_id]]).append(cand_id)
        mapping[cmte_id] = cand_id
    for cmte_id, candidates in collisions.items():
        # Deterministic, so two runs over the same data agree; the warning is
        # the point, not the choice.
        winner = sorted(candidates)[0]
        mapping[cmte_id] = winner
        logger.warning(
            "committee %s maps to %d candidates %s; attributing to %s",
            cmte_id, len(candidates), sorted(candidates), winner,
        )
    return mapping


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


# -- votes (Milestone 3) -------------------------------------------------------

def max_roll_call(conn: Connection, chamber: str, congress: int, session: int) -> int:
    """Newest roll call already stored — the incremental-sync watermark."""
    return _returned_id(
        conn.execute(
            load_sql("select_max_roll_call"),
            {"chamber": chamber, "congress": congress, "session": session},
        )
    )


def upsert_voting_records_bulk(conn: Connection, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with conn.cursor() as cur:
        cur.executemany(load_sql("voting_record_upsert"), rows)


def member_politician_maps(conn: Connection) -> tuple[dict[str, int], dict[str, int]]:
    """(bioguide -> politician_id, lis -> politician_id) for vote attribution."""
    by_bioguide: dict[str, int] = {}
    by_lis: dict[str, int] = {}
    for bioguide, lis, politician_id in conn.execute(
        load_sql("select_member_politicians")
    ).fetchall():
        by_bioguide[str(bioguide)] = int(politician_id)
        if lis is not None:
            by_lis[str(lis)] = int(politician_id)
    return by_bioguide, by_lis


def crosswalk_members(conn: Connection) -> list[tuple[str, str, int]]:
    """(bioguide_id, full_name, source_id) for every current member."""
    return [
        (str(r[0]), str(r[1]), int(r[2]))
        for r in conn.execute(load_sql("select_crosswalk_members")).fetchall()
    ]


# -- bills (Milestone 5) -------------------------------------------------------

def politician_id_by_name(conn: Connection, name: str) -> int | None:
    """Resolve a full name to one politician_id, or None if unknown.

    Raises on an ambiguous name rather than picking one: silently choosing
    the wrong Collins would attribute a voting record to the wrong person.
    """
    rows = conn.execute(load_sql("select_politician_by_name"), {"name": name}).fetchall()
    if not rows:
        return None
    if len(rows) > 1 and str(rows[0][1]) != name:
        candidates = ", ".join(str(r[1]) for r in rows[:5])
        raise ValueError(f"{name!r} matches several politicians: {candidates}")
    return int(rows[0][0])


def bill_keys_needing_metadata(
    conn: Connection, politician_id: int | None = None
) -> list[tuple[int, str]]:
    """(congress, bill_key) pairs that were voted on but have no bills row.

    Passing a politician_id restricts the backfill to that member's record,
    which is how the pilot runs it before committing to every chamber.
    """
    rows = conn.execute(
        load_sql("select_bill_keys_needing_metadata"),
        {"politician_id": politician_id},
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def upsert_bill(
    conn: Connection,
    *,
    congress: int,
    bill_key: str,
    bill_type: str,
    bill_number: int,
    title: str | None,
    policy_area: str | None,
    subjects: list[str],
    summary_text: str | None,
    introduced_date: date | None,
    latest_action: str | None,
    latest_action_date: date | None,
    sponsor_bioguide: str | None,
    congress_gov_url: str,
    source_id: int | None,
) -> int:
    """Idempotent on (congress, bill_key); refreshes mutable fields."""
    return _returned_id(
        conn.execute(
            load_sql("bill_upsert"),
            {
                "congress": congress,
                "bill_key": bill_key,
                "bill_type": bill_type,
                "bill_number": bill_number,
                "title": title,
                "policy_area": policy_area,
                "subjects": subjects,
                "summary_text": summary_text,
                "introduced_date": introduced_date,
                "latest_action": latest_action,
                "latest_action_date": latest_action_date,
                "sponsor_bioguide": sponsor_bioguide,
                "congress_gov_url": congress_gov_url,
                "source_id": source_id,
            },
        )
    )


# -- documents / media (Milestone 4) -------------------------------------------

def politician_id_for_fec(conn: Connection, fec_candidate_id: str, cycle: int) -> int | None:
    cur = conn.execute(
        load_sql("select_politician_for_fec"),
        {"fec_candidate_id": fec_candidate_id, "cycle": cycle},
    )
    row = cur.fetchone()
    return None if row is None else int(row[0])


def insert_document(
    conn: Connection,
    *,
    politician_id: int,
    source_id: int,
    doc_type: str,
    title: str | None,
    url: str,
    published_at: str | None,
    full_text: str,
    content_hash: str,
    transcribed_by: str | None = None,
    meta: dict[str, Any] | None = None,
) -> int:
    """Idempotent on (politician_id, content_hash); returns document_id."""
    return _returned_id(
        conn.execute(
            load_sql("document_insert"),
            {
                "politician_id": politician_id,
                "source_id": source_id,
                "doc_type": doc_type,
                "title": title,
                "url": url,
                "published_at": published_at,
                "full_text": full_text,
                "content_hash": content_hash,
                "transcribed_by": transcribed_by,
                "meta": Jsonb(meta or {}),
            },
        )
    )


def upsert_media_asset(
    conn: Connection,
    *,
    politician_id: int,
    external_id: str,
    title: str | None,
    channel_title: str | None,
    url: str,
    published_at: str | None,
    has_captions: bool | None,
    document_id: int | None,
    source_id: int,
    platform: str = "youtube",
) -> int:
    return _returned_id(
        conn.execute(
            load_sql("media_asset_upsert"),
            {
                "politician_id": politician_id,
                "platform": platform,
                "external_id": external_id,
                "title": title,
                "channel_title": channel_title,
                "url": url,
                "published_at": published_at,
                "has_captions": has_captions,
                "document_id": document_id,
                "source_id": source_id,
            },
        )
    )


# -- promises / extraction (Milestone 4) ---------------------------------------

@dataclass(frozen=True)
class DocumentForExtraction:
    document_id: int
    doc_type: str
    title: str | None
    url: str
    full_text: str


def documents_for_extraction(
    conn: Connection, politician_id: int, prompt_version: str, model_name: str
) -> list[DocumentForExtraction]:
    cur = conn.execute(
        load_sql("select_documents_for_extraction"),
        {"politician_id": politician_id, "prompt_version": prompt_version,
         "model_name": model_name},
    )
    return [
        DocumentForExtraction(
            document_id=int(r[0]), doc_type=str(r[1]),
            title=None if r[2] is None else str(r[2]),
            url=str(r[3]), full_text=str(r[4]),
        )
        for r in cur.fetchall()
    ]


def insert_verified_promise(
    conn: Connection,
    *,
    politician_id: int,
    document_id: int,
    verbatim_quote: str,
    char_start: int,
    char_end: int,
    topic: str,
    specificity: str,
    model_name: str,
    prompt_version: str,
) -> None:
    """Store a promise whose quote already passed verify_quote. Idempotent."""
    conn.execute(
        load_sql("promise_insert"),
        {
            "politician_id": politician_id,
            "document_id": document_id,
            "verbatim_quote": verbatim_quote,
            "char_start": char_start,
            "char_end": char_end,
            "topic": topic,
            "specificity": specificity,
            "is_scoreable": specificity != "rhetorical",
            "model_name": model_name,
            "prompt_version": prompt_version,
        },
    )


def politicians_needing_extraction(
    conn: Connection, prompt_version: str, model_name: str
) -> list[int]:
    cur = conn.execute(
        load_sql("select_politicians_needing_extraction"),
        {"prompt_version": prompt_version, "model_name": model_name},
    )
    return sorted(int(r[0]) for r in cur.fetchall())


def insert_extraction_reject(
    conn: Connection,
    *,
    document_id: int,
    politician_id: int,
    rejected_quote: str,
    chunk_offset: int,
    model_name: str,
    prompt_version: str,
) -> None:
    """Persist a gate rejection — QA data for prompt iteration, never displayed."""
    conn.execute(
        load_sql("extraction_reject_insert"),
        {
            "document_id": document_id,
            "politician_id": politician_id,
            "rejected_quote": rejected_quote,
            "chunk_offset": chunk_offset,
            "model_name": model_name,
            "prompt_version": prompt_version,
        },
    )


def delete_promises_for_document(conn: Connection, document_id: int) -> None:
    """Remove a document's promises before re-extraction under a new prompt/model."""
    conn.execute(load_sql("promises_delete_for_document"), {"document_id": document_id})


@dataclass(frozen=True)
class ReviewItem:
    promise_id: int
    politician_name: str
    doc_title: str | None
    doc_type: str
    url: str
    verbatim_quote: str
    topic: str
    specificity: str
    is_scoreable: bool
    prompt_version: str
    model_name: str
    context_before: str
    context_after: str


def promises_for_review(conn: Connection, context_chars: int = 300) -> list[ReviewItem]:
    """Promises with no human verdict yet, with surrounding document context."""
    cur = conn.execute(
        load_sql("select_promises_for_review"), {"context_chars": context_chars}
    )
    return [
        ReviewItem(
            promise_id=int(r[0]), politician_name=r[1], doc_title=r[2],
            doc_type=r[3], url=r[4], verbatim_quote=r[5], topic=r[8],
            specificity=r[9], is_scoreable=r[10],
            prompt_version=r[11], model_name=r[12],
            context_before=r[13] or "", context_after=r[14] or "",
        )
        for r in cur.fetchall()
    ]


def upsert_promise_review(
    conn: Connection,
    *,
    promise_id: int,
    verdict: str,
    note: str | None,
    prompt_version: str,
    model_name: str,
) -> None:
    """Record (or overwrite) the human verdict on one promise."""
    conn.execute(
        load_sql("promise_review_upsert"),
        {
            "promise_id": promise_id,
            "verdict": verdict,
            "note": note,
            "prompt_version": prompt_version,
            "model_name": model_name,
        },
    )


def review_summary(conn: Connection) -> list[tuple[str, str, int]]:
    """(prompt_version, verdict, count) rows for the precision report."""
    cur = conn.execute(load_sql("report_review_summary"))
    return [(r[0], r[1], int(r[2])) for r in cur.fetchall()]


def mark_document_extracted(
    conn: Connection, document_id: int, model_name: str, prompt_version: str
) -> None:
    conn.execute(
        load_sql("update_document_extracted"),
        {"document_id": document_id, "model_name": model_name,
         "prompt_version": prompt_version},
    )


def states_for_cycle(conn: Connection, cycle: int) -> list[str]:
    """Every state holding a race this cycle, for national runs."""
    return [
        str(r[0])
        for r in conn.execute(load_sql("select_states_for_cycle"), {"cycle": cycle}).fetchall()
    ]


def promises_for_gate(
    conn: Connection, *, gate_version: str, only_unscreened: bool = True
) -> list[tuple[int, str]]:
    """(promise_id, verbatim_quote) awaiting the selectivity screen."""
    rows = conn.execute(
        load_sql("select_promises_for_gate"),
        {"gate_version": gate_version, "only_unscreened": only_unscreened},
    ).fetchall()
    return [(int(r[0]), str(r[1])) for r in rows]


def set_gate_verdict(
    conn: Connection, *, promise_id: int, keep: bool, reason: str, gate_version: str
) -> None:
    """Store the gate's opinion. The promise itself is never modified."""
    conn.execute(
        load_sql("promise_set_gate_verdict"),
        {"promise_id": promise_id, "gate_keep": keep, "gate_reason": reason,
         "gate_version": gate_version},
    )


def unscreened_exportable_promises(conn: Connection) -> int:
    """Exportable promises the current gate has never seen.

    The export job refuses to build while this is non-zero: shipping a
    promise nobody screened is the exact failure the gate exists to prevent.
    """
    cur = conn.execute(
        "SELECT count(*) FROM app_export_promises e "
        "JOIN promises p USING (promise_id) WHERE p.gate_keep IS NULL"
    )
    row = cur.fetchone()
    return 0 if row is None else int(row[0])


# -- evaluation (Milestone 5) --------------------------------------------------

@dataclass(frozen=True)
class PromiseForEvaluation:
    promise_id: int
    politician_id: int
    full_name: str
    verbatim_quote: str
    topic: str
    specificity: str


@dataclass(frozen=True)
class VoteContext:
    """One pre-digested vote as the evaluation prompt will see it."""

    vote_id: int
    bill_key: str
    title: str | None
    policy_area: str | None
    summary_text: str | None
    position: str
    vote_question: str
    voted_at: date
    congress_gov_url: str
    is_procedural: bool
    is_omnibus: bool


def promises_for_evaluation(
    conn: Connection, politician_id: int, *, model_name: str, prompt_version: str
) -> list[PromiseForEvaluation]:
    rows = conn.execute(
        load_sql("select_promises_for_evaluation"),
        {"politician_id": politician_id, "model_name": model_name,
         "prompt_version": prompt_version},
    ).fetchall()
    return [
        PromiseForEvaluation(int(r[0]), int(r[1]), str(r[2]), str(r[3]),
                             str(r[4]), str(r[5]))
        for r in rows
    ]


def members_for_evaluation(
    conn: Connection, *, model_name: str, prompt_version: str
) -> list[tuple[int, str, int]]:
    """(politician_id, full_name, pending_count) for every member with work left."""
    rows = conn.execute(
        load_sql("select_members_for_evaluation"),
        {"model_name": model_name, "prompt_version": prompt_version},
    ).fetchall()
    return [(int(r[0]), str(r[1]), int(r[2])) for r in rows]


def count_unscreened_promises(conn: Connection) -> int:
    """Verified promises the selectivity gate has not judged yet.

    Evaluation requires gate_keep, so these are silently invisible to it.
    The batch runner reports the number rather than letting a forgotten
    gate run look like a corpus with nothing left to evaluate.
    """
    row = conn.execute(
        "SELECT count(*) FROM promises WHERE quote_verified AND gate_keep IS NULL"
    ).fetchone()
    return int(row[0]) if row else 0


def votes_for_promise(
    conn: Connection, politician_id: int, topic: str, limit: int = 25
) -> list[VoteContext]:
    """Topically relevant votes, deduplicated to one per bill."""
    rows = conn.execute(
        load_sql("select_votes_for_promise"),
        {"politician_id": politician_id, "topic": topic, "limit": limit},
    ).fetchall()
    return [
        VoteContext(
            vote_id=int(r[0]), bill_key=str(r[1]), title=r[2], policy_area=r[3],
            summary_text=r[4], position=str(r[5]), vote_question=str(r[6]),
            voted_at=r[7], congress_gov_url=str(r[8]),
            is_procedural=bool(r[9]), is_omnibus=bool(r[10]),
        )
        for r in rows
    ]


def vote_facts(conn: Connection, vote_ids: list[int]) -> dict[int, evidence.VoteFact]:
    """Ground truth for cited votes, keyed by vote_id.

    Ids the model invented simply do not come back, which is what makes
    'unknown_record' detectable rather than assumed.
    """
    if not vote_ids:
        return {}
    rows = conn.execute(
        load_sql("select_vote_facts_for_validation"), {"vote_ids": vote_ids}
    ).fetchall()
    return {
        int(r[0]): evidence.VoteFact(
            vote_id=int(r[0]), politician_id=int(r[1]), position=str(r[2]),
            vote_question=str(r[3]), bill_key=r[4],
            is_omnibus=bool(r[5]), is_procedural=bool(r[6]),
            subjects=frozenset(str(x) for x in cast(list[Any], r[7] or [])),
        )
        for r in rows
    }


def supersede_current_evaluation(conn: Connection, promise_id: int) -> None:
    """Retire the live evaluation. Append-only: only is_current changes."""
    conn.execute(load_sql("evaluation_supersede"), {"promise_id": promise_id})


def insert_evaluation(
    conn: Connection,
    *,
    promise_id: int,
    status: str,
    consistency_score: int | None,
    llm_reasoning: str,
    model_name: str,
    prompt_version: str,
    is_current: bool,
) -> int:
    """Store one evaluation. is_current = FALSE keeps a failed evaluation for
    review while guaranteeing it can never reach the export view."""
    evaluation_id = _returned_id(
        conn.execute(
            load_sql("evaluation_insert"),
            {"promise_id": promise_id, "status": status,
             "consistency_score": consistency_score,
             "llm_reasoning": llm_reasoning, "model_name": model_name,
             "prompt_version": prompt_version},
        )
    )
    if not is_current:
        conn.execute(
            "UPDATE promise_evaluations SET is_current = FALSE WHERE evaluation_id = %s",
            (evaluation_id,),
        )
    return evaluation_id


def insert_evaluation_evidence(
    conn: Connection,
    *,
    evaluation_id: int,
    kind: str,
    record_id: int,
    direction: str,
    validated: bool,
) -> int:
    """Store one citation. Only the id column matching `kind` is populated;
    the schema CHECK and the per-kind foreign keys enforce the rest."""
    columns: dict[str, int | None] = {
        "vote_id": None, "donation_id": None, "filing_uuid": None, "document_id": None,
    }
    key = {"vote": "vote_id", "donation": "donation_id",
           "lobbying_filing": "filing_uuid", "document": "document_id"}[kind]
    columns[key] = record_id
    return _returned_id(
        conn.execute(
            load_sql("evaluation_evidence_insert"),
            {"evaluation_id": evaluation_id, "kind": kind, "direction": direction,
             "validated": validated, **columns},
        )
    )


@dataclass(frozen=True)
class StoredCitation:
    """One persisted citation, as revalidation sees it."""

    evidence_id: int
    evaluation_id: int
    kind: str
    vote_id: int | None
    direction: str
    validated: bool
    politician_id: int
    status: str
    is_current: bool
    promise_topic: str = ""


def evidence_for_revalidation(conn: Connection) -> list[StoredCitation]:
    rows = conn.execute(load_sql("select_evidence_for_revalidation")).fetchall()
    return [
        StoredCitation(
            evidence_id=int(r[0]), evaluation_id=int(r[1]), kind=str(r[2]),
            vote_id=None if r[3] is None else int(r[3]), direction=str(r[4]),
            validated=bool(r[5]), politician_id=int(r[6]), status=str(r[7]),
            is_current=bool(r[8]), promise_topic=str(r[9] or ""),
        )
        for r in rows
    ]


def evaluations_awaiting_review(conn: Connection) -> list[dict[str, Any]]:
    """Broken verdicts with no signature, with everything needed to judge one."""
    rows = conn.execute(load_sql("select_evaluations_awaiting_review")).fetchall()
    return [
        {
            "evaluation_id": int(r[0]), "politician": str(r[1]), "topic": str(r[2]),
            "quote": str(r[3]), "score": r[4], "reasoning": str(r[5]),
            "citations": cast(list[dict[str, Any]], r[6] or []),
        }
        for r in rows
    ]


def record_evaluation_review(
    conn: Connection, *, evaluation_id: int, approved: bool,
    reviewed_by: str, review_note: str | None = None,
) -> None:
    """Sign off or reject. A rejection keeps the row and drops it from export."""
    conn.execute(
        load_sql("evaluation_record_review"),
        {"evaluation_id": evaluation_id, "approved": approved,
         "reviewed_by": reviewed_by, "review_note": review_note},
    )


def set_evidence_validated(conn: Connection, evidence_id: int, validated: bool) -> None:
    conn.execute(
        load_sql("evaluation_evidence_set_validated"),
        {"evidence_id": evidence_id, "validated": validated},
    )


def set_evaluation_current(conn: Connection, evaluation_id: int, is_current: bool) -> None:
    conn.execute(
        load_sql("evaluation_set_current"),
        {"evaluation_id": evaluation_id, "is_current": is_current},
    )


def evaluation_summary(conn: Connection) -> list[tuple[str, str, str, int, int, int]]:
    return [
        (str(r[0]), str(r[1]), str(r[2]), int(r[3]), int(r[4]), int(r[5]))
        for r in conn.execute(load_sql("report_evaluation_summary")).fetchall()
    ]


def citation_reject_summary(conn: Connection) -> list[tuple[str, int, int]]:
    return [
        (str(r[0]), int(r[1]), int(r[2]))
        for r in conn.execute(load_sql("report_citation_rejects")).fetchall()
    ]


def insert_citation_reject(
    conn: Connection,
    *,
    evaluation_id: int,
    kind: str,
    cited_record_id: int,
    direction: str,
    reason: str,
) -> None:
    """Persist a citation that could not be stored as evidence, typically a
    fabricated id. Kept because it is the sharpest available signal that a
    prompt or model is unfit, and it never appears in the model's prose."""
    conn.execute(
        load_sql("evaluation_citation_reject_insert"),
        {"evaluation_id": evaluation_id, "kind": kind,
         "cited_record_id": cited_record_id, "direction": direction, "reason": reason},
    )


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
