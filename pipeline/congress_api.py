"""Throttled Congress.gov API client (api.congress.gov, v3).

Same discipline as the FEC client: thread-safe global request spacing
(the key allows 5,000/hr; we cruise at ~4,000/hr), and callers receive the
canonical key-free URL for sources provenance.

The key travels in the X-Api-Key header, never as a query parameter. That
is not cosmetic: httpx logs every request URL at INFO, so a key in the
query string ends up in log files, terminal scrollback, and any log
aggregator. In the header it stays out of all of them, and the URL we
record as provenance is byte-for-byte the URL we requested.

House roll-call votes only — the API has no senate-vote endpoint (verified
2026-07-17); Senate roll calls come from senate.gov (pipeline.senate_gov).
"""

import logging
import threading
import time
from functools import cache
from typing import Any

import httpx

from pipeline.config import get_settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.congress.gov/v3"
MIN_INTERVAL_SECONDS = 0.9  # ~4,000/hr, under the 5,000/hr key limit
MAX_ATTEMPTS = 4
PAGE_LIMIT = 250  # API maximum


class NotFoundError(RuntimeError):
    """The API returned a 4xx. The record does not exist; do not retry."""


class CongressApiClient:
    def __init__(self) -> None:
        key = get_settings().congress_gov_api_key.get_secret_value()
        if not key:
            raise RuntimeError("CONGRESS_GOV_API_KEY is not set in .env")
        self._key = key
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

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[dict[str, Any], str]:
        """GET an endpoint; returns (json payload, canonical key-free URL)."""
        url = f"{API_BASE}{path}"
        query = {"format": "json", **(params or {})}
        canonical = str(httpx.URL(url, params=query))
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_slot()
            try:
                response = httpx.get(
                    url, params=query, headers={"X-Api-Key": self._key},
                    timeout=30, follow_redirects=True,
                )
                if response.status_code == 429:
                    logger.warning("Congress.gov rate limit hit; backing off")
                    time.sleep(15.0 * attempt)
                    continue
                if 400 <= response.status_code < 500:
                    # A 404 means the record does not exist. Retrying it three
                    # more times with backoff only wastes the rate limit, so
                    # client errors fail immediately and the caller decides.
                    raise NotFoundError(
                        f"Congress.gov returned {response.status_code}: {canonical}"
                    )
                response.raise_for_status()
                return response.json(), canonical
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("Congress.gov attempt %d/%d failed: %s",
                               attempt, MAX_ATTEMPTS, exc)
                time.sleep(2.0 * attempt)
        raise RuntimeError(
            f"Congress.gov request failed after {MAX_ATTEMPTS} attempts: {canonical}"
        ) from last_error


@cache
def client() -> CongressApiClient:
    return CongressApiClient()


def house_vote_page(congress: int, session: int, offset: int) -> tuple[dict[str, Any], str]:
    """One page of the House roll-call vote list (PAGE_LIMIT per page)."""
    return client().get(
        f"/house-vote/{congress}/{session}", {"offset": offset, "limit": PAGE_LIMIT}
    )


def house_vote_members(congress: int, session: int, roll: int) -> tuple[dict[str, Any], str]:
    """Every member's position on one House roll call (keyed by bioguideID)."""
    return client().get(f"/house-vote/{congress}/{session}/{roll}/members")


# -- bill metadata -------------------------------------------------------------
# Roll-call rows name a bill but never say what it is about. These three
# endpoints supply the topical text the evaluation stage reasons over.
# Verified against the live API 2026-08-15: /bill returns title, policyArea,
# sponsors, introducedDate and latestAction inline, with subjects and
# summaries as counted sub-resources fetched separately.


def bill_detail(congress: int, bill_type: str, number: int) -> tuple[dict[str, Any], str]:
    """Core record for one bill: title, policy area, sponsor, latest action."""
    return client().get(f"/bill/{congress}/{bill_type}/{number}")


def bill_subjects(congress: int, bill_type: str, number: int) -> tuple[dict[str, Any], str]:
    """Legislative subject terms — the field the vote pre-filter selects on."""
    return client().get(
        f"/bill/{congress}/{bill_type}/{number}/subjects", {"limit": PAGE_LIMIT}
    )


def bill_summaries(congress: int, bill_type: str, number: int) -> tuple[dict[str, Any], str]:
    """CRS summaries. Plain-language, and the only prose describing content."""
    return client().get(f"/bill/{congress}/{bill_type}/{number}/summaries")
