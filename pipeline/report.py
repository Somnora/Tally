"""Finance verification report for one state.

Prints, per candidate with financial activity: FEC's official numbers, our
loaded itemized sums, and the match ratio between them. The ratio is the
health check for the whole finance pipeline — big divergence means either
FEC's bulk processing lags the candidate's latest filing (expected, closes
on the next weekly run) or a loader bug (like the 22Y refund bug this
report caught during the Maine pilot).

Run:
    uv run python -m pipeline.report --state ME
"""

import argparse
from typing import Any

from pipeline import db

DIVERGENCE_FLAG = 0.15  # flag matches worse than +/- 15%


def money(value: Any) -> str:
    return "-" if value is None else f"${float(value):,.0f}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", required=True)
    parser.add_argument("--cycle", type=int, default=2026)
    args = parser.parse_args()
    state = args.state.upper()

    with db.connect() as conn:
        cur = conn.execute(
            db.load_sql("report_state_finance"), {"state": state, "cycle": args.cycle}
        )
        rows = cur.fetchall()

    print(f"\nFinance report: {state} cycle {args.cycle} ({len(rows)} candidates with activity)\n")
    header = (f"{'candidate':<26} {'race':<10} {'receipts':>13} {'cash':>13} "
              f"{'indiv official':>14} {'indiv loaded':>13} {'match':>6} "
              f"{'PAC':>11} {'IE for':>11} {'IE against':>11}")
    print(header)
    print("-" * len(header))

    flagged: list[str] = []
    for r in rows:
        (full_name, office, district, _party, is_special, receipts, cash,
         indiv_official, indiv_loaded, _refunds, pac,
         ie_support, ie_oppose, _rows, _coverage) = r
        race = f"{office}{'-' + district if district else ''}{' (sp)' if is_special else ''}"
        match = ""
        if indiv_official and indiv_loaded is not None and float(indiv_official) > 0:
            ratio = float(indiv_loaded) / float(indiv_official)
            match = f"{100 * ratio:.0f}%"
            if abs(ratio - 1) > DIVERGENCE_FLAG:
                flagged.append(f"  {full_name}: loaded {money(indiv_loaded)} vs "
                               f"official {money(indiv_official)} ({match})")
        print(f"{full_name[:26]:<26} {race:<10} {money(receipts):>13} {money(cash):>13} "
              f"{money(indiv_official):>14} {money(indiv_loaded):>13} {match:>6} "
              f"{money(pac):>11} {money(ie_support):>11} {money(ie_oppose):>11}")

    if flagged:
        print(f"\nDivergence over {DIVERGENCE_FLAG:.0%} (bulk-file lag or loader bug):")
        for line in flagged:
            print(line)
    print()


if __name__ == "__main__":
    main()
