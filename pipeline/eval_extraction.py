"""Offline scoring harness for extraction selectivity.

Stage 4's problem is not quote accuracy (pipeline/verify.py already settles
that) but SELECTIVITY: extract_v2 pulled out 118 promises of which human
review found only 71 real (60.2% precision). This module scores any
keep-or-drop decision procedure against that labelled set and prints the
whole picture, never just the flattering half:

  leaks_caught        of the 47 non-promises, how many the procedure DROPS
  collateral_damage   of the 71 real promises, how many it wrongly DROPS
  precision           kept_true / (kept_true + kept_false), among rows KEPT
  retention           kept_true / 71

The keep-everything baseline is always printed first, because a precision
number means nothing without the number it has to beat.

Run:
  uv run python -m pipeline.eval_extraction                     # gate vs baseline
  uv run python -m pipeline.eval_extraction --per-rule          # rule-by-rule contribution
  uv run python -m pipeline.eval_extraction --ablate            # leave-one-rule-out sweep
  uv run python -m pipeline.eval_extraction --without hedged_opinion fragment
  uv run python -m pipeline.eval_extraction --min-confidence high
  uv run python -m pipeline.eval_extraction --decisions FILE    # per-row decisions as JSONL

This module touches no database and no network; the gold set is the only
input, so the numbers are free and exactly reproducible.

SCORING A PROMPT. A procedure is Callable[[GoldRow], KeepDecision], so the
deterministic gate is only one implementation. A prompt revision changes what
the model PROPOSES, which this file cannot simulate; scoring extract_v3 for
real means re-running extraction over the same documents on the GPU and
re-labelling the output. The cheap intermediate is a model-backed procedure
that asks, per gold row, "would extract_v3 have extracted this quote?" -- that
is a per-row keep-or-drop decision and plugs in here unchanged: build it in
its own module and register it in PROCEDURES. Whatever the procedure, the
report below stays the same, so prompt and gate results are comparable.
"""

import argparse
import json
from collections.abc import Callable, Collection, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from pipeline.promise_gate import GATE_VERSION, RULE_NAMES, screen_promise

DEFAULT_GOLD = Path("data/review/gold_v2.jsonl")


@dataclass(frozen=True)
class GoldRow:
    """One labelled extraction from the human/triage review pass."""

    promise_id: int
    verbatim_quote: str
    doc_type: str
    topic: str
    specificity: str
    verdict: str
    should_extract: bool
    label_confidence: str
    note: str


class KeepDecision(NamedTuple):
    keep: bool
    reason: str


Procedure = Callable[[GoldRow], KeepDecision]


def load_gold(path: Path) -> list[GoldRow]:
    rows: list[GoldRow] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            raw = json.loads(line)
            rows.append(
                GoldRow(
                    promise_id=int(raw["promise_id"]),
                    verbatim_quote=raw["verbatim_quote"],
                    doc_type=raw.get("doc_type", ""),
                    topic=raw.get("topic", ""),
                    specificity=raw.get("specificity", ""),
                    verdict=raw.get("verdict", ""),
                    should_extract=bool(raw["should_extract"]),
                    label_confidence=raw.get("label_confidence", ""),
                    note=raw.get("note") or "",
                )
            )
    return rows


@dataclass(frozen=True)
class Score:
    """Confusion counts for one procedure over one gold set."""

    name: str
    real_promises: int
    extraction_errors: int
    leaks_caught: int
    collateral_damage: int
    kept_true: int
    kept_false: int
    collateral_ids: tuple[int, ...]
    escaped_ids: tuple[int, ...]

    @property
    def precision(self) -> float:
        kept = self.kept_true + self.kept_false
        return self.kept_true / kept if kept else 0.0

    @property
    def retention(self) -> float:
        return self.kept_true / self.real_promises if self.real_promises else 0.0


