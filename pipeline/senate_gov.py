"""Senate roll-call votes from senate.gov LIS XML.

The Congress.gov API does not serve Senate roll calls, so we read the
Senate's own published XML: a per-session menu listing every vote, plus one
XML document per vote with each senator's position (keyed by LIS id, which
id_crosswalk maps to bioguide).

Fetches are throttled and identified (same etiquette as the API clients);
parsers are pure functions over the XML bytes so they can be unit-tested
with fixtures. XML is parsed with the stdlib ElementTree — senate.gov is a
single trusted government source, not arbitrary input.
"""

import logging
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date, datetime
from functools import cache

import httpx

logger = logging.getLogger(__name__)

LIS_BASE = "https://www.senate.gov/legislative/LIS"
USER_AGENT = "tally-civic-transparency/0.1 (nonpartisan transparency project; local ingestion)"
MIN_INTERVAL_SECONDS = 0.5
MAX_ATTEMPTS = 4


def menu_url(congress: int, session: int) -> str:
    return f"{LIS_BASE}/roll_call_lists/vote_menu_{congress}_{session}.xml"


def vote_url(congress: int, session: int, number: int) -> str:
    return (f"{LIS_BASE}/roll_call_votes/vote{congress}{session}/"
            f"vote_{congress}_{session}_{number:05d}.xml")


def vote_page_url(congress: int, session: int, number: int) -> str:
    """Human-readable receipt page for a Senate roll call."""
    return (f"{LIS_BASE}/roll_call_votes/vote{congress}{session}/"
            f"vote_{congress}_{session}_{number:05d}.htm")


class SenateGovClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def _wait_for_slot(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_allowed:
                    self._next_allowed = now + MIN_INTERVAL_SECONDS
                    return
                wait = self._next_allowed - now
            time.sleep(wait)

    def get(self, url: str) -> bytes:
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_slot()
            try:
                response = httpx.get(
                    url, timeout=30, follow_redirects=True,
                    headers={"User-Agent": USER_AGENT},
                )
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("senate.gov attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
                time.sleep(2.0 * attempt)
        raise RuntimeError(
            f"senate.gov request failed after {MAX_ATTEMPTS} attempts: {url}"
        ) from last_error


@cache
def client() -> SenateGovClient:
    return SenateGovClient()


# -- parsers (pure) -----------------------------------------------------------

@dataclass(frozen=True)
class SenateMenuEntry:
    number: int
    result: str | None
    issue: str | None      # e.g. 'S.J.Res. 198'


@dataclass(frozen=True)
class SenateMemberPosition:
    lis_id: str
    vote_cast: str


@dataclass(frozen=True)
class SenateVoteDetail:
    number: int
    question: str | None
    result: str | None
    document_name: str | None   # bill / nomination the vote concerns
    voted_at: date | None
    members: list[SenateMemberPosition]


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    stripped = " ".join(element.text.split())  # menu XML pads with newlines
    return stripped or None


def parse_vote_menu(xml_bytes: bytes) -> list[SenateMenuEntry]:
    root = ET.fromstring(xml_bytes)
    entries: list[SenateMenuEntry] = []
    for vote in root.iter("vote"):
        number = _text(vote.find("vote_number"))
        if number is None:
            continue
        entries.append(SenateMenuEntry(
            number=int(number),
            result=_text(vote.find("result")),
            issue=_text(vote.find("issue")),
        ))
    return entries


def _parse_vote_date(raw: str | None) -> date | None:
    if raw is None:
        return None
    try:
        return datetime.strptime(raw, "%B %d, %Y, %I:%M %p").date()
    except ValueError:
        return None


def parse_vote_detail(xml_bytes: bytes) -> SenateVoteDetail:
    root = ET.fromstring(xml_bytes)
    members = [
        SenateMemberPosition(lis_id=lis, vote_cast=cast)
        for member in root.iter("member")
        if (lis := _text(member.find("lis_member_id"))) is not None
        and (cast := _text(member.find("vote_cast"))) is not None
    ]
    number = _text(root.find("vote_number"))
    return SenateVoteDetail(
        number=int(number) if number is not None else -1,
        question=_text(root.find("question")),
        result=_text(root.find("vote_result")),
        document_name=_text(root.find("document/document_name")),
        voted_at=_parse_vote_date(_text(root.find("vote_date"))),
        members=members,
    )
