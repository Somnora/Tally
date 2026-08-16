"""Evidence validation as a standing audit, not a one-time check.

The evaluation stage validates citations as it writes them. That is a
snapshot of what was knowable at that moment, and the facts move: bill
metadata arrives and gets corrected, so a vote nobody knew was buried in an
omnibus can turn out to be one. An evaluation that passed in July can be
wrong in September without anything about it changing.

This command re-runs the checks over everything already stored, flips
validated where the answer changed, and demotes any live evaluation that no
longer holds up. Demotion only ever touches is_current, which is the single
column the append-only trigger permits, so the score and reasoning behind an
earlier public verdict survive for audit.

Two checks cannot be re-run and are honestly skipped: whether a cited vote
was in the list the model was shown, and whether the model read the position
correctly. Neither the offered set nor the claimed position is stored, so a
later pass has nothing to compare against. Both are enforced at write time.
Revalidation therefore only ever tightens: it can demote an evaluation, never
promote one.

Run:  uv run python -m pipeline.validate_cli --report
      uv run python -m pipeline.validate_cli --revalidate
      uv run python -m pipeline.validate_cli --revalidate --dry-run
"""

import argparse
from collections import Counter

from pipeline import db, evidence


def run_report() -> None:
    with db.connect() as conn:
        summary = db.evaluation_summary(conn)
        rejects = db.citation_reject_summary(conn)

    if not summary:
        print("No evaluations recorded yet.")
    else:
        print(f"{'prompt':<14} {'model':<24} {'status':<14} "
              f"{'rows':>5} {'current':>8} {'exportable':>11}")
        print("-" * 80)
        totals: Counter[str] = Counter()
        for prompt_version, model_name, status, rows, current, exportable in summary:
            print(f"{prompt_version:<14} {model_name[:24]:<24} {status:<14} "
                  f"{rows:>5} {current:>8} {exportable:>11}")
            totals["rows"] += rows
            totals["current"] += current
            totals["exportable"] += exportable
        print("-" * 80)
        print(f"{'TOTAL':<14} {'':<24} {'':<14} {totals['rows']:>5} "
              f"{totals['current']:>8} {totals['exportable']:>11}")
        if totals["current"]:
            share = totals["exportable"] / totals["current"]
            print(f"\n{totals['exportable']}/{totals['current']} current evaluations "
                  f"are fully cited and validated ({share:.0%}).")

    print("\nRefused citations by reason:")
    if not rejects:
        print("  none recorded")
        return
    for reason, citations, evaluations in rejects:
        print(f"  {reason:<28} {citations:>5} citations across "
              f"{evaluations} evaluations")


def revalidate(
    conn: db.Connection, *, apply_changes: bool = True, verbose: bool = False
) -> Counter[str]:
    """Re-check every stored citation against current facts.

    Only ever tightens. The two write-time checks (was the vote offered, did
    the model read its position correctly) cannot be re-derived, so this pass
    can demote an evaluation but never promote one.
    """
    stored = db.evidence_for_revalidation(conn)
    stats: Counter[str] = Counter()
    stats["citations"] = len(stored)
    if not stored:
        return stats

    facts = db.vote_facts(conn, [c.vote_id for c in stored if c.vote_id is not None])

    # Grouped per evaluation so a status can be re-tested against whatever its
    # citations still support.
    by_evaluation: dict[int, list[db.StoredCitation]] = {}
    for citation in stored:
        by_evaluation.setdefault(citation.evaluation_id, []).append(citation)
    stats["evaluations"] = len(by_evaluation)

    for evaluation_id, citations in by_evaluation.items():
        checks: list[evidence.CitationCheck] = []
        for citation in citations:
            record_id = citation.vote_id if citation.vote_id is not None else -1
            claim = evidence.ClaimedEvidence(
                kind=citation.kind, record_id=record_id, direction=citation.direction,
            )
            check = evidence.check_citation(
                claim, politician_id=citation.politician_id, vote_facts=facts,
            )
            checks.append(check)
            stats[f"citation_{check.reason}"] += 1
            if check.accepted != citation.validated:
                stats["validated_flipped"] += 1
                if verbose:
                    print(f"  evidence {citation.evidence_id}: validated "
                          f"{citation.validated} -> {check.accepted} ({check.reason})")
                if apply_changes:
                    db.set_evidence_validated(conn, citation.evidence_id, check.accepted)

        status = citations[0].status
        supported, reason = evidence.status_is_supported(status, checks)
        if citations[0].is_current and not (supported and all(c.accepted for c in checks)):
            stats["evaluations_demoted"] += 1
            if verbose:
                print(f"  evaluation {evaluation_id}: demoted from current "
                      f"(status {status}, {reason})")
            if apply_changes:
                db.set_evaluation_current(conn, evaluation_id, False)
    return stats


def run_revalidate(dry_run: bool) -> None:
    with db.connect() as conn:
        stats = revalidate(conn, apply_changes=not dry_run, verbose=True)
        if dry_run:
            conn.rollback()
    if not stats["citations"]:
        print("No stored citations to revalidate.")
        return
    print(f"\nRevalidated {stats['citations']} citations across "
          f"{stats['evaluations']} evaluations.")
    print(f"  validated flags changed: {stats['validated_flipped']}")
    print(f"  evaluations demoted:     {stats['evaluations_demoted']}")
    if dry_run:
        print("  (dry run: nothing was written)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Audit evaluation evidence")
    parser.add_argument("--report", action="store_true",
                        help="print evaluation and citation-rejection summary")
    parser.add_argument("--revalidate", action="store_true",
                        help="re-check stored citations against current facts")
    parser.add_argument("--dry-run", action="store_true",
                        help="with --revalidate, report changes without writing")
    args = parser.parse_args()
    if args.revalidate:
        run_revalidate(args.dry_run)
    else:
        run_report()


if __name__ == "__main__":
    main()