def score(rows: Sequence[GoldRow], procedure: Procedure, name: str) -> Score:
    kept_true = kept_false = leaks_caught = collateral = 0
    collateral_ids: list[int] = []
    escaped_ids: list[int] = []
    for row in rows:
        keep = procedure(row).keep
        if row.should_extract:
            if keep:
                kept_true += 1
            else:
                collateral += 1
                collateral_ids.append(row.promise_id)
        else:
            if keep:
                kept_false += 1
                escaped_ids.append(row.promise_id)
            else:
                leaks_caught += 1
    return Score(
        name=name,
        real_promises=sum(1 for r in rows if r.should_extract),
        extraction_errors=sum(1 for r in rows if not r.should_extract),
        leaks_caught=leaks_caught,
        collateral_damage=collateral,
        kept_true=kept_true,
        kept_false=kept_false,
        collateral_ids=tuple(collateral_ids),
        escaped_ids=tuple(escaped_ids),
    )


# -- procedures ----------------------------------------------------------------


def keep_everything(row: GoldRow) -> KeepDecision:
    """The baseline every other number is read against: extract_v2 as shipped."""
    del row
    return KeepDecision(True, "kept")


def gate_procedure(
    disabled_rules: Collection[str] = (), escape_on_commitment: bool = True
) -> Procedure:
    """The deterministic gate, optionally with some rules switched off."""

    def run(row: GoldRow) -> KeepDecision:
        decision = screen_promise(row.verbatim_quote, disabled_rules, escape_on_commitment)
        return KeepDecision(decision.keep, decision.reason)

    return run


# The trap the gate exists to avoid: drop anything containing a hedge phrase,
# with no regard for whether the sentence also commits to something. Scored
# here so "gate on the frame, not the phrase" is a measurement, not a claim.
_NAIVE_HEDGE_PHRASES = (
    "i think", "i believe", "i don't think", "i do not believe", "believes",
    "i feel", "hoping to", "we're looking at",
)


def naive_hedge_stoplist(row: GoldRow) -> KeepDecision:
    lowered = row.verbatim_quote.lower()
    for phrase in _NAIVE_HEDGE_PHRASES:
        if phrase in lowered:
            return KeepDecision(False, "hedge_phrase")
    return KeepDecision(True, "kept")


PROCEDURES: dict[str, Procedure] = {
    "keep-all": keep_everything,
    "gate": gate_procedure(),
    "gate-no-escape": gate_procedure(escape_on_commitment=False),
    "naive-hedge": naive_hedge_stoplist,
}


# -- reporting -----------------------------------------------------------------

_HEADER = (
    f"{'procedure':<32}{'leaks_caught':>13}{'collateral':>12}"
    f"{'precision':>11}{'retention':>11}"
)


def _row(s: Score) -> str:
    return (
        f"{s.name:<32}"
        f"{s.leaks_caught:>8}/{s.extraction_errors:<4}"
        f"{s.collateral_damage:>7}/{s.real_promises:<4}"
        f"{s.precision:>10.1%}"
        f"{s.retention:>11.1%}"
    )


def print_comparison(scores: Iterable[Score]) -> None:
    print(_HEADER)
    print("-" * len(_HEADER))
    for s in scores:
        print(_row(s))


def print_collateral(rows: Sequence[GoldRow], s: Score, procedure: Procedure) -> None:
    """List every real promise the procedure dropped, so a human can judge
    whether the loss was acceptable. This is the guard rail, not a footnote."""
    if not s.collateral_ids:
        print("\ncollateral damage: none")
        return
    by_id = {r.promise_id: r for r in rows}
    print(f"\ncollateral damage ({len(s.collateral_ids)} real promises dropped):")
    for promise_id in s.collateral_ids:
        row = by_id[promise_id]
        print(f"  [{promise_id}] rule={procedure(row).reason} verdict={row.verdict} "
              f"confidence={row.label_confidence}")
        print(f"      {row.verbatim_quote[:220]!r}")


def print_escaped(rows: Sequence[GoldRow], s: Score) -> None:
    """The leaks that got through, grouped by their review verdict: this is
    what a better prompt (or a human) still has to catch."""
    if not s.escaped_ids:
        return
    by_id = {r.promise_id: r for r in rows}
    print(f"\nleaks still getting through ({len(s.escaped_ids)}):")
    for promise_id in s.escaped_ids:
        row = by_id[promise_id]
        print(f"  [{promise_id}] verdict={row.verdict} {row.verbatim_quote[:150]!r}")


