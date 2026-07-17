"""Roll-call vote loaders (Milestone 3).

Chamber-level, incremental sync: House votes via the Congress.gov API,
Senate votes via senate.gov LIS XML. Positions are stored for EVERY current
member (they arrive with each vote fetch regardless), so adding a state to
the pilot never requires refetching votes.

Incremental contract: each sync asks the database for the newest roll call
it already holds (per chamber/congress/session) and fetches only newer
ones. Weekly runs therefore cost a handful of requests, not 1,200.

These functions are called from DBOS steps (pipeline.workflows) in batches,
so a crash resumes at the last completed batch. Each vote's raw payload
(House JSON / Senate XML) becomes the sources row its positions cite.
"""

import hashlib
import json
import logging
from typing import Any

from pipeline import congress_api, db, senate_gov

logger = logging.getLogger(__name__)

_POSITION_MAP = {
    "yea": "yea", "aye": "yea",
    "nay": "nay", "no": "nay",
    "present": "present",
    "not voting": "not_voting",
    "guilty": "guilty", "not guilty": "not_guilty",
}


def normalize_position(raw: str) -> str | None:
    """Map chamber vocabulary (Aye/No/...) onto canonical positions.

    Returns None for non-position votes (e.g. Speaker elections record a
    candidate's name as the 'vote') — those rows are counted and skipped,
    never guessed.
    """
    return _POSITION_MAP.get(" ".join(raw.split()).lower())


def ensure_member_politicians(conn: db.Connection) -> int:
    """Every current member gets a politicians row (bioguide-keyed).

    Members not on the 2026 ballot (senators mid-term) still cast votes and
    still represent constituents; the app needs them. Idempotent.
    """
    members = db.crosswalk_members(conn)
    for bioguide_id, full_name, source_id in members:
        db.upsert_politician_by_bioguide(
            conn, full_name=full_name, party=None, state=None,
            bioguide_id=bioguide_id, source_id=source_id,
        )
    return len(members)


# -- House (Congress.gov API) --------------------------------------------------

def list_new_house_rolls(conn: db.Connection, congress: int, session: int) -> list[int]:
    """Roll-call numbers present upstream but not yet stored, ascending."""
    watermark = db.max_roll_call(conn, "house", congress, session)
    rolls: set[int] = set()
    offset = 0
    while True:
        payload, _url = congress_api.house_vote_page(congress, session, offset)
        votes: list[dict[str, Any]] = payload.get("houseRollCallVotes") or []
        if not votes:
            break
        rolls.update(int(v["rollCallNumber"]) for v in votes)
        offset += congress_api.PAGE_LIMIT
        if offset >= int(payload.get("pagination", {}).get("count") or 0):
            break
    new = sorted(r for r in rolls if r > watermark)
    logger.info("house %d/%d: %d rolls upstream, watermark %d, %d new",
                congress, session, len(rolls), watermark, len(new))
    return new


def load_house_rolls(
    conn: db.Connection, congress: int, session: int, rolls: list[int]
) -> dict[str, int]:
    """Fetch and store member positions for the given House roll calls."""
    bioguide_map, _ = db.member_politician_maps(conn)
    stats = {"votes_loaded": 0, "positions_loaded": 0,
             "skipped_position": 0, "skipped_unknown_member": 0}

    for roll in rolls:
        payload, url = congress_api.house_vote_members(congress, session, roll)
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        source_id = db.insert_source(
            conn, source_type="congress_api_house_vote", url=url,
            content_hash=hashlib.sha256(raw).hexdigest(), raw_payload=raw,
        )
        vote: dict[str, Any] = payload.get("houseRollCallVoteMemberVotes") or {}

        legislation_type = (vote.get("legislationType") or "").strip()
        legislation_number = (vote.get("legislationNumber") or "").strip()
        bill_number = (
            f"{legislation_type} {legislation_number}"
            if legislation_type and legislation_number else None
        )
        start_date = vote.get("startDate") or ""
        voted_at = start_date[:10] or None
        # The clerk's own record is the receipt; API sourceDataURL points at it.
        receipt_url = vote.get("sourceDataURL") or (
            f"https://clerk.house.gov/Votes/{start_date[:4]}{roll}"
        )

        rows: list[dict[str, Any]] = []
        member_results: list[dict[str, Any]] = vote.get("results") or []
        for result in member_results:
            position = normalize_position(str(result.get("voteCast") or ""))
            if position is None:
                stats["skipped_position"] += 1
                continue
            politician_id = bioguide_map.get(str(result.get("bioguideID") or ""))
            if politician_id is None:
                stats["skipped_unknown_member"] += 1
                continue
            rows.append({
                "politician_id": politician_id,
                "congress": congress,
                "chamber": "house",
                "session": session,
                "roll_call_number": roll,
                "bill_number": bill_number,
                "vote_question": vote.get("voteQuestion"),
                "position": position,
                "vote_result": vote.get("result"),
                "voted_at": voted_at,
                "congress_gov_url": receipt_url,
                "source_id": source_id,
            })
        db.upsert_voting_records_bulk(conn, rows)
        stats["votes_loaded"] += 1
        stats["positions_loaded"] += len(rows)
    return stats


# -- Senate (senate.gov LIS XML) -------------------------------------------------

def list_new_senate_numbers(conn: db.Connection, congress: int, session: int) -> list[int]:
    watermark = db.max_roll_call(conn, "senate", congress, session)
    url = senate_gov.menu_url(congress, session)
    xml_bytes = senate_gov.client().get(url)
    db.insert_source(
        conn, source_type="senate_gov_vote_menu", url=url,
        content_hash=hashlib.sha256(xml_bytes).hexdigest(), raw_payload=xml_bytes,
    )
    entries = senate_gov.parse_vote_menu(xml_bytes)
    new = sorted(e.number for e in entries if e.number > watermark)
    logger.info("senate %d/%d: %d votes upstream, watermark %d, %d new",
                congress, session, len(entries), watermark, len(new))
    return new


def load_senate_votes(
    conn: db.Connection, congress: int, session: int, numbers: list[int]
) -> dict[str, int]:
    _, lis_map = db.member_politician_maps(conn)
    stats = {"votes_loaded": 0, "positions_loaded": 0,
             "skipped_position": 0, "skipped_unknown_member": 0}

    for number in numbers:
        url = senate_gov.vote_url(congress, session, number)
        xml_bytes = senate_gov.client().get(url)
        source_id = db.insert_source(
            conn, source_type="senate_gov_vote", url=url,
            content_hash=hashlib.sha256(xml_bytes).hexdigest(), raw_payload=xml_bytes,
        )
        detail = senate_gov.parse_vote_detail(xml_bytes)

        rows: list[dict[str, Any]] = []
        for member in detail.members:
            position = normalize_position(member.vote_cast)
            if position is None:
                stats["skipped_position"] += 1
                continue
            politician_id = lis_map.get(member.lis_id)
            if politician_id is None:
                stats["skipped_unknown_member"] += 1
                continue
            rows.append({
                "politician_id": politician_id,
                "congress": congress,
                "chamber": "senate",
                "session": session,
                "roll_call_number": number,
                "bill_number": detail.document_name,
                "vote_question": detail.question,
                "position": position,
                "vote_result": detail.result,
                "voted_at": detail.voted_at,
                "congress_gov_url": senate_gov.vote_page_url(congress, session, number),
                "source_id": source_id,
            })
        db.upsert_voting_records_bulk(conn, rows)
        stats["votes_loaded"] += 1
        stats["positions_loaded"] += len(rows)
    return stats
