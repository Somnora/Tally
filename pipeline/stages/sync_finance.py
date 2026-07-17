"""Stage 1 — sync_finance: official FEC totals per candidate (Milestone 2).

Fetches the candidate's two-year financial totals from the OpenFEC API,
stores the raw payload as a sources row, and upserts candidate_totals.
Bulk itemized rows are loaded separately (pipeline.etl.fec_itemized); the
totals stored here are FEC's own aggregates and double as a consistency
check against our itemized sums in mv_candidacy_finance.

Called from the per-candidate DBOS workflow (pipeline.workflows); also
usable directly for one-off syncs.
"""

import hashlib
import json
from typing import Any

from pipeline import db, fec_api
from pipeline.stages import StageStats


def map_totals_payload(result: dict[str, Any]) -> dict[str, Any]:
    """OpenFEC /totals result -> candidate_totals columns (partial params)."""
    coverage_end = result.get("coverage_end_date")
    if isinstance(coverage_end, str):
        coverage_end = coverage_end[:10]  # ISO timestamp -> date part
    return {
        "total_receipts": result.get("receipts"),
        "total_disbursements": result.get("disbursements"),
        "cash_on_hand": result.get("last_cash_on_hand_end_period"),
        "debts_owed": result.get("last_debts_owed_by_committee"),
        "individual_itemized": result.get("individual_itemized_contributions"),
        "individual_unitemized": result.get("individual_unitemized_contributions"),
        "pac_contributions": result.get("other_political_committee_contributions"),
        "coverage_end": coverage_end,
    }


def sync_finance(
    conn: db.Connection, *, politician_id: int, fec_candidate_id: str, cycle: int
) -> StageStats:
    """Refresh one candidate's official totals. Idempotent.

    Candidates with no FEC filings yet (paper candidacies with no committee
    activity) are counted, not treated as errors.
    """
    payload, canonical_url = fec_api.candidate_totals(fec_candidate_id, cycle)
    raw = json.dumps(payload, sort_keys=True).encode("utf-8")
    source_id = db.insert_source(
        conn,
        source_type="fec_api_totals",
        url=canonical_url,
        content_hash=hashlib.sha256(raw).hexdigest(),
        raw_payload=raw,
    )

    results: list[dict[str, Any]] = payload.get("results") or []
    if not results:
        return {"no_filings": 1}

    totals = map_totals_payload(results[0]) | {
        "fec_candidate_id": fec_candidate_id,
        "cycle": cycle,
        "politician_id": politician_id,
        "source_id": source_id,
    }
    db.upsert_candidate_totals(conn, totals)
    return {"totals_upserted": 1}