def print_per_rule(rows: Sequence[GoldRow]) -> None:
    """What each rule is worth on its own: leaks it catches, real promises it
    costs. A rule that fires on one row is memorisation, not a pattern."""
    print(f"\n{'rule':<24}{'leaks_caught':>13}{'collateral':>12}   dropped ids")
    print("-" * 76)
    for rule in RULE_NAMES:
        only_this = gate_procedure(disabled_rules=[r for r in RULE_NAMES if r != rule])
        s = score(rows, only_this, rule)
        print(f"{rule:<24}{s.leaks_caught:>13}{s.collateral_damage:>12}   "
              f"{','.join(str(i) for i in s.collateral_ids) or '-'}")


def print_ablation(rows: Sequence[GoldRow]) -> None:
    """Leave-one-rule-out: what the full gate loses without each rule."""
    full = score(rows, gate_procedure(), f"{GATE_VERSION} (all rules)")
    print()
    print(_HEADER)
    print("-" * len(_HEADER))
    print(_row(full))
    for rule in RULE_NAMES:
        s = score(rows, gate_procedure(disabled_rules=[rule]), f"  without {rule}")
        print(_row(s))


def write_decisions(rows: Sequence[GoldRow], procedure: Procedure, path: Path) -> None:
    """Per-row decisions as JSONL, for diffing two procedures or for review."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            decision = procedure(row)
            f.write(json.dumps({
                "promise_id": row.promise_id,
                "should_extract": row.should_extract,
                "kept": decision.keep,
                "reason": decision.reason,
                "correct": decision.keep == row.should_extract,
                "verbatim_quote": row.verbatim_quote,
            }, ensure_ascii=False) + "\n")
    print(f"\nWrote {len(rows)} decisions to {path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Score a keep-or-drop procedure for promise extraction against the gold set"
    )
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD,
                        help=f"labelled JSONL (default {DEFAULT_GOLD})")
    parser.add_argument("--procedure", choices=sorted(PROCEDURES), default="gate",
                        help="decision procedure to score (default gate)")
    parser.add_argument("--without", nargs="*", default=[], metavar="RULE",
                        choices=list(RULE_NAMES), help="disable these gate rules")
    parser.add_argument("--min-confidence", choices=["all", "high"], default="all",
                        help="score only rows the reviewer marked high confidence")
    parser.add_argument("--curve", action="store_true",
                        help="score every registered procedure in one table")
    parser.add_argument("--per-rule", action="store_true",
                        help="print each rule's standalone contribution")
    parser.add_argument("--ablate", action="store_true",
                        help="print a leave-one-rule-out sweep")
    parser.add_argument("--decisions", type=Path, metavar="FILE",
                        help="write per-row decisions as JSONL")
    args = parser.parse_args()

    rows = load_gold(args.gold)
    if args.min_confidence == "high":
        rows = [r for r in rows if r.label_confidence == "high"]

    if args.procedure.startswith("gate"):
        escape = args.procedure == "gate"
        procedure: Procedure = gate_procedure(args.without, escape_on_commitment=escape)
        label = GATE_VERSION if escape else f"{GATE_VERSION} (no commitment escape)"
    else:
        procedure = PROCEDURES[args.procedure]
        label = args.procedure
    if args.without:
        label += " -" + ",".join(args.without)

    real = sum(1 for r in rows if r.should_extract)
    print(f"gold: {args.gold}  {len(rows)} rows "
          f"({real} real promises, {len(rows) - real} extraction errors)"
          f"{'  [high-confidence labels only]' if args.min_confidence == 'high' else ''}\n")

    baseline = score(rows, keep_everything, "keep-everything (extract_v2)")
    scored = score(rows, procedure, label)
    if args.curve:
        print_comparison(
            [baseline] + [score(rows, p, n) for n, p in PROCEDURES.items() if n != "keep-all"]
        )
    else:
        print_comparison([baseline, scored])
    print_collateral(rows, scored, procedure)
    print_escaped(rows, scored)

    if args.per_rule:
        print_per_rule(rows)
    if args.ablate:
        print_ablation(rows)
    if args.decisions:
        write_decisions(rows, procedure, args.decisions)

    print("\nNOTE: these rules were written while reading this gold set, so the "
          "numbers are in-sample.\n      They are a development-set ceiling, not a "
          "forecast for new documents.")


if __name__ == "__main__":
    main()
