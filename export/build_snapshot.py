"""Postgres to SQLite: build the read-only snapshot the app ships.

The web app is a static client. It downloads one SQLite file and queries it
in the browser, so this file IS the public product: whatever lands here is
what voters see, and nothing else about the database is reachable from the
outside. That makes the export the last gate rather than a plumbing step.

What travels, and what does not:

  travels   races, candidates, finance ROLLUPS, top donor committees,
            displayable promises with a context window, current evaluations
            whose every citation validated, and the cited votes themselves.
  stays     itemized contributions (34 MB of donor names and addresses; the
            app deep-links to fec.gov), raw source payloads (24 MB), whole
            documents, the 344,000-row voting_records table, extraction
            rejects, review verdicts, and anything the app_export_* views
            withhold.

Three refusals are built in, because a snapshot that is merely smaller than
the database is not the same as a snapshot that is safe to publish:

  * it will not build while any exportable promise is unscreened by the
    selectivity gate, since shipping an unscreened promise is the exact
    failure the gate exists to prevent;
  * it fails if the result exceeds the size ceiling, rather than quietly
    shipping something too large for a browser to fetch;
  * every table is written from an app_export_* view or an aggregate, never
    from a base table, so tightening a view tightens the snapshot with it.

The manifest beside the file records the content hash, row counts and build
time, so a client can tell whether it already holds the current snapshot
without downloading it again.

Run:  uv run python -m export.build_snapshot
      uv run python -m export.build_snapshot --out dist/tally.sqlite --cycle 2026
"""

import argparse
import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pipeline import db

# A browser has to fetch this. The blueprint's ceiling is 150 MB; the job
# fails rather than emit something larger, because the failure mode of an
# oversized snapshot is an app that silently never loads.
MAX_SNAPSHOT_BYTES = 150 * 1024 * 1024

# How much text either side of a quote travels, so a reader can see the quote
# was not clipped into a different meaning.
CONTEXT_CHARS = 300

# How much of a bill's summary travels beside a vote. The operative verbs are
# at the front of a CRS summary (repeals, establishes, prohibits), and the
# whole point of carrying it is that a title alone misleads.
SUMMARY_CHARS = 400

# Votes shown per promise. Enough to be representative, few enough that a
# reader actually reads them rather than skimming a wall.
VOTES_PER_PROMISE = 8

SNAPSHOT_FORMAT_VERSION = 2


@dataclass(frozen=True)
class TableSpec:
    """One snapshot table: its name, its query, and how the app looks it up."""

    name: str
    sql_file: str
    params: tuple[str, ...] = ()
    indexes: tuple[str, ...] = ()


TABLES: tuple[TableSpec, ...] = (
    TableSpec("races", "export_races", ("cycle",),
              ("state, office, district",)),
    TableSpec("candidates", "export_candidates", ("cycle",),
              ("race_id", "politician_id", "state, office, district")),
    TableSpec("finance", "export_finance", ("cycle",),
              ("candidacy_id", "politician_id")),
    TableSpec("top_donors", "export_top_donors", (),
              ("candidacy_id",)),
    TableSpec("promises", "export_promises", ("context_chars",),
              ("politician_id", "topic")),
    # The votes beside a promise, carrying no verdict. This is what the app
    # shows in place of a score: the same short list the evaluation stage
    # would have been given, deep-linked, for the reader to judge.
    TableSpec("promise_votes", "export_promise_votes",
              ("summary_chars", "votes_per_promise"),
              ("promise_id",)),
    TableSpec("evaluations", "export_evaluations", (),
              ("promise_id",)),
    TableSpec("evidence", "export_evidence", (),
              ("evaluation_id",)),
    # An incumbent's own record. Without these a sitting member's card can
    # only say what they raised, while their entire voting history sits
    # unused in the database.
    TableSpec("member_record", "export_member_record", ("cycle",),
              ("politician_id",)),
    TableSpec("member_topics", "export_member_topics", ("cycle",),
              ("politician_id",)),
    TableSpec("recent_votes", "export_recent_votes", ("cycle",),
              ("politician_id",)),
)


def _sqlite_type(value: Any) -> str:
    """SQLite is dynamically typed; these affinities are for readability and
    for the app's own sanity, not for enforcement."""
    if isinstance(value, bool):
        return "INTEGER"
    if isinstance(value, int):
        return "INTEGER"
    if isinstance(value, float):
        return "REAL"
    return "TEXT"


