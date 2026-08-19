"""Campaign-site and Wayback Machine document fetching.

Etiquette: robots.txt is checked per host and honored; requests carry the
project user agent and are spaced per host, so no single server sees traffic
faster than one request every 1.5 seconds no matter how many are in flight
across the internet. Text extraction uses
trafilatura (main-content extraction); pages that yield no meaningful text
(splash pages, donation forms) are skipped by the caller.
"""

import json
import logging
import ssl
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

# Fragments suggesting a policy/positions page, in link-priority order. They
# are matched against the URL path AND the anchor text, because official-site
# CMSes routinely label the link a reader would want ("Votes and Legislation")
# with a path no keyword list could guess.
ISSUE_KEYWORDS = ("issue", "priorit", "platform", "plan", "agenda", "legislat",
                  "about", "record")

# Path segments that are never policy content, excluded even when a keyword
# matches. Without this, "about" pulls in staff pages, district maps and event
# calendars, and on a house.gov site those fill the whole link budget before a
# single issue page is reached: a member whose only substantive pages sat
# behind an /issues dropdown got five pages of logistics instead. Measured on
# real gap-member sites (begich, guest, timmoore, cammack.house.gov,
# 2026-08-17), not guessed.
JUNK_SEGMENTS = frozenset({
    "contact", "services", "offices", "office-locations", "events",
    "committees-and-caucuses", "staff-page", "our-district", "press-kit",
    "newsletter-subscribe", "newsletter-unsubscribe", "survey", "internships",
    "tours-and-tickets", "flags", "grant-applicants", "help-federal-agency",
    "commendations-and-greetings", "art-competition",
    "congressional-app-challenge", "military-academy-nominations",
    "request-an-appearance", "website-problem", "rss.xml", "privacy",
    "copyright", "accessibility", "terms-conditions",
})

# Excluded from ISSUE discovery only. Press-release slugs are dense with
# keyword bait ("...leads-legislation-establish-strategic-bitcoin-reserve"),
# so without this they leak into the issue budget and arrive mislabelled; the
# dedicated press walk owns them and labels them press_release. "in-the-news"
# is third-party coverage: reported speech about the member, which quote
# verification would reject as the member's own words anyway.
NOT_ISSUE_SEGMENTS = frozenset({"press-releases", "in-the-news", "editorial"})

MIN_TEXT_CHARS = 400  # below this, a page is navigation/donation chrome, not content


def is_certificate_error(exc: Exception) -> bool:
    """Whether a connection failure was the certificate rather than the host.

    A campaign whose host serves an incomplete certificate chain is live and
    a voter can read it: browsers repair the chain themselves, strict clients
    do not. Telling that apart from a dead domain is the difference between
    reporting a candidate as silent and reading what they said.
    """
    seen: set[int] = set()
    cause: BaseException | None = exc
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, ssl.SSLCertVerificationError):
            return True
        cause = cause.__cause__ or cause.__context__
    return "CERTIFICATE_VERIFY_FAILED" in str(exc).upper()


def throttle_key(url: str) -> str:
    """The host we owe politeness to, as the last two labels of its name.

    Coarser than the full hostname on purpose. Every member's official site
    is its own subdomain of house.gov but they share infrastructure, so
    spacing per subdomain would let 435 of them be hit at once. Two labels
    keeps the whole of house.gov on one clock while leaving 3,000 unrelated
    campaign domains on 3,000 separate ones, which is what politeness
    actually means here.
    """
    host = urlsplit(url).netloc.lower().split(":")[0]
    labels = [label for label in host.split(".") if label]
    return ".".join(labels[-2:]) if len(labels) >= 2 else host


