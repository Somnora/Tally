"""Stage 3 — sync_documents: campaign pages, Wayback snapshots, YouTube.

Implemented for the Milestone 4 pilot (no GPU required):

  campaign_site   fetch the seeded campaign homepage + discovered issue
                  pages, extract main text, store as documents
  wayback         earliest 2025+ Internet Archive capture of each page —
                  the baseline that catches promises scrubbed later
  youtube         discover candidate video via the official Data API; where
                  public captions exist the transcript becomes a document
                  now; the rest queue in media_assets for whisper on a GPU
                  instance (pipeline.stages.sync_documents.transcribe_media)

Every fetched artifact (HTML bytes, API payload, caption text) becomes a
sources row before the derived document is stored. Documents dedupe on
(politician_id, sha256 of full_text), so re-runs and unchanged pages cost
nothing. full_text is immutable once promises reference it.
"""

import hashlib
import json
import logging
from typing import Any

from pipeline import db, webdocs, youtube
from pipeline.stages import StageStats

logger = logging.getLogger(__name__)

MAX_VIDEOS_PER_QUERY = 6


def _store_page(
    conn: db.Connection,
    politician_id: int,
    url: str,
    html: bytes,
    doc_type: str,
    source_type: str,
    title: str | None,
    stats: StageStats,
) -> None:
    source_id = db.insert_source(
        conn, source_type=source_type, url=url,
        content_hash=hashlib.sha256(html).hexdigest(), raw_payload=html,
    )
    text = webdocs.extract_text(html)
    if text is None:
        stats["pages_without_content"] += 1
        return
    db.insert_document(
        conn, politician_id=politician_id, source_id=source_id, doc_type=doc_type,
        title=title, url=url, published_at=None, full_text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        meta={"origin_url": url},
    )
    stats["documents_stored"] += 1


def sync_campaign_site(
    conn: db.Connection, politician_id: int, campaign_url: str
) -> dict[str, Any]:
    """Homepage + issue pages. Returns stats plus the page URLs (for wayback)."""
    stats: StageStats = {"pages_fetched": 0, "pages_failed": 0,
                         "pages_without_content": 0, "documents_stored": 0}
    page_urls: list[str] = []

    homepage = webdocs.client().get(campaign_url)
    if homepage is None:
        stats["pages_failed"] += 1
        return {"stats": stats, "page_urls": page_urls}
    stats["pages_fetched"] += 1
    page_urls.append(campaign_url)
    _store_page(conn, politician_id, campaign_url, homepage,
                "campaign_site", "campaign_site_html", "Campaign homepage", stats)

    for link in webdocs.discover_issue_links(homepage, campaign_url):
        html = webdocs.client().get(link)
        if html is None:
            stats["pages_failed"] += 1
            continue
        stats["pages_fetched"] += 1
        page_urls.append(link)
        _store_page(conn, politician_id, link, html,
                    "campaign_site", "campaign_site_html", None, stats)

    return {"stats": stats, "page_urls": page_urls}


def sync_wayback(conn: db.Connection, politician_id: int, page_urls: list[str]) -> StageStats:
    """Earliest cycle snapshot per page; unchanged content dedupes away."""
    stats: StageStats = {"snapshots_found": 0, "snapshots_missing": 0,
                         "pages_without_content": 0, "documents_stored": 0}
    for url in page_urls:
        snapshot = webdocs.earliest_snapshot(url)
        if snapshot is None:
            stats["snapshots_missing"] += 1
            continue
        stats["snapshots_found"] += 1
        html = webdocs.client().get(snapshot.archive_url, check_robots=False)
        if html is None:
            continue
        _store_page(conn, politician_id, snapshot.archive_url, html,
                    "wayback_snapshot", "wayback_snapshot_html",
                    f"Archived {snapshot.timestamp[:8]}: {snapshot.original_url}", stats)
    return stats


def sync_youtube(
    conn: db.Connection, politician_id: int, queries: list[str], required_name: str
) -> StageStats:
    """Discover videos; captions become documents, the rest queue for whisper.

    Relevance gate: YouTube search relevance drifts (a query for one
    candidate can surface unrelated livestreams), so a video only counts if
    the candidate's name appears in its title, channel, or description.
    Filtered videos are counted, never stored — wrongly attributing a
    transcript to a candidate is worse than missing a video.
    """
    stats: StageStats = {"videos_discovered": 0, "captions_stored": 0,
                         "pending_transcription": 0, "skipped_irrelevant": 0,
                         "caption_fetch_blocked": 0}
    seen_video_ids: set[str] = set()
    needle = required_name.lower()

    for query in queries:
        payload, canonical_url = youtube.search_videos(query, MAX_VIDEOS_PER_QUERY)
        raw = json.dumps(payload, sort_keys=True).encode("utf-8")
        search_source_id = db.insert_source(
            conn, source_type="youtube_api_search", url=canonical_url,
            content_hash=hashlib.sha256(raw).hexdigest(), raw_payload=raw,
        )
        items: list[dict[str, Any]] = payload.get("items") or []
        for item in items:
            id_block: dict[str, Any] = item.get("id") or {}
            video_id = str(id_block.get("videoId") or "")
            if not video_id or video_id in seen_video_ids:
                continue
            seen_video_ids.add(video_id)
            snippet: dict[str, Any] = item.get("snippet") or {}
            haystack = " ".join(
                str(snippet.get(field) or "")
                for field in ("title", "channelTitle", "description")
            ).lower()
            if needle not in haystack:
                stats["skipped_irrelevant"] += 1
                continue
            stats["videos_discovered"] += 1

            captions, caption_status = youtube.fetch_captions(video_id)
            document_id: int | None = None
            if captions is not None:
                caption_source_id = db.insert_source(
                    conn, source_type="youtube_captions",
                    url=youtube.video_url(video_id),
                    content_hash=hashlib.sha256(captions.encode("utf-8")).hexdigest(),
                    raw_payload=captions.encode("utf-8"),
                )
                document_id = db.insert_document(
                    conn, politician_id=politician_id, source_id=caption_source_id,
                    doc_type="youtube_transcript", title=snippet.get("title"),
                    url=youtube.video_url(video_id),
                    published_at=snippet.get("publishedAt"),
                    full_text=captions,
                    content_hash=hashlib.sha256(captions.encode("utf-8")).hexdigest(),
                    transcribed_by="youtube_captions",
                    meta={"video_id": video_id, "channel": snippet.get("channelTitle"),
                          "discovery_query": query},
                )
                stats["captions_stored"] += 1
            elif caption_status == "blocked":
                stats["caption_fetch_blocked"] += 1
            else:
                stats["pending_transcription"] += 1

            # has_captions: True (read), False (video has none), NULL (blocked,
            # unknown) — a blocked fetch must never masquerade as "no captions".
            has_captions = True if captions is not None else (
                None if caption_status == "blocked" else False
            )
            db.upsert_media_asset(
                conn, politician_id=politician_id, external_id=video_id,
                title=snippet.get("title"), channel_title=snippet.get("channelTitle"),
                url=youtube.video_url(video_id), published_at=snippet.get("publishedAt"),
                has_captions=has_captions, document_id=document_id,
                source_id=search_source_id,
            )
    return stats


def transcribe_media(media_url: str) -> str:
    """Transcribe audio/video to text (faster-whisper, GPU required).

    Runs only where Settings.gpu_available is true; callers on non-GPU
    machines must enqueue for remote execution instead of calling this.
    """
    raise NotImplementedError("Milestone 4 Phase D — GPU path, runs on Lambda instances")
