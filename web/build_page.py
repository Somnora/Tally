"""Build the district page from the published snapshot.

The page is a single self-contained HTML file: stylesheet, script and data all
inlined. That is a deliberate choice for this stage rather than a shortcut.
The whole Maine pilot is 94 KB of JSON, so fetching a SQLite file and a WASM
engine to query it would cost the reader more bytes than simply handing them
the answer. When coverage grows past roughly a megabyte the read path should
switch to loading dist/tally.sqlite through sql.js with range requests, which
is what the snapshot format and its manifest already exist to support. Only
this module changes when that day comes.

It reads the SNAPSHOT, never Postgres. Whatever the export refused to publish
is therefore invisible here too, and the page cannot accidentally show
something the app_export_* views withheld.

Run:  uv run python -m export.build_snapshot     # refresh the snapshot first
      uv run python web/build_page.py
"""

import json
import sqlite3
from pathlib import Path

WEB = Path(__file__).resolve().parent
SNAPSHOT = WEB.parent / "dist" / "tally.sqlite"
MANIFEST = WEB.parent / "dist" / "tally.manifest.json"
OUT = WEB / "index.html"

# The pilot has data for one state. Widen this when coverage does.
STATE = "ME"


def _rows(con: sqlite3.Connection, sql: str, args: tuple = ()) -> list[dict]:
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args)]


def display_name(stored: str) -> str:
    """FEC files names as "LEPAGE, PAUL"; readers expect "Paul Lepage"."""
    if "," not in stored:
        return stored
    last, first = (part.strip() for part in stored.split(",", 1))
    first = " ".join(
        word.capitalize()
        for word in first.replace("MR.", "").replace("MRS.", "").replace("DR.", "").split()
    )
    return f"{first} {last.capitalize()}".strip()


def collect(con: sqlite3.Connection) -> dict[str, list[dict]]:
    candidates = _rows(con, """
        SELECT c.candidacy_id, c.politician_id, c.full_name, c.party,
               c.incumbent_challenger, r.state, r.office, r.district
        FROM candidates c JOIN races r USING (race_id)
        WHERE r.state = ?
          AND c.politician_id IN (SELECT DISTINCT politician_id FROM promises)
        ORDER BY r.district, c.party""", (STATE,))
    for candidate in candidates:
        candidate["display_name"] = display_name(candidate["full_name"])

    ids = [c["politician_id"] for c in candidates]
    if not ids:
        raise RuntimeError(
            f"no {STATE} candidates carry promises in the snapshot; "
            "rebuild it with: uv run python -m export.build_snapshot"
        )
    marks = ",".join("?" * len(ids))
    args = tuple(ids)
    finance = _rows(
        con, f"SELECT * FROM finance WHERE politician_id IN ({marks})", args
    )
    donors = _rows(con, f"""
        SELECT * FROM top_donors
        WHERE donor_rank <= 5
          AND candidacy_id IN (
              SELECT candidacy_id FROM candidates WHERE politician_id IN ({marks})
          )""", args)
    promises = _rows(
        con, f"SELECT * FROM promises WHERE politician_id IN ({marks})", args
    )
    return {
        "candidates": candidates,
        "finance": finance,
        "donors": donors,
        "promises": promises,
        "evaluations": _rows(con, "SELECT * FROM evaluations"),
        "evidence": _rows(con, "SELECT * FROM evidence"),
    }


def build() -> Path:
    if not SNAPSHOT.exists():
        raise RuntimeError(
            f"{SNAPSHOT} not found. Build it first: "
            "uv run python -m export.build_snapshot"
        )
    con = sqlite3.connect(SNAPSHOT)
    try:
        payload = collect(con)
    finally:
        con.close()
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    counts = manifest["row_counts"]

    n_candidates = len(payload["candidates"])
    built_on = manifest["generated_at"][:10]
    css = (WEB / "app.css").read_text(encoding="utf-8")
    script = (WEB / "app.js").read_text(encoding="utf-8")
    data = json.dumps(payload, separators=(",", ":"), default=str)

    OUT.write_text(f"""<title>Follow the Money</title>
<style>{css}</style>
<header class="mast"><div class="mast-in">
  <div class="brand">
    <div class="wordmark">Tally<em>.</em></div>
    <p class="tagline">Who is running in your district, where their money comes from,
      what they promised, and how they actually voted. Every claim links to its source.</p>
  </div>
  <div class="scope"><span>Pilot data</span><b>Maine &middot; 2026</b></div>
</div></header>

<div class="wrap">
  <div class="tabs" id="tabs" role="tablist"></div>
  <div class="race-head" id="raceHead"></div>
  <div class="grid" id="grid"></div>
  <section class="detail" id="detail"></section>

  <footer>
    <p><strong>How to read this.</strong> A promise appears only if its exact words were
    found in the source document, character for character. An alignment verdict appears only
    if every vote it cites was checked in code against the legislator&rsquo;s own record.
    Where the record cannot settle a question, this page says so instead of scoring it.</p>
    <p><strong>What this is not yet.</strong> This is a pilot covering {n_candidates} Maine
    candidates, not all 435 districts. Snapshot built {built_on} and containing
    {counts['races']} races, {counts['candidates']} candidates,
    {counts['promises']} displayable promises and {counts['evaluations']} alignment
    verdicts. Money figures are FEC filings; votes link to the House Clerk.
    Identical treatment, identical method, every candidate.</p>
  </footer>
</div>
<script>window.__TALLY__ = {data};</script>
<script>{script}</script>
""", encoding="utf-8")
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"{path}  {path.stat().st_size // 1024} KB")
