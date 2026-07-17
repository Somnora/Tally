"""DBOS workflows: durable ingestion (finance per candidate, votes per chamber).

The blueprint pattern, live: coordinators enqueue independent workflows — a
failure on one never blocks the others, and a crash resumes from the last
completed step (state lives in the civic_dbos Postgres database, not in this
process).

Run:
    uv run python -m pipeline.workflows finance --state ME
    uv run python -m pipeline.workflows votes --congress 119

Finance syncs per candidate (candidate-specific API data); votes sync per
chamber-session in batched steps, because all members share the same roll
calls — fetching them per candidate would repeat identical work 435 times.

Verified against installed dbos 2.27.0: DBOSConfig(name, system_database_url),
@DBOS.step(retries_allowed/max_attempts/interval_seconds/backoff_rate),
Queue(name, concurrency), Queue.enqueue -> WorkflowHandle.get_result().
"""

import argparse
import hashlib
import logging
from pathlib import Path
from typing import Any

import yaml
from dbos import DBOS, DBOSConfig, Queue

from pipeline import db
from pipeline.config import get_settings
from pipeline.etl import congress_votes
from pipeline.stages import extract_promises as extraction_stage
from pipeline.stages import sync_documents as documents_stage
from pipeline.stages.sync_finance import sync_finance

SEED_URL_BASE = "https://github.com/Somnora/Tally/blob/main"

VOTE_BATCH_SIZE = 25  # roll calls per durable step (~30s of fetching each)

logger = logging.getLogger(__name__)

DBOS(config=DBOSConfig(
    name="civic_ingestion",
    system_database_url=get_settings().dbos_system_database_url,
    run_admin_server=False,
))

# Concurrency 3: gentle on the FEC API (the client throttles globally too).
candidate_queue = Queue("candidates", concurrency=3)


# --- steps: each opens its own connection; upserts make retries safe --------

@DBOS.step()
def start_run_step(run_type: str, politician_id: int) -> int:
    with db.connect() as conn:
        return db.start_run(conn, run_type, politician_id)


@DBOS.step()
def finish_run_step(
    run_id: int, status: str, stats: dict[str, int], error: str | None = None
) -> None:
    with db.connect() as conn:
        db.finish_run(conn, run_id, status, dict(stats), error)


@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=5, backoff_rate=2.0)
def finance_step(politician_id: int, fec_candidate_id: str, cycle: int) -> dict[str, int]:
    with db.connect() as conn:
        return sync_finance(
            conn, politician_id=politician_id, fec_candidate_id=fec_candidate_id, cycle=cycle
        )


@DBOS.step()
def list_candidacies_step(state: str, cycle: int) -> list[dict[str, Any]]:
    with db.connect() as conn:
        return [
            {"politician_id": c.politician_id, "fec_candidate_id": c.fec_candidate_id}
            for c in db.state_candidacies(conn, state, cycle)
        ]


@DBOS.step()
def refresh_views_step() -> None:
    with db.connect() as conn:
        db.refresh_finance_views(conn)


# --- vote steps ---------------------------------------------------------------

@DBOS.step()
def ensure_members_step() -> int:
    with db.connect() as conn:
        return congress_votes.ensure_member_politicians(conn)


@DBOS.step(retries_allowed=True, max_attempts=4, interval_seconds=10, backoff_rate=2.0)
def list_new_rolls_step(chamber: str, congress: int, session: int) -> list[int]:
    with db.connect() as conn:
        if chamber == "house":
            return congress_votes.list_new_house_rolls(conn, congress, session)
        return congress_votes.list_new_senate_numbers(conn, congress, session)


@DBOS.step(retries_allowed=True, max_attempts=4, interval_seconds=10, backoff_rate=2.0)
def load_rolls_batch_step(
    chamber: str, congress: int, session: int, rolls: list[int]
) -> dict[str, int]:
    with db.connect() as conn:
        if chamber == "house":
            return congress_votes.load_house_rolls(conn, congress, session, rolls)
        return congress_votes.load_senate_votes(conn, congress, session, rolls)


# --- document steps -----------------------------------------------------------

@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=10, backoff_rate=2.0)
def documents_site_step(politician_id: int, campaign_url: str) -> dict[str, Any]:
    with db.connect() as conn:
        return documents_stage.sync_campaign_site(conn, politician_id, campaign_url)


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=10, backoff_rate=2.0)
def documents_wayback_step(politician_id: int, page_urls: list[str]) -> dict[str, int]:
    with db.connect() as conn:
        return documents_stage.sync_wayback(conn, politician_id, page_urls)


@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=10, backoff_rate=2.0)
def documents_youtube_step(
    politician_id: int, queries: list[str], required_name: str
) -> dict[str, int]:
    with db.connect() as conn:
        return documents_stage.sync_youtube(conn, politician_id, queries, required_name)


