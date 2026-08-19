"""Discover and harvest candidates' own campaign websites.

The gap this closes: document harvesting reached 461 of 4,079 candidates for
2026, and the missing 3,618 were not obscure filers. They were, almost
exactly, everyone not already in Congress. pipeline.etl.official_sites keys on
the congress-legislators roster, which lists sitting members and nobody else,
so a challenger's page showed who funded them and then said nothing about what
they had promised. Incumbents got to speak; their opponents did not.

Where the URL comes from, and why it matters more than the coverage: FEC Form
1 asks a committee for its web address, and the committee answers under
penalty of law. The alternatives were a name search or a third-party
database. A name search is how Sherrod Brown nearly became Shontel Brown in
this codebase once already; here the campaign names its own site and we join
on the committee id, so there is no step at which one candidate's words can
be attributed to another. Measured against 60 real 2026 challengers, 87% had
declared an address, and the rate was identical in the sub-$500k tail --
filing a website is a filing habit, not a function of money.

What it does not reach, stated plainly because the methodology page says it
in public: roughly a quarter of declared addresses are dead domains or
script-rendered shells with no server-side prose, and a handful of hosts
disallow crawling. Every one of those outcomes is written to
candidate_websites.fetch_outcome rather than quietly dropped, so "we have no
promises for this candidate" can always be answered with which of those
happened.

Two passes, separately resumable:

  discover  one OpenFEC call per candidate, reading the declared website off
            each authorized committee; ~4,000 calls, inside one hour of the
            key's rate limit
  harvest   the existing polite crawler over each declared site, storing
            campaign_site documents for extraction

Run:  uv run python -m pipeline.etl.campaign_sites discover --dry-run
      uv run python -m pipeline.etl.campaign_sites discover
      uv run python -m pipeline.etl.campaign_sites harvest --limit 20
"""

import argparse
import hashlib
import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, cast
from urllib.parse import urlsplit, urlunsplit

from pipeline import db, fec_api, webdocs
from pipeline.stages import StageStats
from pipeline.stages.sync_documents import sync_campaign_site

logger = logging.getLogger(__name__)

# Only committees the candidate authorized. An unauthorized committee (a PAC,
# a party arm) may name its own site, and storing that as the candidate's
# would put a spender's words on a candidate's page -- the same misattribution
# that gave one senator $51.5M of party committee money earlier in this
# project.
AUTHORIZED_DESIGNATIONS = frozenset({"P", "A"})

# Values filers type into the box when they have no site. Matched whole, so a
# real domain containing one of these words is untouched.
PLACEHOLDERS = re.compile(r"(?i)^(n/?a|none|no|nil|tbd|pending|not applicable|unknown|[-.]+)$")

# Hosts that are a presence but not a campaign site: a profile, a donation
# form or a list of links, with no issue pages to read and nothing a text
# extractor can pull prose from. Counted and reported rather than stored,
# because a row we will never fetch would show up in coverage as an
# unexplained blank.
#
# Site builders are deliberately NOT here. A campaign on a free wixsite.com or
# godaddysites.com subdomain has a real site with real issue pages, and it
# tends to be a small campaign -- the candidates least likely to be covered
# anywhere else, which is the whole point of this pass. Excluding a host
# because it looks cheap would rebuild the bias by hand.
NON_SITE_HOSTS = (
    "facebook.com", "fb.com", "twitter.com", "x.com", "instagram.com",
    "linkedin.com", "youtube.com", "youtu.be", "tiktok.com", "threads.net",
    "actblue.com", "winred.com", "anedot.com", "donorbox.org", "gofundme.com",
    "linktr.ee",
)


def normalize_url(raw: str) -> str | None:
    """A fetchable URL from a filed value, or None when there is no URL in it.

    Filers write "WWW.EXAMPLE.COM", "https://example.com/", stray whitespace
    and the occasional email address. Scheme and host are lowercased; the path
    is left alone, because paths can be case-sensitive and rewriting one turns
    a working address into a 404.
    """
    text = raw.strip().strip("<>\"'")
    if not text or PLACEHOLDERS.match(text):
        return None
    if "@" in text and "://" not in text:
        return None  # an email address, not a website
    if " " in text or "\t" in text:
        return None  # free text, not an address
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    host = parts.netloc.lower()
    if "." not in host or host.endswith("."):
        return None
    path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), host, path, parts.query, ""))


