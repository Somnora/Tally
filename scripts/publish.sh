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
METHOD="docs/methodology.md"
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

for f in "$SNAPSHOT" "$MANIFEST" "$PAGE" "$METHOD"; do
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
    # The methodology ships WITH the page, not just in the repo. Every score
    # links to it, and CLAUDE.md requires that link to work; a verdict whose
    # "how this is produced" 404s is worse than one with no link at all.
    METHOD_HTML="$(mktemp)"
    python3 - "$METHOD" "$METHOD_HTML" <<'PYEOF'
import html, pathlib, re, sys
md = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")

# Blocks, not lines. The source is hard wrapped at about 72 characters, and a
# line-per-element converter turned every wrapped line into its own paragraph
# and pushed the continuation of each bullet out of its own list. The rendered
# page said the right things in the wrong shape, which on the one page that
# exists to explain how the numbers are made is its own kind of inaccuracy.
out, buf, mode, list_tag, in_code = [], [], None, None, False


def flush():
    global buf, mode
    if buf:
        text = html.escape(" ".join(buf))
        out.append(f"<li>{text}</li>" if mode == "li" else f"<p>{text}</p>")
    buf, mode = [], None


def close_list():
    global list_tag
    if list_tag:
        out.append(f"</{list_tag}>")
        list_tag = None


def open_list(tag):
    global list_tag
    if list_tag != tag:
        close_list()
        out.append(f"<{tag}>")
        list_tag = tag


for raw in md.splitlines():
    line = raw.rstrip()
    if line.startswith("```"):
        flush(); close_list()
        in_code = not in_code
        out.append("<pre>" if in_code else "</pre>")
        continue
    if in_code:
        out.append(html.escape(raw)); continue
    if not line.strip():
        flush(); continue
    if line.startswith("|"):
        flush(); close_list()
        cells = [html.escape(c.strip()) for c in line.strip("|").split("|")]
        if set("".join(cells)) <= set("-: "):
            continue
        out.append("<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>")
        continue
    heading = re.match(r"^(#{1,4})\s+(.*)$", line)
    if heading:
        flush(); close_list()
        level = len(heading.group(1))
        out.append(f"<h{level}>{html.escape(heading.group(2))}</h{level}>")
        continue
    bullet = re.match(r"^[-*]\s+(.*)$", line)
    ordered = re.match(r"^\d+\.\s+(.*)$", line)
    if bullet or ordered:
        flush()
        open_list("ul" if bullet else "ol")
        mode = "li"
        buf.append((bullet or ordered).group(1))
        continue
    # An indented line continues whatever block is open; that is what wrapping
    # looks like in the source.
    if mode is not None and raw[:1].isspace():
        buf.append(line.strip()); continue
    if mode is None:
        close_list(); mode = "p"
    buf.append(line.strip())

flush(); close_list()
body = "\n".join(out)
body = re.sub(r"(<tr>.*</tr>)", r"<table>\1</table>", body, flags=re.S, count=1)
body = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", body)
pathlib.Path(sys.argv[2]).write_text(
    "<!doctype html><meta charset=utf-8><title>Tally methodology</title>"
    "<meta name=viewport content='width=device-width,initial-scale=1'>"
    "<style>body{max-width:46rem;margin:2rem auto;padding:0 1.2rem;"
    "font:16px/1.65 ui-sans-serif,system-ui,sans-serif;color:#1a1c1a;background:#fbfbf9}"
    "h1,h2,h3{line-height:1.25;margin:2rem 0 .6rem}h1{margin-top:0}"
    "ul,ol{padding-left:1.3rem}li{margin:.35rem 0}"
    "table{border-collapse:collapse;width:100%;margin:1rem 0}"
    "td{border:1px solid #d8dcd6;padding:.45rem .6rem;font-size:14px;vertical-align:top}"
    "code,pre{font-family:ui-monospace,monospace;font-size:13px}"
    "pre{background:#f1f2ef;padding:.8rem;overflow-x:auto}"
    "a{color:#2f6f4f}</style>\n<a href='./'>&larr; Back to Tally</a>\n" + body,
    encoding="utf-8")
PYEOF
    BLOB_METHOD="$(git hash-object -w "$METHOD_HTML")"
    rm -f "$METHOD_HTML"
    BLOB_SNAP="$(git hash-object -w "$SNAPSHOT")"
    BLOB_MAN="$(git hash-object -w "$MANIFEST")"
    # .nojekyll stops Pages running the build over these files. Without it,
    # Jekyll ignores paths beginning with an underscore and can rewrite others.
    BLOB_NOJEKYLL="$(printf '' | git hash-object -w --stdin)"

    TREE="$(printf '%s\n' \
        "100644 blob $BLOB_NOJEKYLL	.nojekyll" \
        "100644 blob $BLOB_PAGE	index.html" \
        "100644 blob $BLOB_METHOD	methodology.html" \
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