@DBOS.step()
def load_seed_step(seed_path: str, cycle: int) -> list[dict[str, Any]]:
    """Ingest the curated seed file itself as a source, resolve politicians."""
    raw = Path(seed_path).read_bytes()
    seed: dict[str, Any] = yaml.safe_load(raw)
    with db.connect() as conn:
        db.insert_source(
            conn, source_type="curated_seed",
            url=f"{SEED_URL_BASE}/{seed_path}",
            content_hash=hashlib.sha256(raw).hexdigest(), raw_payload=raw,
        )
        resolved: list[dict[str, Any]] = []
        seed_candidates: list[dict[str, Any]] = seed.get("candidates") or []
        for candidate in seed_candidates:
            fec_id = str(candidate["fec_candidate_id"])
            politician_id = db.politician_id_for_fec(conn, fec_id, cycle)
            if politician_id is None:
                logger.warning("seed candidate %s (%s) has no candidacy row; skipping",
                               candidate.get("display_name"), fec_id)
                continue
            queries: list[Any] = candidate.get("youtube_queries") or []
            resolved.append({
                "politician_id": politician_id,
                "display_name": str(candidate.get("display_name") or fec_id),
                "campaign_url": str(candidate["campaign_url"]),
                "youtube_queries": [str(q) for q in queries],
            })
    return resolved


def _merge_stats(into: dict[str, int], other: dict[str, int]) -> None:
    for key, value in other.items():
        into[key] = into.get(key, 0) + value


# --- extraction steps ----------------------------------------------------------

@DBOS.step(retries_allowed=True, max_attempts=3, interval_seconds=30, backoff_rate=2.0)
def extract_promises_step(politician_id: int) -> dict[str, int]:
    with db.connect() as conn:
        return extraction_stage.extract_promises(conn, politician_id)


@DBOS.step()
def list_extraction_candidates_step() -> list[int]:
    """Politicians with documents awaiting extraction under the CURRENT
    prompt+model (same predicate the per-document query uses)."""
    with db.connect() as conn:
        return db.politicians_needing_extraction(
            conn,
            extraction_stage.PROMPT_VERSION,
            get_settings().local_model or "unknown",
        )


# --- workflows ---------------------------------------------------------------

@DBOS.workflow()
def candidate_documents_workflow(
    politician_id: int, campaign_url: str, youtube_queries: list[str], required_name: str
) -> dict[str, int]:
    """Documents sync for one candidate: site, wayback, youtube — 3 durable steps."""
    run_id = start_run_step("sync_documents", politician_id)
    try:
        site = documents_site_step(politician_id, campaign_url)
        stats: dict[str, int] = dict(site["stats"])
        _merge_stats(stats, documents_wayback_step(politician_id, list(site["page_urls"])))
        _merge_stats(stats, documents_youtube_step(politician_id, youtube_queries, required_name))
    except Exception as exc:
        finish_run_step(run_id, "failed", {}, str(exc))
        raise
    finish_run_step(run_id, "succeeded", stats)
    return stats


@DBOS.workflow()
def documents_run(seed_path: str, cycle: int) -> dict[str, int]:
    """Coordinator: one documents workflow per seeded candidate."""
    candidates = load_seed_step(seed_path, cycle)
    logger.info("documents sync for %d seeded candidates", len(candidates))
    handles = [
        candidate_queue.enqueue(
            candidate_documents_workflow,
            c["politician_id"], c["campaign_url"], c["youtube_queries"],
            # surname is the relevance needle for YouTube results
            str(c["display_name"]).split()[-1],
        )
        for c in candidates
    ]
    totals: dict[str, int] = {"candidates": len(handles), "failed_candidates": 0}
    for handle in handles:
        try:
            _merge_stats(totals, handle.get_result())
        except Exception:
            totals["failed_candidates"] += 1
    return totals

@DBOS.workflow()
def candidate_finance_workflow(
    politician_id: int, fec_candidate_id: str, cycle: int
) -> dict[str, int]:
    """Finance sync for one candidate; later milestones add votes/documents."""
    run_id = start_run_step("sync_finance", politician_id)
    try:
        stats = finance_step(politician_id, fec_candidate_id, cycle)
    except Exception as exc:
        finish_run_step(run_id, "failed", {}, str(exc))
        raise
    finish_run_step(run_id, "succeeded", stats)
    return stats