def is_non_site(url: str) -> bool:
    """A social, fundraising or link-aggregator host rather than a campaign site."""
    host = urlsplit(url).netloc.lower().removeprefix("www.")
    return any(host == bad or host.endswith("." + bad) for bad in NON_SITE_HOSTS)


def websites_from_payload(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
    """(cmte_id, declared_url, normalized_url) for each authorized committee.

    Pure, so the parsing rules are testable without an API key: which
    committees count, what a filed value has to look like to be a URL, and
    which duplicates collapse.
    """
    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    results: list[Any] = payload.get("results") or []
    for item in results:
        if not isinstance(item, dict):
            continue
        committee = cast(dict[str, Any], item)
        if committee.get("designation") not in AUTHORIZED_DESIGNATIONS:
            continue
        cmte_id = committee.get("committee_id")
        declared = committee.get("website")
        if not cmte_id or not declared:
            continue
        normalized = normalize_url(str(declared))
        if normalized is None or normalized in seen:
            continue
        seen.add(normalized)
        out.append((str(cmte_id), str(declared).strip(), normalized))
    return out


# Candidates queried at once. The FEC key's limit is on request RATE, which
# fec_api already enforces globally, so concurrency here only overlaps the
# API's own latency: measured at ~3.4s per response, a sequential pass over
# 4,079 candidates took four hours to send 41 minutes of requests.
DISCOVERY_WORKERS = 8


def _discover_one(
    candidate: db.CandidateForDiscovery, cycle: int
) -> tuple[str, int, int]:
    """Query one candidate; returns (outcome, websites_stored, non_site_seen)."""
    payload, canonical = fec_api.candidate_committees(candidate.fec_candidate_id, cycle)
    found = websites_from_payload(payload)
    usable = [(c, d, u) for c, d, u in found if not is_non_site(u)]
    non_site = len(found) - len(usable)

    with db.connect() as conn:
        if usable:
            raw = json.dumps(payload, sort_keys=True).encode("utf-8")
            source_id = db.insert_source(
                conn, source_type="fec_api_candidate_committees", url=canonical,
                content_hash=hashlib.sha256(raw).hexdigest(), raw_payload=raw,
            )
            for cmte_id, declared, url in usable:
                db.insert_candidate_website(
                    conn, politician_id=candidate.politician_id,
                    fec_candidate_id=candidate.fec_candidate_id, cycle=cycle,
                    cmte_id=cmte_id, url=url, declared_url=declared,
                    source_id=source_id,
                )
        # Written whatever the answer was, including none: the scan record is
        # what makes a rerun skip this candidate and what makes "declared no
        # website" a number we can report rather than infer from an absence.
        db.record_website_scan(conn, candidate.fec_candidate_id, cycle, len(usable))

    if usable:
        return "candidates_with_website", len(usable), non_site
    return ("social_or_donation_only" if non_site else "no_website_declared"), 0, non_site


def discover(
    cycle: int,
    limit: int | None = None,
    rediscover: bool = False,
    workers: int = DISCOVERY_WORKERS,
) -> Counter[str]:
    """Read each candidate's declared website off their authorized committees."""
    stats: Counter[str] = Counter()
    with db.connect() as conn:
        targets = db.candidates_for_website_discovery(conn, cycle, rediscover)
    if limit:
        targets = targets[:limit]
    stats["candidates"] = len(targets)
    logger.info("%d candidates to query across %d workers", len(targets), workers)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_discover_one, candidate, cycle): candidate
            for candidate in targets
        }
        for future in as_completed(futures):
            candidate = futures[future]
            done += 1
            try:
                outcome, stored, non_site = future.result()
            except Exception:
                stats["failed"] += 1
                logger.exception("%s: lookup failed; continuing",
                                 candidate.fec_candidate_id)
                continue
            stats[outcome] += 1
            stats["websites_stored"] += stored
            stats["non_site_urls_skipped"] += non_site
            if done % 200 == 0:
                logger.info("%d/%d queried, %d with a website",
                            done, len(targets), stats["candidates_with_website"])
    return stats


# Sites crawled at once. Politeness is enforced per host inside webdocs, so
# this bounds our own concurrency (sockets, connections), not any one
# server's load: sixteen workers are sixteen different campaigns' servers,
# each still seeing one request every 1.5 seconds.
DEFAULT_WORKERS = 12


