"""YouTube discovery (official Data API) + public caption fetching.

Discovery uses the official API with the project key (quota: search costs
100 units of the 10,000/day budget — a five-candidate sync uses ~10 searches).
Captions come from youtube-transcript-api (verified 1.2.4:
YouTubeTranscriptApi().fetch(video_id) -> FetchedTranscript.to_raw_data()),
which reads the public caption tracks. Videos without captions are queued in
media_assets for whisper transcription on a GPU instance later.
"""

import logging
import threading
import time
from functools import cache
from typing import Any, cast

import httpx
from youtube_transcript_api import YouTubeTranscriptApi

from pipeline.config import get_settings

logger = logging.getLogger(__name__)

SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
PUBLISHED_AFTER = "2025-01-01T00:00:00Z"  # the 2026 cycle
MIN_INTERVAL_SECONDS = 0.5


class _Throttle:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def wait(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                if now >= self._next_allowed:
                    self._next_allowed = now + MIN_INTERVAL_SECONDS
                    return
                pause = self._next_allowed - now
            time.sleep(pause)


@cache
def _throttle() -> _Throttle:
    return _Throttle()


def search_videos(query: str, max_results: int = 6) -> tuple[dict[str, Any], str]:
    """Official API search; returns (payload, canonical key-free URL)."""
    key = get_settings().youtube_api_key.get_secret_value()
    if not key:
        raise RuntimeError("YOUTUBE_API_KEY is not set in .env")
    params: dict[str, Any] = {
        "part": "snippet", "q": query, "type": "video",
        "maxResults": max_results, "publishedAfter": PUBLISHED_AFTER,
        "relevanceLanguage": "en", "safeSearch": "none",
    }
    canonical = str(httpx.URL(SEARCH_URL, params=params))
    _throttle().wait()
    response = httpx.get(SEARCH_URL, params={**params, "key": key}, timeout=30)
    response.raise_for_status()
    return response.json(), canonical


def fetch_captions(video_id: str) -> tuple[str | None, str]:
    """(caption text, status) where status is 'ok', 'none', or 'blocked'.

    'blocked' means YouTube refused the request (rate limit / IP block) —
    the video may well HAVE captions, we just could not read them now, so
    callers must record "unknown", never "no captions". Auto-generated
    captions lack punctuation; acceptable for the pilot, and whisper
    transcription on the GPU box supersedes them later.
    """
    _throttle().wait()
    try:
        transcript = YouTubeTranscriptApi().fetch(video_id)
    except Exception as exc:
        kind = type(exc).__name__
        logger.info("no captions for %s (%s)", video_id, kind)
        if kind in ("IpBlocked", "RequestBlocked", "YouTubeRequestFailed", "AgeRestricted"):
            return None, "blocked"
        return None, "none"  # TranscriptsDisabled / NoTranscriptFound: truly captionless
    # The library annotates this as List[Dict] with no type params; the cast
    # plus scoped ignore pins the shape we rely on ({'text','start','duration'}).
    raw_items = cast(
        "list[dict[str, Any]]",
        transcript.to_raw_data(),  # pyright: ignore[reportUnknownMemberType]
    )
    parts = [str(item.get("text", "")).strip() for item in raw_items]
    text = " ".join(part for part in parts if part)
    return (text, "ok") if text else (None, "none")


def video_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
