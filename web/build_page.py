"""Build the district page from the published snapshot.

The page is a single self-contained HTML file: stylesheet, script and data all
inlined. With money loaded for all fifty states the payload is now around a
megabyte, which is the point at which that choice stops being obviously right.
It still beats fetching a SQLite file plus a WASM engine for a reader who only
wants one district, and it compresses to roughly a quarter of its size in
transit. The next step when coverage grows is loading dist/tally.sqlite through
sql.js with range requests, which the snapshot format and its manifest already
exist to support; only this module changes when that day comes.

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
GEO = WEB.parent / "data" / "geo" / "us_cd119.topo.json"
OUT = WEB / "index.html"

# Money now covers every state; promises cover Maine. The page includes any
# candidate a reader could learn something about, and says plainly which kind
# of coverage each district has.


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
        WHERE c.candidacy_id IN (SELECT candidacy_id FROM finance WHERE total_receipts > 0)
           OR c.politician_id IN (SELECT DISTINCT politician_id FROM promises)
        ORDER BY r.state, r.office, r.district, c.party""")
    for candidate in candidates:
        candidate["display_name"] = display_name(candidate["full_name"])

    if not candidates:
        raise RuntimeError(
            "no candidates carry money or promises in the snapshot; "
            "rebuild it with: uv run python -m export.build_snapshot"
        )
    # Money columns are rounded to whole dollars and empty rows dropped. At
    # national scale the page inlines its own data, so every field that
    # survives is paid for in bytes the reader downloads.
    finance = _rows(con, """
        SELECT candidacy_id, politician_id, total_receipts, cash_on_hand,
               pac_contributions_official, individual_itemized_official,
               individual_unitemized, ie_support, ie_oppose
        FROM finance WHERE total_receipts > 0""")
    money_cols = ("total_receipts", "cash_on_hand", "pac_contributions_official",
                  "individual_itemized_official", "individual_unitemized",
                  "ie_support", "ie_oppose")
    for row in finance:
        for key in money_cols:
            row[key] = round(float(row[key] or 0))
    donors = _rows(con, """
        SELECT candidacy_id, committee_name, total_amount, donor_rank
        FROM top_donors WHERE donor_rank <= 3""")
    for row in donors:
        row["total_amount"] = round(float(row["total_amount"] or 0))
    # An incumbent's own voting record. Restricted to people actually shown,
    # so members who are not on this year's ballot cost the reader nothing.
    on_page = {c["politician_id"] for c in candidates}
    record = [r for r in _rows(con, "SELECT * FROM member_record")
              if r["politician_id"] in on_page]
    topics = [r for r in _rows(con, "SELECT * FROM member_topics")
              if r["politician_id"] in on_page]
    votes = [r for r in _rows(con, "SELECT * FROM recent_votes")
             if r["politician_id"] in on_page]
    for row in votes:                      # titles are the bulk of this table
        if row.get("bill_title"):
            row["bill_title"] = row["bill_title"][:96]
        row.pop("vote_question", None)     # filtered to substantive already
        row.pop("policy_area", None)       # the topics table covers this
    for row in candidates:
        row.pop("full_name", None)         # display_name is what the page shows
    return {
        "candidates": candidates,
        "finance": finance,
        "donors": donors,
        "promises": _rows(con, "SELECT * FROM promises"),
        "evaluations": _rows(con, "SELECT * FROM evaluations"),
        "evidence": _rows(con, "SELECT * FROM evidence"),
        "record": record,
        "topics": topics,
        "votes": votes,
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

    # The bulk tables ship column-wise: {cols, rows}. Repeating
    # "pac_contributions_official" across 2,730 rows costs 76 KB in key names
    # alone, and the reader downloads every byte. app.js rehydrates them into
    # ordinary objects at load, so nothing downstream knows the difference.
    for name in ("candidates", "finance", "donors", "votes", "topics", "record"):
        rows = payload[name]
        if not rows:
            payload[name] = {"cols": [], "rows": []}
            continue
        cols = list(rows[0])
        payload[name] = {"cols": cols, "rows": [[r.get(c) for c in cols] for r in rows]}

    n_candidates = len(payload["candidates"]["rows"])
    ci = payload["candidates"]["cols"]
    state_at, pol_at = ci.index("state"), ci.index("politician_id")
    with_promises = {p["politician_id"] for p in payload["promises"]}
    states = len({r[state_at] for r in payload["candidates"]["rows"]})
    researched = len({
        r[state_at] for r in payload["candidates"]["rows"] if r[pol_at] in with_promises
    })
    built_on = manifest["generated_at"][:10]
    css = (WEB / "app.css").read_text(encoding="utf-8")
    # map.js first: it defines renderMap, which app.js calls on every render.
    script = (WEB / "map.js").read_text(encoding="utf-8") + "\n" + \
        (WEB / "app.js").read_text(encoding="utf-8")
    data = json.dumps(payload, separators=(",", ":"), default=str)
    geo = GEO.read_text(encoding="utf-8").strip()

    OUT.write_text(f"""<title>Follow the Money</title>
<style>{css}</style>
<header class="mast"><div class="mast-in">
  <div class="brand">
    <div class="wordmark">Tally<em>.</em></div>
    <p class="tagline">Who is running in your district, where their money comes from,
      what they promised, and how they actually voted. Every claim links to its source.</p>
  </div>
  <div class="scope"><span>2026 cycle</span><b>{states} states</b></div>
</div></header>

<div class="wrap">
  <div class="map-wrap"><div id="map"></div></div>
  <div class="picker">
    <label for="stateSel">State</label>
    <select id="stateSel"></select>
    <span class="hint">A dot marks a seat where promises have been researched.
      Everywhere else shows campaign finance only, so far.</span>
  </div>
  <div class="tabs" id="tabs" role="tablist"></div>
  <div class="race-head" id="raceHead"></div>
  <div class="grid" id="grid"></div>
  <section class="detail" id="detail"></section>

  <footer>
    <p><strong>How to read this.</strong> A promise appears only if its exact words were
    found in the source document, character for character. An alignment verdict appears only
    if every vote it cites was checked in code against the legislator&rsquo;s own record.
    Where the record cannot settle a question, this page says so instead of scoring it.</p>
    <p><strong>Where coverage stands.</strong> Campaign finance is loaded for all
    {states} states: {n_candidates} candidates with FEC filings across {counts['races']} races.
    Promises have been researched in {researched} state so far, because that step means
    reading each candidate&rsquo;s own words rather than downloading a filing. This page
    shows which is which instead of leaving a district looking empty.
    Snapshot built {built_on}: {counts['promises']} displayable promises and
    {counts['evaluations']} alignment verdicts. Money comes from FEC filings; votes link
    to the House Clerk. Identical treatment, identical method, every candidate.</p>
  </footer>
</div>
<script>window.__TALLY_GEO__ = {geo};
window.__TALLY__ = {data};</script>
<script>{script}</script>
""", encoding="utf-8")
    return OUT


if __name__ == "__main__":
    path = build()
    print(f"{path}  {path.stat().st_size // 1024} KB")
