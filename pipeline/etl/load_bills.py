"""Bill metadata backfill (Milestone 5).

A roll-call row says how a member voted and on which bill number, but
nothing about what the bill DOES. The evaluation stage compares a promise
to a voting record, so without a title and subject terms it has two
options, both bad: call every promise 'unverifiable', or let the model
recall the bill from pretraining. The second invents evidence, which
invariant 2 forbids outright. This loader supplies the missing text from
Congress.gov and stores it with provenance.

Scope is driven by votes, not by the congress: members share roll calls,
so the whole Maine pilot needs a few hundred bills. Nominations (PN rows)
carry no bill_key and never reach this loader.

Run:  uv run python -m pipeline.etl.load_bills --politician "Chellie Pingree"
      uv run python -m pipeline.etl.load_bills --limit 5      # smoke test
      uv run python -m pipeline.etl.load_bills                # every voted bill
"""

import argparse
import hashlib
import json
import logging
import re
from collections import Counter
from datetime import date
from typing import Any, cast

from pipeline import congress_api, db

logger = logging.getLogger(__name__)

# Normalized prefix (from voting_records.bill_key) -> Congress.gov path segment.
# Anything not in this map is refused rather than guessed: a wrong segment
# would silently fetch the wrong bill.
BILL_TYPE_PATHS = {
    "HR": "hr",
    "S": "s",
    "HRES": "hres",
    "SRES": "sres",
    "HJRES": "hjres",
    "SJRES": "sjres",
    "HCONRES": "hconres",
    "SCONRES": "sconres",
}

_TAG = re.compile(r"<[^>]+>")


def split_bill_key(bill_key: str) -> tuple[str, int] | None:
    """'HR-8595' -> ('hr', 8595). None if the key is not a known bill type.

    Pure and total: every caller handles None rather than trusting the
    string, so a stray key can never build an API path.
    """
    prefix, _, number = bill_key.partition("-")
    path = BILL_TYPE_PATHS.get(prefix.upper())
    if path is None or not number.isdigit():
        return None
    return path, int(number)


def strip_html(text: str) -> str:
    """CRS summaries arrive as HTML. Store readable prose, not markup."""
    return " ".join(_TAG.sub(" ", text).split())


