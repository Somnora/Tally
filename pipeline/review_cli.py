"""Human-review CLI for extracted promises.

Shows each unreviewed promise with surrounding document context and records
a verdict. The point is a real precision number per prompt_version before
any prompt v3 work, and a human-confirmed set for the evaluation stage.

Run:  uv run python -m pipeline.review_cli                    # review queue
      uv run python -m pipeline.review_cli --report           # verdict summary
      uv run python -m pipeline.review_cli --export FILE      # queue -> JSONL for a triage agent
      uv run python -m pipeline.review_cli --import-triage FILE  # agent verdicts -> DB

Keys: y correct / o opinion / f fragment / n not a promise
      s wrong specificity / t wrong topic / k skip / q quit
Any verdict may be followed by a note when prompted.

Triage flow: another agent labels the export; only rows it marks
confidence=high are imported (tagged "triage:<agent>" in the note), and
its unsure rows stay in the human queue.
"""

import argparse
import json
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

from pipeline import db

VERDICT_KEYS = {
    "y": "correct",
    "o": "opinion",
    "f": "fragment",
    "n": "not_a_promise",
    "s": "wrong_specificity",
    "t": "wrong_topic",
}

# The label-only verdicts still count as real promises when computing precision.
REAL_PROMISE_VERDICTS = {"correct", "wrong_specificity", "wrong_topic"}


def _wrap(text: str, indent: str = "    ") -> str:
    return textwrap.fill(" ".join(text.split()), width=88, initial_indent=indent,
                         subsequent_indent=indent)


def _show(item: db.ReviewItem, position: int, total: int) -> None:
    title = item.doc_title or item.url
    print(f"\n[{position}/{total}] {item.politician_name} | {item.doc_type} | {title}")
    print(f"  topic={item.topic}  specificity={item.specificity}  scoreable={item.is_scoreable}")
    print(f"  {item.url}")
    if item.context_before:
        print(_wrap("... " + item.context_before))
    print(_wrap(">>> " + item.verbatim_quote + " <<<", indent="  "))
    if item.context_after:
        print(_wrap(item.context_after + " ..."))


def _prompt_verdict() -> tuple[str | None, str | None]:
    """Return (verdict, note); verdict None means skip, 'quit' handled by caller."""
    while True:
        raw = input("  [y]es [o]pinion [f]ragment [n]ot-promise [s]pecificity [t]opic "
                    "[k]skip [q]uit > ").strip().lower()
        if raw == "q":
            return "quit", None
        if raw == "k":
            return None, None
        if raw in VERDICT_KEYS:
            verdict = VERDICT_KEYS[raw]
            note = input("  note (enter for none) > ").strip() or None
            return verdict, note
        print("  unrecognized key")


def run_review() -> None:
    with db.connect() as conn:
        queue = db.promises_for_review(conn)
    if not queue:
        print("Nothing left to review.")
        return
    print(f"{len(queue)} promises awaiting review.")
    for i, item in enumerate(queue, start=1):
        _show(item, i, len(queue))
        verdict, note = _prompt_verdict()
        if verdict == "quit":
            print("Stopped; progress is saved per verdict.")
            return
        if verdict is None:
            continue
        # One connection per verdict so a mid-session ctrl-C loses nothing.
        with db.connect() as conn:
            db.upsert_promise_review(
                conn, promise_id=item.promise_id, verdict=verdict, note=note,
                prompt_version=item.prompt_version, model_name=item.model_name,
            )
    print("Review queue complete.")


def run_report() -> None:
    with db.connect() as conn:
        rows = db.review_summary(conn)
    if not rows:
        print("No reviews recorded yet.")
        return
    by_version: dict[str, dict[str, int]] = {}
    for prompt_version, verdict, n in rows:
        by_version.setdefault(prompt_version, {})[verdict] = n
    for prompt_version, verdicts in by_version.items():
        total = sum(verdicts.values())
        real = sum(n for v, n in verdicts.items() if v in REAL_PROMISE_VERDICTS)
        print(f"\n{prompt_version}: {total} reviewed")
        for verdict, n in sorted(verdicts.items(), key=lambda kv: -kv[1]):
            print(f"  {verdict:<18} {n:>4}  ({n / total:.0%})")
        print(f"  precision (real promises / reviewed): {real}/{total} = {real / total:.0%}")


def run_export(path: Path) -> None:
    """Dump the unreviewed queue as JSONL for an external triage agent."""
    with db.connect() as conn:
        queue = db.promises_for_review(conn)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for item in queue:
            f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")
    print(f"Exported {len(queue)} unreviewed promises to {path}")


def run_import_triage(path: Path, agent: str) -> None:
    """Import an agent's verdicts. Only confidence=high rows are recorded;
    unsure rows are left for the human queue. Never overwrites an existing
    verdict (a human verdict always wins)."""
    with db.connect() as conn:
        pending = {item.promise_id: item for item in db.promises_for_review(conn)}
    imported = unsure = skipped = bad = 0
    with path.open(encoding="utf-8") as f, db.connect() as conn:
        for line_no, line in enumerate(f, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            promise_id = int(row["promise_id"])
            verdict = row["verdict"]
            confidence = row.get("confidence", "high")
            if promise_id not in pending:
                skipped += 1  # already reviewed, or unknown id
                continue
            if verdict not in VERDICT_KEYS.values():
                print(f"  line {line_no}: unknown verdict {verdict!r}, skipping")
                bad += 1
                continue
            if confidence != "high":
                unsure += 1
                continue
            item = pending[promise_id]
            note = f"triage:{agent}"
            if row.get("note"):
                note += f" {row['note']}"
            db.upsert_promise_review(
                conn, promise_id=promise_id, verdict=verdict, note=note,
                prompt_version=item.prompt_version, model_name=item.model_name,
            )
            imported += 1
    print(f"Imported {imported} verdicts from {agent}; {unsure} unsure left in "
          f"human queue; {skipped} skipped (already reviewed/unknown); {bad} malformed.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Review extracted promises")
    parser.add_argument("--report", action="store_true", help="print verdict summary and exit")
    parser.add_argument("--export", type=Path, metavar="FILE",
                        help="write unreviewed queue as JSONL and exit")
    parser.add_argument("--import-triage", type=Path, metavar="FILE",
                        help="import an external agent's verdict JSONL and exit")
    parser.add_argument("--agent", default="gemini", help="triage agent name for tagging")
    args = parser.parse_args()
    if args.report:
        run_report()
    elif args.export:
        run_export(args.export)
    elif args.import_triage:
        run_import_triage(args.import_triage, args.agent)
    else:
        try:
            run_review()
        except (KeyboardInterrupt, EOFError):
            print("\nStopped; progress is saved per verdict.")
            sys.exit(0)


if __name__ == "__main__":
    main()
