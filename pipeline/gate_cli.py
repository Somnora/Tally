"""Apply the selectivity gate to stored promises and record its verdict.

The gate measured 92% precision against the gold set where ungated
extraction managed 60%, losing one real promise to do it. Until now that
result sat in a module nothing called. This is what connects it to the
product: it screens every verified promise and writes the verdict onto the
row, so app_export_promises can refuse the ones that were never promises.

Read that 92% narrowly. The gold set is 118 rows, all from transcripts, and
official congressional sites now supply the overwhelming majority of stored
promises. The genre carrying the most weight in the product is the one the
precision figure says least about.

Nothing is destroyed. The gate's opinion lives in three columns beside the
promise; the quote, offsets, topic and specificity are untouched, and a
promise the gate drops is still in the database, still reviewable, still
recoverable by rerunning with a rule disabled or by a human review verdict,
which outranks the gate in both directions.

Run:  uv run python -m pipeline.gate_cli --report        # what would change
      uv run python -m pipeline.gate_cli --apply         # screen unscreened
      uv run python -m pipeline.gate_cli --apply --all   # rescreen everything
"""

import argparse
from collections import Counter

from pipeline import db
from pipeline.promise_gate import GATE_VERSION, screen_promise


def run(apply_changes: bool, rescreen_all: bool) -> None:
    with db.connect() as conn:
        pending = db.promises_for_gate(
            conn, gate_version=GATE_VERSION, only_unscreened=not rescreen_all
        )
        if not pending:
            print(f"Nothing to screen: every verified promise already carries "
                  f"a {GATE_VERSION} verdict.")
            return

        stats: Counter[str] = Counter()
        dropped: list[tuple[int, str, str]] = []
        for promise_id, quote in pending:
            decision = screen_promise(quote)
            stats["kept" if decision.keep else "dropped"] += 1
            stats[f"reason_{decision.reason}"] += 1
            if not decision.keep:
                dropped.append((promise_id, decision.reason, quote))
            if apply_changes:
                db.set_gate_verdict(
                    conn, promise_id=promise_id, keep=decision.keep,
                    reason=decision.reason, gate_version=GATE_VERSION,
                )
        if not apply_changes:
            conn.rollback()

    total = len(pending)
    print(f"{GATE_VERSION} screened {total} promises: "
          f"{stats['kept']} kept, {stats['dropped']} dropped "
          f"({stats['dropped'] / total:.0%}).\n")
    print("dropped by rule:")
    for key, count in sorted(stats.items(), key=lambda kv: -kv[1]):
        if key.startswith("reason_") and key != "reason_kept":
            print(f"  {key[len('reason_'):]:<24} {count}")

    if dropped:
        print(f"\nfirst {min(8, len(dropped))} dropped, so the calls are visible:")
        for promise_id, reason, quote in dropped[:8]:
            flat = " ".join(quote.split())
            print(f"  [{promise_id}] {reason:<22} {flat[:74]}")

    if not apply_changes:
        print("\n(report only: nothing was written. Rerun with --apply.)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Screen promises with the selectivity gate")
    parser.add_argument("--report", action="store_true",
                        help="show what would change and write nothing (the default)")
    parser.add_argument("--apply", action="store_true", help="write verdicts")
    parser.add_argument("--all", action="store_true",
                        help="rescreen promises already carrying a verdict")
    args = parser.parse_args()
    run(args.apply, args.all)


if __name__ == "__main__":
    main()