def _opt_str(value: Any) -> str | None:
    """Trim an untyped JSON value to a non-empty string, or None."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_date(value: Any) -> date | None:
    """Congress.gov dates are 'YYYY-MM-DD' or an ISO timestamp; both start
    with the date. Anything else yields None rather than a wrong date."""
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def newest_summary(payload: dict[str, Any]) -> str | None:
    """The most recent CRS summary, stripped of markup.

    A bill accumulates summaries as it advances (Introduced, Passed House,
    ...). The newest describes the version members actually voted on.
    """
    summaries: list[Any] = payload.get("summaries") or []
    dated: list[dict[str, Any]] = []
    for item in summaries:
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        if entry.get("text"):
            dated.append(entry)
    if not dated:
        return None
    newest = max(dated, key=lambda s: str(s.get("actionDate") or s.get("updateDate") or ""))
    return strip_html(str(newest["text"])) or None


def subject_terms(payload: dict[str, Any]) -> list[str]:
    """Legislative subject terms, deduplicated and ordered.

    These are what the evaluation stage's vote pre-filter selects on, so a
    bill with no subjects is a bill that can never be matched to a promise
    by topic. That is a real outcome worth seeing, not something to paper
    over with a placeholder.
    """
    raw: Any = payload.get("subjects")
    if not isinstance(raw, dict):
        return []
    subjects = cast(dict[str, Any], raw)
    listed: list[Any] = subjects.get("legislativeSubjects") or []
    terms: set[str] = set()
    for item in listed:
        if not isinstance(item, dict):
            continue
        entry = cast(dict[str, Any], item)
        name = _opt_str(entry.get("name"))
        if name is not None:
            terms.add(name)
    return sorted(terms)


def load_bill(conn: db.Connection, congress: int, bill_key: str) -> bool:
    """Fetch and store one bill. False means it was skipped, not that it failed.

    Three requests per bill (detail, subjects, summaries). At the client's
    ~0.9s spacing that is under three seconds per bill.
    """
    split = split_bill_key(bill_key)
    if split is None:
        logger.warning("unrecognized bill key %r; skipped", bill_key)
        return False
    bill_type, number = split

    try:
        detail_payload, detail_url = congress_api.bill_detail(congress, bill_type, number)
    except congress_api.NotFoundError:
        # A bill number appearing in a roll call but absent from the bill
        # API is a genuine upstream gap. Log it; never invent a title.
        logger.warning("no Congress.gov record for %s (congress %d)", bill_key, congress)
        return False

    bill: dict[str, Any] = detail_payload.get("bill") or {}
    raw = json.dumps(detail_payload, sort_keys=True).encode("utf-8")
    source_id = db.insert_source(
        conn, source_type="congress_api_bill", url=detail_url,
        content_hash=hashlib.sha256(raw).hexdigest(), raw_payload=raw,
    )

    # Both sub-resources are counted inline, so a bill with none costs no
    # extra request.
    subject_ref: dict[str, Any] = bill.get("subjects") or {}
    subjects: list[str] = []
    if subject_ref.get("count"):
        subjects_payload, _ = congress_api.bill_subjects(congress, bill_type, number)
        subjects = subject_terms(subjects_payload)

    summary_ref: dict[str, Any] = bill.get("summaries") or {}
    summary: str | None = None
    if summary_ref.get("count"):
        summaries_payload, _ = congress_api.bill_summaries(congress, bill_type, number)
        summary = newest_summary(summaries_payload)

    sponsors: list[Any] = bill.get("sponsors") or []
    first_sponsor: dict[str, Any] = sponsors[0] if sponsors else {}
    latest: dict[str, Any] = bill.get("latestAction") or {}
    policy_area: dict[str, Any] = bill.get("policyArea") or {}

    db.upsert_bill(
        conn,
        congress=congress,
        bill_key=bill_key,
        bill_type=bill_type,
        bill_number=number,
        title=_opt_str(bill.get("title")),
        policy_area=_opt_str(policy_area.get("name")),
        subjects=subjects,
        summary_text=summary,
        introduced_date=_parse_date(bill.get("introducedDate")),
        latest_action=_opt_str(latest.get("text")),
        latest_action_date=_parse_date(latest.get("actionDate")),
        sponsor_bioguide=_opt_str(first_sponsor.get("bioguideId")),
        congress_gov_url=detail_url,
        source_id=source_id,
    )
    return True


def backfill(
    conn: db.Connection, politician_id: int | None = None, limit: int | None = None
) -> Counter[str]:
    """Fetch every voted-on bill that has no metadata row yet."""
    pending = db.bill_keys_needing_metadata(conn, politician_id)
    if limit is not None:
        pending = pending[:limit]
    stats: Counter[str] = Counter()
    stats["pending"] = len(pending)
    logger.info("%d bills need metadata", len(pending))
    for index, (congress, bill_key) in enumerate(pending, start=1):
        try:
            stored = load_bill(conn, congress, bill_key)
        except Exception:
            # One unavailable bill must not abandon the other 500.
            logger.exception("failed on %s (congress %d)", bill_key, congress)
            stats["failed"] += 1
            continue
        stats["stored" if stored else "skipped"] += 1
        # Commit per bill. The full backfill is a twenty minute network job,
        # so holding one transaction open would mean a stall at minute
        # nineteen throws away every bill fetched so far, and nothing is
        # visible while it runs. Each bill is independent; there is no
        # consistency reason to batch them.
        conn.commit()
        if index % 25 == 0:
            logger.info("%d/%d bills fetched", index, len(pending))
    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill bill metadata from Congress.gov")
    parser.add_argument("--politician", help="restrict to one member's voting record")
    parser.add_argument("--limit", type=int, help="stop after N bills (smoke test)")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    with db.connect() as conn:
        politician_id: int | None = None
        if args.politician:
            politician_id = db.politician_id_by_name(conn, args.politician)
            if politician_id is None:
                parser.error(f"no politician matching {args.politician!r}")
        stats = backfill(conn, politician_id, args.limit)
    print(f"bills pending={stats['pending']} stored={stats['stored']} "
          f"skipped={stats['skipped']} failed={stats['failed']}")


if __name__ == "__main__":
    main()