def _as_sqlite_value(value: Any) -> Any:
    """Postgres types SQLite has no notion of become text or numbers.

    Dates and decimals are the ones that matter here: a date must survive as
    an ISO string the app can sort lexicographically, and a money amount must
    not silently become a float with a rounding error attached.
    """
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    if isinstance(value, bool):
        return int(value)
    return str(value)


def _copy_table(
    pg: db.Connection, lite: sqlite3.Connection, spec: TableSpec, params: dict[str, Any]
) -> int:
    cur = pg.execute(db.load_sql(spec.sql_file),
                     {k: params[k] for k in spec.params})
    rows = cur.fetchall()
    if cur.description is None:
        raise RuntimeError(f"{spec.sql_file} returned no column description")
    columns = [d[0] for d in cur.description]

    # Column affinities come from the first non-null value seen per column,
    # falling back to TEXT. An empty table still gets a real schema so the
    # app's queries parse rather than erroring on a missing table.
    affinities: list[str] = []
    for index in range(len(columns)):
        affinity = "TEXT"
        for row in rows:
            if row[index] is not None:
                affinity = _sqlite_type(row[index])
                break
        affinities.append(affinity)

    column_ddl = ", ".join(f'"{c}" {a}' for c, a in zip(columns, affinities, strict=True))
    lite.execute(f'CREATE TABLE "{spec.name}" ({column_ddl})')  # noqa: S608
    if rows:
        placeholders = ", ".join("?" for _ in columns)
        lite.executemany(
            f'INSERT INTO "{spec.name}" VALUES ({placeholders})',  # noqa: S608
            [tuple(_as_sqlite_value(v) for v in row) for row in rows],
        )
    for position, index_columns in enumerate(spec.indexes):
        lite.execute(
            f'CREATE INDEX "idx_{spec.name}_{position}" '  # noqa: S608
            f'ON "{spec.name}" ({index_columns})'
        )
    return len(rows)


def build(
    out_path: Path,
    cycle: int,
    context_chars: int = CONTEXT_CHARS,
    conn: db.Connection | None = None,
    max_bytes: int = MAX_SNAPSHOT_BYTES,
) -> dict[str, Any]:
    """Build the snapshot and return its manifest.

    `conn` lets tests drive a scratch database; production opens its own.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.exists():
        out_path.unlink()

    counts: dict[str, int] = {}
    params = {"cycle": cycle, "context_chars": context_chars,
              "summary_chars": SUMMARY_CHARS,
              "votes_per_promise": VOTES_PER_PROMISE}

    own_connection = conn is None
    pg = conn if conn is not None else db.connect()
    try:
        unscreened = db.unscreened_exportable_promises(pg)
        if unscreened:
            raise RuntimeError(
                f"{unscreened} exportable promises have never been screened by the "
                "selectivity gate. Publishing an unscreened promise is the failure "
                "the gate exists to prevent. Run: "
                "uv run python -m pipeline.gate_cli --apply"
            )
        lite = sqlite3.connect(out_path)
        try:
            for spec in TABLES:
                counts[spec.name] = _copy_table(pg, lite, spec, params)
            lite.commit()
            lite.execute("VACUUM")
        finally:
            lite.close()
    finally:
        if own_connection:
            pg.close()

    size_bytes = out_path.stat().st_size
    if size_bytes > max_bytes:
        out_path.unlink()
        raise RuntimeError(
            f"snapshot is {size_bytes / 1024 / 1024:.1f} MB, over the "
            f"{max_bytes / 1024 / 1024:.0f} MB ceiling; refusing to publish"
        )

    digest = hashlib.sha256(out_path.read_bytes()).hexdigest()
    return {
        "format_version": SNAPSHOT_FORMAT_VERSION,
        "cycle": cycle,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "filename": out_path.name,
        "size_bytes": size_bytes,
        "sha256": digest,
        "row_counts": counts,
        "context_chars": context_chars,
    }


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Build the public SQLite snapshot")
    parser.add_argument("--out", type=Path, default=Path("dist/tally.sqlite"))
    parser.add_argument("--cycle", type=int, default=2026)
    parser.add_argument("--context-chars", type=int, default=CONTEXT_CHARS)
    args = parser.parse_args(argv)

    manifest = build(args.out, args.cycle, args.context_chars)
    manifest_path = args.out.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"{args.out}  {manifest['size_bytes'] / 1024:.0f} KB")
    for name, count in manifest["row_counts"].items():
        print(f"  {name:<14} {count:>6}")
    print(f"  sha256 {manifest['sha256'][:16]}...")
    print(f"manifest -> {manifest_path}")


if __name__ == "__main__":
    main()
