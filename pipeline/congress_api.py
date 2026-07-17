"""Throttled Congress.gov API client (api.congress.gov, v3).

Same discipline as the FEC client: thread-safe global request spacing
(the key allows 5,000/hr; we cruise at ~4,000/hr), and callers receive the
canonical key-free URL for sources provenance. Keys never leave this module
except inside the outgoing request.

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
                    url, params={**query, "api_key": self._key},
                    timeout=30, follow_redirects=True,
                )
                if response.status_code == 429:
                    logger.warning("Congress.gov rate limit hit; backing off")
                    time.sleep(15.0 * attempt)
                    continue
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
