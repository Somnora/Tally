#!/usr/bin/env bash
# Publish the built page and data snapshot.
#
#   scripts/publish.sh              # preview: checks everything, changes nothing
#   scripts/publish.sh --yes        # actually publish
#   scripts/publish.sh --yes --skip-release   # Pages only
#   scripts/publish.sh --yes --skip-pages     # Release only
#
# TWO DESTINATIONS, BECAUSE THEY DO DIFFERENT JOBS.
#
# Pages serves what a browser reads. Measured 2026-08-17: GitHub Pages returns
# both `accept-ranges: bytes` and `access-control-allow-origin: *`, so the app
# can fetch pages of the SQLite file lazily instead of pulling all 18MB. A
# GitHub RELEASE asset supports ranges too but sends no CORS header at all, so
# a browser on the Pages origin cannot fetch one. That single missing header is
# why Pages hosts the live copy.
#
# Releases hold the versioned archive. Pages is overwritten every run, so
# without this there would be no way to see what we published last month. For a
# transparency project that is not a nicety: a reader who wants to check a
# claim we made in March needs March's data, not today's.
#
# IT NEVER TOUCHES YOUR WORKING TREE. No checkout, no stash, no branch switch.
# The gh-pages commit is assembled with plumbing (hash-object, mktree,
# commit-tree) and pushed straight to the remote ref, so uncommitted work
# cannot be disturbed by running this, and a failure halfway through leaves the
# working tree exactly as it was.
#
# The branch is an ORPHAN, force-pushed each time, so history never
# accumulates. Publishing an 18MB binary weekly into a normal branch would add
# roughly a gigabyte a year of blobs that can never be collected.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SNAPSHOT="dist/tally.sqlite"
MANIFEST="dist/tally.manifest.json"
PAGE="web/index.html"
BRANCH="gh-pages"

CONFIRMED=0
SKIP_PAGES=0
SKIP_RELEASE=0
TAG=""

while [ $# -gt 0 ]; do
    case "$1" in
        --yes)          CONFIRMED=1 ;;
        --skip-pages)   SKIP_PAGES=1 ;;
        --skip-release) SKIP_RELEASE=1 ;;
        --tag)          TAG="${2:?--tag needs a value}"; shift ;;
        -h|--help)      sed -n '2,12p' "$0"; exit 0 ;;
        *)              echo "unknown argument: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '%s\n' "$*"; }
fail() { printf 'REFUSING: %s\n' "$*" >&2; exit 1; }

# -- preflight -----------------------------------------------------------------
# Every check here has cost someone a bad publish somewhere. They are cheap and
# they run before anything leaves this machine.

command -v gh  >/dev/null || fail "the gh CLI is not installed"
gh auth status >/dev/null 2>&1 || fail "gh is not authenticated (run: gh auth login)"

for f in "$SNAPSHOT" "$MANIFEST" "$PAGE"; do
    [ -f "$f" ] || fail "$f is missing. Build it first:
    uv run python -m export.build_snapshot && uv run python -m web.build_page"
done

# The manifest carries the snapshot's sha256. If they disagree, the manifest is
# stale, which means the snapshot was rebuilt and something did not finish, and
# publishing would ship a file that does not match its own checksum.
MANIFEST_SHA="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['sha256'])")"
ACTUAL_SHA="$(shasum -a 256 "$SNAPSHOT" | cut -d' ' -f1)"
[ "$MANIFEST_SHA" = "$ACTUAL_SHA" ] || fail \
    "manifest sha256 does not match $SNAPSHOT.
    manifest: $MANIFEST_SHA
    actual:   $ACTUAL_SHA
    Rebuild: uv run python -m export.build_snapshot"

# A page built BEFORE the snapshot is showing older numbers than the data it
# ships beside. Silent and easy to do: rebuild the snapshot, forget the page.
if [ "$SNAPSHOT" -nt "$PAGE" ]; then
    fail "$PAGE is older than $SNAPSHOT, so the page shows stale numbers.
    Rebuild: uv run python -m web.build_page"
fi

PROMISES="$(python3 -c "import json;print(json.load(open('$MANIFEST'))['row_counts']['promises'])")"
[ "$PROMISES" -gt 0 ] || fail "the snapshot contains no promises; refusing to publish an empty product"

SIZE_MB="$(python3 -c "import json;print(round(json.load(open('$MANIFEST'))['size_bytes']/1048576,1))")"
PAGE_MB="$(python3 -c "import os;print(round(os.path.getsize('$PAGE')/1048576,1))")"

