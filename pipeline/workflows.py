"""DBOS workflows: durable per-candidate ingestion (Milestone 2: finance only).

The blueprint pattern, live: a coordinator workflow enqueues ONE workflow per
candidate — a failure on one candidate never blocks the others, and a crash
resumes from the last completed step (state lives in the civic_dbos Postgres
database, not in this process).

Run:
    uv run python -m pipeline.workflows --state ME

Verified against installed dbos 2.27.0: DBOSConfig(name, system_database_url),
@DBOS.step(retries_allowed/max_attempts/interval_seconds/backoff_rate),
Queue(name, concurrency), Queue.enqueue -> WorkflowHandle.get_result().
"""

import argparse
import logging
from typing import Any

from dbos import DBOS, DBOSConfig, Queue

from pipeline import db
from pipeline.config import get_settings
from pipeline.stages.sync_finance import sync_finance

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


# --- workflows ---------------------------------------------------------------

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


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--cycle", type=int, default=2026)
    args = parser.parse_args()

    DBOS.launch()
    try:
        totals = state_finance_run(args.state.upper(), args.cycle)
        for key in sorted(totals):
            logger.info("%-24s %d", key, totals[key])
    finally:
        DBOS.destroy()


if __name__ == "__main__":
    main()