class _FetchClient:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        self._unverified: set[str] = set()

    def fetched_unverified(self, url: str) -> bool:
        """Whether this URL was read without a verified certificate chain."""
        with self._lock:
            return url in self._unverified

    def _get_without_verification(self, url: str) -> bytes | None:
        """One retry with certificate checking off, for a broken chain.

        Deliberate, narrow, and recorded. We already read a campaign site the
        committee declared as plain http, which carries no verification at
        all, so refusing a misconfigured https host while accepting an
        unencrypted one would be incoherent -- and it would fall hardest on
        small campaigns with cheap hosting, which is the group this whole pass
        exists to reach. Every page read this way is marked, and the
        methodology page says so.
        """
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        self._wait_for_slot(url)
        try:
            response = httpx.get(
                url, timeout=30, headers={"User-Agent": USER_AGENT},
                follow_redirects=True, verify=context,
            )
            response.raise_for_status()
        except httpx.HTTPError:
            return None
        with self._lock:
            self._unverified.add(url)
        logger.info("%s read without certificate verification (broken chain)", url)
        return response.content

    def _wait_for_slot(self, url: str) -> None:
        """Space requests per host, not globally.

        A single global clock made a national campaign-site pass take days:
        every request waited on every unrelated request that preceded it,
        while no individual server saw traffic worth throttling. Sequential
        callers are unaffected, because a walk of one site hits one host.
        """
        key = throttle_key(url)
        while True:
            with self._lock:
                now = time.monotonic()
                nxt = self._next_allowed.get(key, 0.0)
                if now >= nxt:
                    self._next_allowed[key] = now + MIN_INTERVAL_SECONDS
                    return
                wait = nxt - now
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
            self._wait_for_slot(url)
            try:
                response = httpx.get(
                    url, timeout=30, headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                response.raise_for_status()
                return response.content
            except httpx.HTTPStatusError as exc:
                # A 404 is an answer, not a failure to get one. Probe paths
                # produce them by design -- four speculative URLs per campaign
                # site -- and retrying each three times with backoff spent
                # about half a minute per site learning what the first request
                # already said. 5xx is the server struggling, so that retries.
                if exc.response.status_code < 500:
                    logger.debug("%s returned %d", url, exc.response.status_code)
                    return None
                last_error = exc
                time.sleep(2.0 * attempt)
            except (httpx.ConnectError, httpx.UnsupportedProtocol) as exc:
                # A domain that does not resolve, or refuses the connection,
                # will not start resolving two seconds later. Roughly a
                # quarter of declared campaign addresses are dead registrations
                # and each was costing three 30-second waits.
                if isinstance(exc, httpx.ConnectError) and is_certificate_error(exc):
                    return self._get_without_verification(url)
                logger.debug("%s unreachable: %s", url, exc)
                return None
            except httpx.HTTPError as exc:
                last_error = exc
                time.sleep(2.0 * attempt)
        logger.warning("giving up on %s: %s", url, last_error)
        return None


@cache
def client() -> _FetchClient:
    return _FetchClient()


class _LinkCollector(HTMLParser):
    """Collects (href, anchor text) pairs, because the text is often the only
    honest label a link has."""

    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._href:
                self.links.append((self._href, " ".join("".join(self._text).split())))
            self._href = None


def extract_text(html_bytes: bytes) -> str | None:
    """Main-content text, or None when the page has no meaningful prose."""
    text = trafilatura.extract(html_bytes.decode("utf-8", errors="replace"))
    if text is None or len(text) < MIN_TEXT_CHARS:
        return None
    return text


def _same_site_links(
    html_bytes: bytes, base_url: str
) -> list[tuple[str, str, str]]:
    """(normalized_url, lowercased path, lowercased anchor text) for every
    same-host page link, in page order, deduped, excluding the page itself."""
    collector = _LinkCollector()
    collector.feed(html_bytes.decode("utf-8", errors="replace"))
    base_host = urlsplit(base_url).netloc.removeprefix("www.")

    out: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for href, text in collector.links:
        absolute = urljoin(base_url, href.strip())
        parts = urlsplit(absolute)
        if parts.scheme not in ("http", "https"):
            continue
        if parts.netloc.removeprefix("www.") != base_host:
            continue
        normalized = absolute.split("#", 1)[0].rstrip("/")
        if normalized in seen or normalized == base_url.rstrip("/"):
            continue
        seen.add(normalized)
        out.append((normalized, parts.path.lower(), text.lower()))
    return out


def _is_junk(path: str) -> bool:
    segments = {segment for segment in path.split("/") if segment}
    if segments & JUNK_SEGMENTS:
        return True
    # Binary assets sometimes carry keyword-bearing names ("issues-flyer.png").
    return path.rsplit(".", 1)[-1] in {"png", "jpg", "jpeg", "gif", "svg", "pdf",
                                       "xml", "ico", "mp4", "mp3"}


def discover_issue_links(html_bytes: bytes, base_url: str, cap: int = 8) -> list[str]:
    """Same-site links that look like policy/positions pages, deduped.

    A keyword may match in the path or in the anchor text: official-site
    CMSes label the policy link plainly ("Votes and Legislation") while the
    path says nothing a list could anticipate. Junk segments are excluded even
    on a match, so "about" cannot spend the budget on staff pages and event
    calendars before an issue page is reached.
    """
    links = _same_site_links(html_bytes, base_url)
    found: list[str] = []
    for keyword in ISSUE_KEYWORDS:  # keyword-priority order, then page order
        for url, path, text in links:
            if url in found or _is_junk(path):
                continue
            if {segment for segment in path.split("/")} & NOT_ISSUE_SEGMENTS:
                continue
            if keyword in path or keyword in text:
                found.append(url)
                if len(found) >= cap:
                    return found
    return found


def discover_press_index(html_bytes: bytes, base_url: str) -> str | None:
    """The site's press-release index, when one is linked.

    Matched narrowly on "press-release"/"press releases" rather than "news"
    or "media": a media page is photo galleries and interview clips, while
    the press-release index is dated first-party statements, which is the
    only one of those a promise can be verified against.
    """
    for url, path, text in _same_site_links(html_bytes, base_url):
        if _is_junk(path):
            continue
        if "press-release" in path or "press releases" in text:
            return url
    return None


def discover_child_links(html_bytes: bytes, index_url: str, cap: int = 8) -> list[str]:
    """Pages one level below an index page, in page order.

    An /issues page on the house.gov CMS is a grid of topic tiles with almost
    no prose of its own; the positions live at /issues/<topic>. This walks
    from a fetched index to its children so the crawl stores the pages that
    carry the member's actual words, not the navigation that points at them.
    """
    prefix = urlsplit(index_url).path.rstrip("/").lower() + "/"
    found = [
        url for url, path, _ in _same_site_links(html_bytes, index_url)
        if path.rstrip("/").startswith(prefix) and not _is_junk(path)
    ]
    return found[:cap]


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