EMPTY_STATS: StageStats = {
    "pages_fetched": 0, "pages_failed": 0, "pages_without_content": 0,
    "documents_stored": 0, "probes_missed": 0, "index_children_fetched": 0,
    "press_releases_stored": 0, "pages_tls_unverified": 0,
}


def _harvest_one(site: db.WebsiteToHarvest) -> tuple[str, StageStats]:
    """Crawl one site in its own connection, so one failure rolls back one site."""
    # Asked before crawling, and recorded separately, because "this campaign
    # asked not to be crawled" and "this address does not resolve" are
    # different facts about why we have nothing. Collapsing them would let the
    # coverage page imply a live campaign is a dead one.
    if not webdocs.client().allowed_by_robots(site.url):
        with db.connect() as conn:
            db.record_website_fetch(conn, site.candidate_website_id, "robots_disallowed")
        return "robots_disallowed", dict(EMPTY_STATS)

    with db.connect() as conn:
        result = sync_campaign_site(conn, site.politician_id, site.url)
        site_stats: StageStats = result["stats"]
        if site_stats["documents_stored"]:
            outcome = "documents_stored"
        elif site_stats["pages_fetched"]:
            outcome = "no_content"
        else:
            outcome = "unreachable"
        db.record_website_fetch(conn, site.candidate_website_id, outcome)
    return outcome, site_stats


def harvest(
    cycle: int,
    limit: int | None = None,
    recheck: bool = False,
    workers: int = DEFAULT_WORKERS,
    outcome: str | None = None,
) -> Counter[str]:
    """Crawl each declared site, recording what happened to every one."""
    stats: Counter[str] = Counter()
    with db.connect() as conn:
        targets = db.candidate_websites_to_harvest(conn, cycle, recheck, outcome)
    if limit:
        targets = targets[:limit]
    stats["sites"] = len(targets)
    logger.info("%d sites to harvest across %d workers", len(targets), workers)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(_harvest_one, site): site for site in targets}
        # Results are folded in on this thread, so the counters need no lock.
        for future in as_completed(futures):
            site = futures[future]
            done += 1
            try:
                outcome, site_stats = future.result()
            except Exception:
                stats["sites_failed"] += 1
                logger.exception("%s (%s) failed; continuing", site.name, site.url)
                continue
            # Prefixed, because "documents_stored" is both an outcome for a
            # site and a count of pages within one. Summing them into the same
            # key reported 20 documents from 15.
            stats["site_" + outcome] += 1
            for key, value in site_stats.items():
                stats[key] += value
            logger.info("%d/%d %s: %s (%d pages, %d documents)",
                        done, len(targets), site.name, outcome,
                        site_stats["pages_fetched"], site_stats["documents_stored"])
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("discover", "harvest"))
    parser.add_argument("--cycle", type=int, default=2026)
    parser.add_argument("--limit", type=int, help="stop after N (smoke test)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would run and fetch nothing")
    parser.add_argument("--rediscover", action="store_true",
                        help="re-query candidates whose website we already have")
    parser.add_argument("--recheck", action="store_true",
                        help="re-crawl sites already visited")
    parser.add_argument("--outcome", choices=("documents_stored", "no_content",
                                              "unreachable", "robots_disallowed"),
                        help="re-crawl only sites that last recorded this outcome")
    parser.add_argument("--workers", type=int, default=0,
                        help="tasks at once (per-host and API spacing are unchanged)")
    args = parser.parse_args()
    workers = args.workers or (
        DISCOVERY_WORKERS if args.phase == "discover" else DEFAULT_WORKERS)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if args.dry_run:
        with db.connect() as conn:
            if args.phase == "discover":
                n = len(db.candidates_for_website_discovery(conn, args.cycle, args.rediscover))
                print(f"{n} candidates would be queried "
                      f"(~{n * fec_api.MIN_INTERVAL_SECONDS / 60:.0f} minutes at the "
                      f"throttled rate)")
            else:
                sites = db.candidate_websites_to_harvest(
                    conn, args.cycle, args.recheck, args.outcome)
                print(f"{len(sites)} sites would be crawled")
                for site in sites[:10]:
                    print(f"  {site.name[:34]:<34} {site.url}")
        return

    if args.phase == "discover":
        stats = discover(args.cycle, args.limit, args.rediscover, workers)
    else:
        stats = harvest(args.cycle, args.limit, args.recheck, workers, args.outcome)
    print(f"\n{args.phase} complete")
    for key in sorted(stats):
        print(f"  {key:<28} {stats[key]}")


if __name__ == "__main__":
    main()
