"""Harvest sitting members' official congressional websites.

This is the promise source that needs no API quota. YouTube discovery costs
100 units per search against a 10,000/day budget, which puts a national pass
weeks away; house.gov and senate.gov cost nothing but politeness, and the
roster is already on disk in the legislators file we use for the ID
crosswalk. 536 members carry an official URL there and every one of them
matches a politician row.

It also aims at the right people. A sitting member is the only kind of
candidate who has a roll-call record, so they are the only kind whose stated
positions can be checked against votes at all. Promises plus votes is the
whole product; harvesting challengers first would produce material nothing
can yet be compared against.

What this is NOT: a campaign promise source. These pages are published by a
congressional office under rules restricting campaign content, so everything
lands as doc_type 'official_site' and stays distinguishable from
'campaign_site' all the way to the public snapshot.

Etiquette comes from pipeline.webdocs: robots.txt is checked per host and
honoured, requests are spaced 1.5 seconds apart, and the User-Agent names the
project. At up to ~20 pages a member (homepage, issue pages and their index
children, a handful of recent press releases) this is a slow, unattended run
by design; going faster would mean hammering congressional web servers to
save an afternoon.

Run:  uv run python -m pipeline.etl.official_sites --dry-run
      uv run python -m pipeline.etl.official_sites
      uv run python -m pipeline.etl.official_sites --limit 5
"""

import argparse
import logging
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import yaml

from pipeline import db
from pipeline.stages.sync_documents import sync_official_site

logger = logging.getLogger(__name__)

LEGISLATORS = Path("data/raw/legislators-current.yaml")


@dataclass(frozen=True)
class Member:
    bioguide: str
    politician_id: int
    name: str
    url: str


def roster(conn: db.Connection, cycle: int, only_candidates: bool) -> list[Member]:
    """Sitting members with an official URL, matched to our politician rows.

    only_candidates restricts to people actually on this cycle's ballot, which
    is the useful default: a member who is retiring has a voting record but no
    race for a reader to look up.
    """
    raw: list[Any] = yaml.safe_load(LEGISLATORS.read_text(encoding="utf-8"))
    by_bioguide: dict[str, tuple[str, str]] = {}
    for item in raw:
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        terms: list[Any] = entry.get("terms") or []
        ids = cast(dict[str, Any], entry.get("id") or {})
        names = cast(dict[str, Any], entry.get("name") or {})
        bioguide = ids.get("bioguide")
        url = cast(dict[str, Any], terms[-1]).get("url") if terms else None
        if bioguide and url:
            name = names.get("official_full") or names.get("last") or bioguide
            by_bioguide[str(bioguide)] = (str(name), str(url))

    known = {
        str(r[0]): int(r[1])
        for r in conn.execute(
            "SELECT bioguide_id, politician_id FROM politicians WHERE bioguide_id IS NOT NULL"
        ).fetchall()
    }
    on_ballot = {
        str(r[0])
        for r in conn.execute(
            "SELECT DISTINCT p.bioguide_id FROM politicians p "
            "JOIN candidacies c USING (politician_id) JOIN races r USING (race_id) "
            "WHERE r.cycle = %s AND p.bioguide_id IS NOT NULL",
            (cycle,),
        ).fetchall()
    }
    members = [
        Member(bioguide, known[bioguide], name, url)
        for bioguide, (name, url) in sorted(by_bioguide.items())
        if bioguide in known and (not only_candidates or bioguide in on_ballot)
    ]
    return members


def already_harvested(conn: db.Connection) -> set[int]:
    """Members who already have official-site documents, so a rerun resumes."""
    return {
        int(r[0])
        for r in conn.execute(
            "SELECT DISTINCT politician_id FROM documents WHERE doc_type = 'official_site'"
        ).fetchall()
    }


def members_without_promises(conn: db.Connection) -> set[int]:
    """Members whose harvest produced no promises: the crawler's known misses.

    The gap the improved crawl exists to close. These members HAVE documents,
    so the resume logic would skip every one of them; re-fetching is safe
    because documents upsert on content hash, meaning an unchanged page is a
    no-op and only genuinely new pages arrive as new rows awaiting extraction.
    """
    return {
        int(r[0])
        for r in conn.execute(
            "SELECT politician_id FROM politicians p WHERE NOT EXISTS "
            "(SELECT 1 FROM promises pr WHERE pr.politician_id = p.politician_id)"
        ).fetchall()
    }


def harvest(members: list[Member], resume: bool = True) -> Counter[str]:
    stats: Counter[str] = Counter()
    stats["members"] = len(members)
    done: set[int] = set()
    if resume:
        with db.connect() as conn:
            done = already_harvested(conn)

    todo = [m for m in members if m.politician_id not in done]
    stats["skipped_already_done"] = len(members) - len(todo)
    logger.info("%d members to harvest (%d already done)", len(todo), stats["skipped_already_done"])

    for index, member in enumerate(todo, start=1):
        # One connection per member: a run this long must not hold a single
        # transaction open, and a member that fails must not roll back the
        # ones already stored.
        try:
            with db.connect() as conn:
                result = sync_official_site(conn, member.politician_id, member.url)
            for key, value in result["stats"].items():
                stats[key] += value
            if result["stats"]["documents_stored"]:
                stats["members_with_documents"] += 1
            else:
                stats["members_without_documents"] += 1
            logger.info("%d/%d %s: %d pages, %d documents",
                        index, len(todo), member.name,
                        result["stats"]["pages_fetched"],
                        result["stats"]["documents_stored"])
        except Exception:
            stats["members_failed"] += 1
            logger.exception("%s (%s) failed; continuing", member.name, member.url)
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cycle", type=int, default=2026)
    parser.add_argument("--limit", type=int, help="stop after N members (smoke test)")
    parser.add_argument("--all-members", action="store_true",
                        help="include members not on this cycle's ballot")
    parser.add_argument("--dry-run", action="store_true",
                        help="list what would be harvested and fetch nothing")
    parser.add_argument("--no-resume", action="store_true",
                        help="re-harvest members that already have documents")
    parser.add_argument("--only-gaps", action="store_true",
                        help="only members whose harvest produced no promises "
                             "(implies re-fetching; unchanged pages are no-ops)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with db.connect() as conn:
        members = roster(conn, args.cycle, only_candidates=not args.all_members)
        if args.only_gaps:
            gaps = members_without_promises(conn)
            members = [m for m in members if m.politician_id in gaps]
    if args.limit:
        members = members[: args.limit]

    if args.dry_run:
        print(f"{len(members)} members would be harvested "
              f"(up to ~{len(members) * 20} page fetches, "
              f"~{len(members) * 20 * 1.5 / 60:.0f} minutes worst case)")
        for member in members[:10]:
            print(f"  {member.name[:34]:<34} {member.url}")
        if len(members) > 10:
            print(f"  ... and {len(members) - 10} more")
        return

    stats = harvest(members, resume=not (args.no_resume or args.only_gaps))
    print("\nofficial-site harvest complete")
    for key in sorted(stats):
        print(f"  {key:<28} {stats[key]}")


if __name__ == "__main__":
    main()