REMOTE_URL="$(git remote get-url origin)"
SLUG="$(printf '%s' "$REMOTE_URL" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')"
# github.io hostnames are lower case regardless of how the account is spelled,
# so printing the owner verbatim would hand out a URL that 404s.
OWNER="$(printf '%s' "${SLUG%%/*}" | tr '[:upper:]' '[:lower:]')"
[ -n "$SLUG" ] || fail "could not read owner/repo from origin ($REMOTE_URL)"

: "${TAG:=data-$(date -u +%Y-%m-%d)}"

# -- what will happen ----------------------------------------------------------

say "repository   $SLUG"
say "snapshot     $SNAPSHOT  ${SIZE_MB}MB  sha256 ${ACTUAL_SHA:0:16}..."
say "page         $PAGE  ${PAGE_MB}MB"
say "rows         $(python3 -c "
import json
rc = json.load(open('$MANIFEST'))['row_counts']
print(', '.join(f'{k} {v}' for k, v in rc.items() if v))")"
say ""
[ "$SKIP_PAGES" = 1 ]   || say "Pages    -> https://${OWNER}.github.io/${SLUG#*/}/  (branch $BRANCH, force-pushed)"
[ "$SKIP_RELEASE" = 1 ] || say "Release  -> tag $TAG, with the snapshot and manifest attached"
say ""

if [ "$CONFIRMED" != 1 ]; then
    say "PREVIEW ONLY. Nothing has been published and nothing was changed."
    say "Re-run with --yes to publish. This makes the data publicly readable."
    exit 0
fi

# -- Pages ---------------------------------------------------------------------

if [ "$SKIP_PAGES" != 1 ]; then
    say "== Pages =="
    git fetch -q origin "$BRANCH" 2>/dev/null || true
    PREV="$(git ls-remote --heads origin "$BRANCH" | cut -f1)"

    # Plumbing, so the working tree is never involved. Each file becomes a blob
    # in the object database directly; nothing is checked out anywhere.
    BLOB_PAGE="$(git hash-object -w "$PAGE")"
    BLOB_SNAP="$(git hash-object -w "$SNAPSHOT")"
    BLOB_MAN="$(git hash-object -w "$MANIFEST")"
    # .nojekyll stops Pages running the build over these files. Without it,
    # Jekyll ignores paths beginning with an underscore and can rewrite others.
    BLOB_NOJEKYLL="$(printf '' | git hash-object -w --stdin)"

    TREE="$(printf '%s\n' \
        "100644 blob $BLOB_NOJEKYLL	.nojekyll" \
        "100644 blob $BLOB_PAGE	index.html" \
        "100644 blob $BLOB_MAN	tally.manifest.json" \
        "100644 blob $BLOB_SNAP	tally.sqlite" | git mktree)"

    # No parent: each publish is a fresh root commit, so the previous 18MB blob
    # becomes unreachable rather than permanent history.
    COMMIT="$(git commit-tree "$TREE" -m "Publish $TAG

snapshot sha256 $ACTUAL_SHA
$PROMISES promises

Built from $(git rev-parse --short HEAD) on $(git rev-parse --abbrev-ref HEAD).
This branch is generated and force-pushed; do not commit to it by hand.")"

    if [ -n "$PREV" ]; then
        # --force-with-lease against the sha we just read: if someone else
        # pushed in between, this fails instead of discarding their work.
        git push -q --force-with-lease="refs/heads/$BRANCH:$PREV" \
            origin "$COMMIT:refs/heads/$BRANCH"
        say "force-pushed $BRANCH  ${PREV:0:8} -> ${COMMIT:0:8}"
    else
        git push -q origin "$COMMIT:refs/heads/$BRANCH"
        say "created $BRANCH at ${COMMIT:0:8}"
    fi

    if ! gh api "repos/$SLUG/pages" >/dev/null 2>&1; then
        say ""
        say "NOTE: GitHub Pages is not enabled for this repository yet, so the"
        say "      branch is pushed but nothing is served. Enable it once:"
        say "        gh api -X POST repos/$SLUG/pages -f source[branch]=$BRANCH -f source[path]=/"
        say "      or Settings > Pages > Source: $BRANCH / root."
    fi
fi

# -- Release -------------------------------------------------------------------

if [ "$SKIP_RELEASE" != 1 ]; then
    say "== Release =="
    if gh release view "$TAG" --repo "$SLUG" >/dev/null 2>&1; then
        # Re-running on the same day should update the day's artefacts rather
        # than fail or silently create a second tag.
        gh release upload "$TAG" "$SNAPSHOT" "$MANIFEST" --repo "$SLUG" --clobber
        say "updated existing release $TAG"
    else
        gh release create "$TAG" "$SNAPSHOT" "$MANIFEST" \
            --repo "$SLUG" \
            --title "Data snapshot $TAG" \
            --notes "$(python3 -c "
import json
m = json.load(open('$MANIFEST'))
rc = m['row_counts']
print(f'''Public data snapshot for cycle {m[\"cycle\"]}, built {m[\"generated_at\"]}.

| table | rows |
|---|---|''')
for k, v in rc.items():
    print(f'| {k} | {v:,} |')
print(f'''
\`tally.sqlite\` is {m[\"size_bytes\"]/1048576:.1f} MB, sha256 \`{m[\"sha256\"]}\`.
Verify with: \`shasum -a 256 tally.sqlite\`

This archive is immutable. The copy the web app reads is on the gh-pages
branch and is overwritten on every publish; this one is here so a claim made
today can still be checked after that copy has moved on.

Methodology: docs/methodology.md in this repository.''')")"
        say "created release $TAG"
    fi
    say "https://github.com/$SLUG/releases/tag/$TAG"
fi

say ""
say "done."
