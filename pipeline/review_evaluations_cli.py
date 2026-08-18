"""Human sign-off for broken-promise verdicts.

WHY ONLY THIS ONE STATUS. "X broke their promise" is the most damaging thing
this product says about a named person, and it has twice come close to being
said wrongly at scale: a dropped bill summary produced verdicts that were
exactly backwards, and evaluate_v3 produced 132 more resting on inverted vote
directions. Both times the only thing between a false accusation and the
public site was somebody noticing in time. Every other status still publishes
automatically; this is not a review queue for the pipeline, it is a stop on
the one verdict that accuses.

A reviewer sees the promise in the member's own words, the model's reasoning,
and every cited vote WITH WHAT THAT BILL ACTUALLY DOES, because approving
"broken, score 15" with nothing else on screen is a rubber stamp.

Run:  uv run python -m pipeline.review_evaluations_cli --list
      uv run python -m pipeline.review_evaluations_cli --approve 42 --by "James"
      uv run python -m pipeline.review_evaluations_cli --reject 42 --by "James" \
          --note "cited vote is a repeal; nay protected the promise"
"""

import argparse
import textwrap
from typing import Any, cast

from pipeline import db


def show(item: dict[str, object]) -> None:
    print(f"\n--- evaluation {item['evaluation_id']} "
          f"| {item['politician']} | {item['topic']} | score {item['score']}")
    print(textwrap.fill(f'PROMISE: "{item["quote"]}"', 78,
                        subsequent_indent="  "))
    print(textwrap.fill(f"MODEL SAYS: {item['reasoning']}", 78,
                        subsequent_indent="  "))
    citations = cast(list[dict[str, Any]], item["citations"])
    if not citations:
        print("  CITES NOTHING (which should be impossible for broken)")
        return
    for c in citations:
        print(f"  [{c.get('direction')}] voted {c.get('position')} on "
              f"{c.get('bill_key')}: {c.get('title')}")
        summary = str(c.get("summary") or "")
        if summary:
            print(textwrap.fill(f"WHAT IT DOES: {summary}", 74,
                                initial_indent="      ", subsequent_indent="      "))
        else:
            print("      WHAT IT DOES: (no summary published; judge with caution)")
        print(f"      {c.get('url')}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--list", action="store_true", help="show what is waiting")
    action.add_argument("--approve", type=int, metavar="ID")
    action.add_argument("--reject", type=int, metavar="ID")
    parser.add_argument("--by", help="who is signing off (recorded)")
    parser.add_argument("--note", help="why, recorded alongside the signature")
    args = parser.parse_args()

    with db.connect() as conn:
        if args.list:
            waiting = db.evaluations_awaiting_review(conn)
            if not waiting:
                print("Nothing awaiting review: no unsigned broken verdicts.")
                return
            print(f"{len(waiting)} broken verdict(s) awaiting a signature. "
                  f"None of them can reach the snapshot until they have one.")
            for item in waiting:
                show(item)
            return

        if not args.by:
            parser.error("--by is required: an approval with no name cannot be audited")
        evaluation_id = args.approve if args.approve is not None else args.reject
        approved = args.approve is not None
        db.record_evaluation_review(
            conn, evaluation_id=evaluation_id, approved=approved,
            reviewed_by=args.by, review_note=args.note,
        )
    print(f"evaluation {evaluation_id}: "
          f"{'approved for publication' if approved else 'rejected and withdrawn'} "
          f"by {args.by}")


if __name__ == "__main__":
    main()
