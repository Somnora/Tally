#!/usr/bin/env bash
# The weekly data refresh, and deliberately not the whole pipeline.
#
#   scripts/weekly_refresh.sh            # refresh and publish
#   scripts/weekly_refresh.sh --dry-run  # do the work, publish nothing
#
# WHAT IT DOES: FEC finance, Congress.gov roll calls, official-site harvest,
# the selectivity gate, then rebuild and publish. All of it free apart from
# politeness, and all of it the data that actually goes stale week to week.
#
# WHAT IT DELIBERATELY DOES NOT DO, and why the split matters more than the
# schedule:
#
#   EXTRACTION needs a GPU. An unattended job that rents one is an unattended
#   job that spends money, so newly harvested documents wait here and the run
#   reports how many are pending. That number is the prompt to do a deliberate
#   GPU pass, not a silent charge.
#
#   EVALUATION is worse than merely expensive. Its most valuable output is the
#   broken-promise verdict, which cannot publish without a human signature,
#   and on the last triage roughly nine in ten of those should never publish
#   at all. Scheduling it weekly would spend real money to grow a review queue
#   nobody is reading. It stays a decision someone makes on purpose.
#
# So this keeps the evidence current and leaves every judgment manual. When
# the scoring is good enough that its output can be trusted unattended, that
# is the moment to revisit this comment, not before.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT" || { echo "cannot cd to $REPO_ROOT"; exit 1; }

DRY_RUN=0
[ "${1:-}" = "--dry-run" ] && DRY_RUN=1

LOG_DIR="${TALLY_LOG_DIR:-$HOME/.tally-logs}"
mkdir -p "$LOG_DIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/refresh-$STAMP.log"

exec > >(tee -a "$LOG") 2>&1
echo "== Tally weekly refresh $STAMP =="

failed=()
step() {
    local name="$1"; shift
    echo; echo "-- $name"
    # A failing step is recorded and the run continues. Losing this week's
    # roll calls must not also cost the finance update, and a partial refresh
    # that says so is better than no refresh that says nothing.
    if ! "$@"; then
        echo "STEP FAILED: $name"
        failed+=("$name")
    fi
}

step "finance (FEC, all states)"      uv run python -m pipeline.workflows finance --state ALL
step "votes (Congress.gov, 119th)"    uv run python -m pipeline.workflows votes --congress 119
step "official-site harvest"          uv run python -m pipeline.etl.official_sites
step "selectivity gate"               uv run python -m pipeline.gate_cli --apply

PENDING="$(psql civic -qAt -c "SELECT count(*) FROM documents WHERE extracted_at IS NULL" 2>/dev/null || echo '?')"

echo; echo "-- rebuild"
if ! uv run python -m export.build_snapshot; then
    echo "SNAPSHOT BUILD FAILED - refusing to publish"
    failed+=("snapshot")
elif ! uv run python -m web.build_page; then
    echo "PAGE BUILD FAILED - refusing to publish"
    failed+=("page")
elif [ "$DRY_RUN" = 1 ]; then
    echo; echo "DRY RUN: built but published nothing."
else
    echo; echo "-- publish"
    # publish.sh carries its own refusals (manifest hash, page staleness,
    # empty snapshot); this does not second-guess them.
    step "publish" ./scripts/publish.sh --yes
fi

echo
echo "== summary =="
echo "  documents awaiting extraction: $PENDING  (needs a deliberate GPU pass)"
if [ ${#failed[@]} -gt 0 ]; then
    echo "  FAILED STEPS: ${failed[*]}"
    echo "  log: $LOG"
    exit 1
fi
echo "  all steps completed"
echo "  log: $LOG"