@DBOS.workflow()
def state_finance_run(state: str, cycle: int) -> dict[str, int]:
    """Coordinator: enqueue every candidate in a state, then tally outcomes."""
    candidacies = list_candidacies_step(state, cycle)
    logger.info("enqueueing %d candidate workflows for %s", len(candidacies), state)

    handles = [
        candidate_queue.enqueue(
            candidate_finance_workflow, c["politician_id"], c["fec_candidate_id"], cycle
        )
        for c in candidacies
    ]

    totals: dict[str, int] = {"candidates": len(handles), "failed_candidates": 0}
    for handle in handles:
        try:
            for key, value in handle.get_result().items():
                totals[key] = totals.get(key, 0) + value
        except Exception:
            # The failed candidate's own ingestion_runs row has the error;
            # one bad candidate must not sink the state run.
            totals["failed_candidates"] += 1

    refresh_views_step()
    return totals


@DBOS.workflow()
def chamber_votes_workflow(chamber: str, congress: int, session: int) -> dict[str, int]:
    """Sync one chamber-session incrementally, in durable batches."""
    run_id = start_votes_run_step(f"sync_votes_{chamber}_{congress}_{session}")
    try:
        rolls = list_new_rolls_step(chamber, congress, session)
        stats: dict[str, int] = {"new_rolls": len(rolls)}
        for start in range(0, len(rolls), VOTE_BATCH_SIZE):
            batch = rolls[start : start + VOTE_BATCH_SIZE]
            for key, value in load_rolls_batch_step(chamber, congress, session, batch).items():
                stats[key] = stats.get(key, 0) + value
    except Exception as exc:
        finish_run_step(run_id, "failed", {}, str(exc))
        raise
    finish_run_step(run_id, "succeeded", stats)
    return stats


@DBOS.step()
def start_votes_run_step(run_type: str) -> int:
    with db.connect() as conn:
        return db.start_run(conn, run_type, None)


@DBOS.workflow()
def votes_run(congress: int, sessions: list[int]) -> dict[str, int]:
    """Coordinator: both chambers, all sessions, in parallel on the queue."""
    ensure_members_step()
    handles = [
        candidate_queue.enqueue(chamber_votes_workflow, chamber, congress, session)
        for chamber in ("house", "senate")
        for session in sessions
    ]
    totals: dict[str, int] = {"chamber_sessions": len(handles), "failed_chamber_sessions": 0}
    for handle in handles:
        try:
            for key, value in handle.get_result().items():
                totals[key] = totals.get(key, 0) + value
        except Exception:
            totals["failed_chamber_sessions"] += 1
    return totals


@DBOS.workflow()
def candidate_extraction_workflow(politician_id: int) -> dict[str, int]:
    run_id = start_run_step("extract_promises", politician_id)
    try:
        stats = extract_promises_step(politician_id)
    except Exception as exc:
        finish_run_step(run_id, "failed", {}, str(exc))
        raise
    finish_run_step(run_id, "succeeded", stats)
    return stats


@DBOS.workflow()
def extraction_run() -> dict[str, int]:
    """Coordinator: one extraction workflow per candidate with pending docs."""
    politician_ids = list_extraction_candidates_step()
    logger.info("extraction for %d candidates with pending documents", len(politician_ids))
    handles = [
        candidate_queue.enqueue(candidate_extraction_workflow, pid)
        for pid in politician_ids
    ]
    totals: dict[str, int] = {"candidates": len(handles), "failed_candidates": 0}
    for handle in handles:
        try:
            _merge_stats(totals, handle.get_result())
        except Exception:
            totals["failed_candidates"] += 1
    return totals


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    finance = sub.add_parser("finance", help="official FEC totals per candidate")
    finance.add_argument("--state", required=True)
    finance.add_argument("--cycle", type=int, default=2026)

    votes = sub.add_parser("votes", help="roll-call votes for both chambers")
    votes.add_argument("--congress", type=int, default=119)
    votes.add_argument("--sessions", default="1,2", help="comma-separated, e.g. 1,2")

    documents = sub.add_parser("documents", help="campaign sites, wayback, youtube")
    documents.add_argument("--seed", default="data/seeds/me_pilot.yaml")
    documents.add_argument("--cycle", type=int, default=2026)

    sub.add_parser("extract", help="LLM promise extraction (needs VLLM_BASE_URL)")

    args = parser.parse_args()
    DBOS.launch()
    try:
        if args.command == "finance":
            totals = state_finance_run(args.state.upper(), args.cycle)
        elif args.command == "documents":
            totals = documents_run(args.seed, args.cycle)
        elif args.command == "extract":
            settings = get_settings()
            if not settings.vllm_base_url or not settings.local_model:
                raise SystemExit(
                    "extract needs a model endpoint: set VLLM_BASE_URL and LOCAL_MODEL "
                    "in .env (start a vllm-serve or sglang-serve job in Manifold first)"
                )
            totals = extraction_run()
        else:
            sessions = [int(s) for s in str(args.sessions).split(",") if s.strip()]
            totals = votes_run(args.congress, sessions)
        for key in sorted(totals):
            logger.info("%-24s %d", key, totals[key])
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    main()
