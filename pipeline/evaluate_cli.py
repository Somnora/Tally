"""Run the evaluation stage for one member.

The stage itself lives in pipeline.stages.evaluate_promises; this is just the
handle that makes a run reproducible from a shell and prints what the
validator caught. Every number it reports is a count of something code
checked, not something the model claimed.

Needs a live model endpoint (VLLM_BASE_URL and LOCAL_MODEL in .env), so it
only runs while a GPU is serving.

Run:  uv run python -m pipeline.evaluate_cli --politician "Chellie Pingree"
      uv run python -m pipeline.evaluate_cli --politician "Chellie Pingree" --dry-run
"""

import argparse
import logging
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from pipeline import db
from pipeline.config import get_settings
from pipeline.stages import StageStats
from pipeline.stages.evaluate_promises import (
    MAX_VOTES,
    PROMPT_VERSION,
    build_prompt,
    evaluate_promises,
)

logger = logging.getLogger(__name__)


def run_dry(politician_id: int, model_name: str) -> None:
    """Show what would be sent, without calling a model. Cheap way to confirm
    the pre-filter is sane before spending GPU time on a whole member."""
    with db.connect() as conn:
        promises = db.promises_for_evaluation(
            conn, politician_id, model_name=model_name, prompt_version=PROMPT_VERSION
        )
        print(f"{len(promises)} promises eligible for evaluation.\n")
        for promise in promises:
            votes = db.votes_for_promise(
                conn, promise.politician_id, promise.topic, MAX_VOTES
            )
            omnibus = sum(1 for v in votes if v.is_omnibus)
            print(f"  [{promise.promise_id}] {promise.topic:<16} "
                  f"{promise.specificity:<12} {len(votes):>2} votes "
                  f"({omnibus} omnibus)  {promise.verbatim_quote[:52]}")
        if promises:
            print("\nExample prompt for the first eligible promise:\n")
            first = promises[0]
            print(build_prompt(
                first, db.votes_for_promise(conn, first.politician_id, first.topic, MAX_VOTES)
            ))


def run(politician_id: int, model_name: str) -> None:
    with db.connect() as conn:
        stats = evaluate_promises(conn, politician_id, model_name)

    print("\nEvaluation run complete.")
    for key in ("eligible", "evaluated", "no_related_votes",
                "evaluations_validated", "evaluations_failed_validation"):
        print(f"  {key:<34} {stats.get(key, 0)}")

    citation_reasons = {k: v for k, v in stats.items() if k.startswith("citation_")}
    if citation_reasons:
        print("\n  citations by outcome:")
        for reason, count in sorted(citation_reasons.items(), key=lambda kv: -kv[1]):
            print(f"    {reason[len('citation_'):]:<32} {count}")
    other = {k: v for k, v in stats.items()
             if k.startswith(("score_", "missing_"))}
    for key, count in sorted(other.items()):
        print(f"  {key:<34} {count}")


def run_all(model_name: str, workers: int) -> None:
    """Every member with work left, one connection and one transaction each.

    Per-member isolation is the same rule the ingestion workflows follow: a
    run this long must not hold one transaction open, and a member the model
    chokes on must not roll back the members already stored. A failure is
    counted and logged, and that member is simply still pending on the next
    run.
    """
    with db.connect() as conn:
        members = db.members_for_evaluation(
            conn, model_name=model_name, prompt_version=PROMPT_VERSION
        )
        unscreened = db.count_unscreened_promises(conn)

    if unscreened:
        # Evaluation requires gate_keep, so these are invisible to the query
        # above. Saying so is the difference between "nothing left to do" and
        # "the gate has not run since the last extraction".
        print(f"NOTE: {unscreened} verified promises carry no gate verdict and are "
              f"not eligible.\n      Run: uv run python -m pipeline.gate_cli --apply\n")

    total_pending = sum(pending for _, _, pending in members)
    print(f"{len(members)} members with {total_pending} promises awaiting evaluation, "
          f"{workers} at a time.\n")

    totals: Counter[str] = Counter()
    failed: list[str] = []
    done = 0

    def one_member(member: tuple[int, str, int]) -> tuple[str, int, StageStats]:
        politician_id, name, pending = member
        # A connection per member, opened inside the worker thread: psycopg
        # connections are not for sharing across threads, and the per-member
        # transaction boundary is what keeps one bad member from rolling back
        # the others.
        with db.connect() as conn:
            return name, pending, evaluate_promises(conn, politician_id, model_name)

    # Members run concurrently because the model server batches: vLLM is
    # started with --max-num-seqs 8, and a sequential client leaves seven of
    # those slots idle while it waits on the eighth. Threads rather than
    # processes because every one of these is blocked on a socket.
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(one_member, m): m for m in members}
        for future in as_completed(futures):
            done += 1
            member = futures[future]
            try:
                name, pending, stats = future.result()
            except Exception:
                failed.append(member[1])
                logger.exception("%s failed; continuing", member[1])
                continue
            totals.update(stats)
            print(f"  {done:>3}/{len(members)} {name[:34]:<34} "
                  f"{pending:>3} promises  "
                  f"{stats.get('evaluations_validated', 0):>3} validated  "
                  f"{stats.get('evaluations_failed_validation', 0):>3} rejected  "
                  f"{stats.get('no_related_votes', 0):>3} no votes",
                  flush=True)

    print("\nBatch complete.")
    for key in ("eligible", "evaluated", "no_related_votes",
                "evaluations_validated", "evaluations_failed_validation",
                "model_failures"):
        print(f"  {key:<34} {totals.get(key, 0)}")
    if failed:
        print(f"\n  {len(failed)} members failed outright and remain pending:")
        for name in failed:
            print(f"    {name}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate promises for one member")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--politician", help="full name as stored")
    target.add_argument("--all", action="store_true",
                        help="every member with promises still awaiting evaluation")
    parser.add_argument("--workers", type=int, default=6,
                        help="members evaluated concurrently (vllm serves 8 sequences)")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the eligible promises and a sample prompt, call no model")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = get_settings()
    model_name = settings.local_model or "unset"

    if args.all:
        if args.dry_run:
            with db.connect() as conn:
                members = db.members_for_evaluation(
                    conn, model_name=model_name, prompt_version=PROMPT_VERSION
                )
                unscreened = db.count_unscreened_promises(conn)
            print(f"{len(members)} members, "
                  f"{sum(p for _, _, p in members)} promises awaiting evaluation "
                  f"({unscreened} unscreened and therefore not eligible).")
            for politician_id, name, pending in members[:20]:
                print(f"  {politician_id:>6}  {name[:40]:<40} {pending:>4}")
            if len(members) > 20:
                print(f"  ... and {len(members) - 20} more")
            return
        if not settings.vllm_base_url or not settings.local_model:
            parser.error("set VLLM_BASE_URL and LOCAL_MODEL in .env (start a GPU first)")
        run_all(model_name, args.workers)
        return

    with db.connect() as conn:
        politician_id = db.politician_id_by_name(conn, args.politician)
    if politician_id is None:
        parser.error(f"no politician matching {args.politician!r}")

    if args.dry_run:
        run_dry(politician_id, model_name)
        return
    if not settings.vllm_base_url or not settings.local_model:
        parser.error("set VLLM_BASE_URL and LOCAL_MODEL in .env (start a GPU first)")
    run(politician_id, model_name)


if __name__ == "__main__":
    main()
