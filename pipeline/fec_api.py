"""Throttled OpenFEC API client.

Rate discipline: requests are spaced globally (thread-safe, since DBOS runs
steps on worker threads) to stay comfortably under the key's 7,500/hr limit.
On HTTP 429 the client flips to the backup key if one is configured.

Provenance discipline: callers get back the canonical request URL WITHOUT the
api_key parameter — that is what goes into sources.url. Keys never leave this
module except inside the outgoing request itself.
"""

import logging
import threading
import time
from functools import cache
from typing import Any

import httpx

from pipeline.config import get_settings

logger = logging.getLogger(__name__)

API_BASE = "https://api.open.fec.gov/v1"
MIN_INTERVAL_SECONDS = 0.6  # ~6,000/hr ceiling, under the 7,500/hr key limit
MAX_ATTEMPTS = 4


class FecApiClient:
    def __init__(self) -> None:
        settings = get_settings()
        self._keys = [
            key.get_secret_value()
            for key in (settings.fec_api_key, settings.fec_api_key_backup)
            if key.get_secret_value()
        ]
        if not self._keys:
            raise RuntimeError("FEC_API_KEY is not set in .env")
        self._active_key = 0
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def _wait_for_slot(self) -> None:
        """Global spacing between requests; safe across DBOS worker threads."""
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_allowed:
                    self._next_allowed = now + MIN_INTERVAL_SECONDS
                    return
                wait = self._next_allowed - now
            time.sleep(wait)

    def get(self, path: str, params: dict[str, Any]) -> tuple[dict[str, Any], str]:
        """GET an endpoint; returns (json payload, canonical key-free URL)."""
        url = f"{API_BASE}{path}"
        canonical = str(httpx.URL(url, params=params))
        last_error: Exception | None = None
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._wait_for_slot()
            key = self._keys[self._active_key]
            try:
                response = httpx.get(
                    url, params={**params, "api_key": key}, timeout=30, follow_redirects=True
                )
                if response.status_code == 429:
                    if len(self._keys) > 1:
                        self._active_key = 1 - self._active_key
                        logger.warning("FEC rate limit hit; switching to backup key")
                    time.sleep(2.0 * attempt)
                    continue
                response.raise_for_status()
                return response.json(), canonical
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning("FEC API attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, exc)
                time.sleep(2.0 * attempt)
        raise RuntimeError(f"FEC API request failed after {MAX_ATTEMPTS} attempts: {canonical}") \
            from last_error


@cache
def client() -> FecApiClient:
    """Process-wide client so throttling is truly global."""
    return FecApiClient()


def candidate_totals(fec_candidate_id: str, cycle: int) -> tuple[dict[str, Any], str]:
    """Official per-candidate financial totals for a two-year cycle.

    election_full=false: two-year totals, matching the cycle-scoped bulk
    files we load (a Senate election_full window spans six years).
    """
    return client().get(
        f"/candidate/{fec_candidate_id}/totals/",
        {"cycle": cycle, "election_full": "false", "per_page": 10},
    )
