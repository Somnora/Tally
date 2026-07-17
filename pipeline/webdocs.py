"""Campaign-site and Wayback Machine document fetching.

Etiquette: robots.txt is checked per host and honored; requests carry the
project user agent and are globally throttled. Text extraction uses
trafilatura (main-content extraction); pages that yield no meaningful text
(splash pages, donation forms) are skipped by the caller.
"""

import json
import logging
import threading
import time
from dataclasses import dataclass
from functools import cache
from html.parser import HTMLParser
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import httpx
import trafilatura

logger = logging.getLogger(__name__)

USER_AGENT = "tally-civic-transparency/0.1 (nonpartisan transparency project; local ingestion)"
MIN_INTERVAL_SECONDS = 1.5
MAX_ATTEMPTS = 3
CDX_URL = "https://web.archive.org/cdx/search/cdx"

# Path fragments suggesting a policy/positions page, in link-priority order.
ISSUE_KEYWORDS = ("issue", "priorit", "platform", "plan", "agenda", "about", "record")
MIN_TEXT_CHARS = 400  # below this, a page is navigation/donation chrome, not content


class _FetchClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = 0.0
        self._robots: dict[str, robotparser.RobotFileParser] = {}

    def _wait_for_slot(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_allowed:
                    self._next_allowed = now + MIN_INTERVAL_SECONDS
                    return
                wait = self._next_allowed - now
            time.sleep(wait)

    def allowed_by_robots(self, url: str) -> bool:
        host = urlsplit(url).netloc
        if host not in self._robots:
            parser = robotparser.RobotFileParser()
            try:
                response = httpx.get(
                    f"https://{host}/robots.txt", timeout=15,
                    headers={"User-Agent": USER_AGENT}, follow_redirects=True,
                )
                parser.parse(response.text.splitlines() if response.status_code == 200 else [])
            except httpx.HTTPError:
                parser.parse([])  # unreachable robots.txt = no restrictions published
            self._robots[host] = parser
        return self._robots[host].can_fetch(USER_AGENT, url)

    def get(self, url: str, *, check_robots: bool = True) -> bytes | None:
        """Fetch a URL politely; None means denied/failed (caller counts it)."""
        if check_robots and not self.allowed_by_robots(url):
            logger.info("robots.txt disallows %s", url)
            return None
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_slot()
            try:
                response = httpx.get(
                    url, timeout=30, headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.content
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2.0 * attempt)
        logger.warning("giving up on %s: %s", url, last_error)
        return None


@cache
def client() -> _FetchClient:
    return _FetchClient()


class _LinkCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


def extract_text(html_bytes: bytes) -> str | None:
    """Main-content text, or None when the page has no meaningful prose."""
    text = trafilatura.extract(html_bytes.decode("utf-8", errors="replace"))
    if text is None or len(text) < MIN_TEXT_CHARS:
        return None
    return text


def discover_issue_links(html_bytes: bytes, base_url: str, cap: int = 6) -> list[str]:
    """Same-site links that look like policy/positions pages, deduped."""
    collector = _LinkCollector()
    collector.feed(html_bytes.decode("utf-8", errors="replace"))
    base_host = urlsplit(base_url).netloc.removeprefix("www.")

    found: list[str] = []
    seen: set[str] = set()
    for keyword in ISSUE_KEYWORDS:  # keyword-priority order, then page order
        for href in collector.hrefs:
            absolute = urljoin(base_url, href.strip())
            parts = urlsplit(absolute)
            if parts.scheme not in ("http", "https"):
                continue
            if parts.netloc.removeprefix("www.") != base_host:
                continue
            normalized = absolute.split("#", 1)[0].rstrip("/")
            if normalized in seen or normalized.rstrip("/") == base_url.rstrip("/"):
                continue
            if keyword in parts.path.lower():
                seen.add(normalized)
                found.append(normalized)
                if len(found) >= cap:
                    return found
    return found


@dataclass(frozen=True)
class WaybackSnapshot:
    timestamp: str        # YYYYMMDDhhmmss
    archive_url: str
    original_url: str


def earliest_snapshot(url: str, from_year: int = 2025) -> WaybackSnapshot | None:
    """Earliest Wayback capture of a URL in the cycle — catches later scrubbing."""
    cdx_query = httpx.URL(CDX_URL, params={
        "url": url, "output": "json", "from": str(from_year),
        "filter": "statuscode:200", "collapse": "digest", "limit": "1",
    })
    raw = client().get(str(cdx_query), check_robots=False)
    if raw is None:
        return None
    try:
        rows = json.loads(raw)
    except ValueError:
        return None
    if len(rows) < 2:  # first row is the header
        return None
    header, first = rows[0], rows[1]
    fields = dict(zip(header, first, strict=False))
    timestamp, original = str(fields.get("timestamp")), str(fields.get("original"))
    return WaybackSnapshot(
        timestamp=timestamp,
        archive_url=f"https://web.archive.org/web/{timestamp}/{original}",
        original_url=original,
    )
