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

from pipeline import db
from pipeline.config import get_settings
from pipeline.stages.evaluate_promises import (
    MAX_VOTES,
    PROMPT_VERSION,
    build_prompt,
    evaluate_promises,
)


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate promises for one member")
    parser.add_argument("--politician", required=True, help="full name as stored")
    parser.add_argument("--dry-run", action="store_true",
                        help="show the eligible promises and a sample prompt, call no model")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    settings = get_settings()
    model_name = settings.local_model or "unset"

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
