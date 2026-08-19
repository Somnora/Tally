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
    # Carried on the document because it qualifies the evidence: a page read
    # over a certificate we could not verify is still what the host served,
    # but we cannot say the host was who it claimed to be. Recorded rather
    # than refused, and never silent.
    meta: dict[str, Any] = {"origin_url": url}
    if webdocs.client().fetched_unverified(url):
        meta["tls_verified"] = False
        stats["pages_tls_unverified"] += 1
    db.insert_document(
        conn, politician_id=politician_id, source_id=source_id, doc_type=doc_type,
        title=title, url=url, published_at=None, full_text=text,
        content_hash=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        meta=meta,
    )
    stats["documents_stored"] += 1


# Path tails that mark an INDEX page: a grid of topic tiles whose children,
# not itself, carry the member's words. Fetching one triggers a walk of its
# children.
_INDEX_TAILS = ("issues", "priorities", "legislation", "platform", "agenda")

# Hard ceiling on fetches for one site, homepage and probes included. The
# politeness delay makes every page cost 1.5s, so an unbounded walk is a time
# problem before it is a bandwidth one.
MAX_SITE_PAGES = 24


def sync_site(
    conn: db.Connection,
    politician_id: int,
    site_url: str,
    *,
    doc_type: str = "campaign_site",
    source_type: str = "campaign_site_html",
    homepage_title: str = "Campaign homepage",
    probe_paths: tuple[str, ...] = (),
    press_release_cap: int = 0,
) -> dict[str, Any]:
    """Homepage + issue pages (+ probed indexes and their children).

    The same walk serves a campaign site and a member's official
    house.gov/senate.gov site; only the labels differ, and they differ because
    government speech and campaign speech are not the same claim about a
    person even when the walk that finds them is identical.

    probe_paths exist because link discovery can only see links that are in
    the HTML. The house.gov CMS keeps its Issues section in a script-rendered
    dropdown, so /issues is real, full of positions, and linked from nowhere a
    parser can reach: 82 of 185 members with no extracted promises had no
    issue page found at all, while /issues answered directly on every site
    probed. A probe that 404s costs one polite request and is counted, not
    treated as an error.

    press_release_cap follows the newest press releases when an index for
    them is discovered. On several official sites the issue pages are
    boilerplate feeds and the member's actual commitments appear only in
    releases, which the ingestion blueprint has always named as a source.
    """
    stats: StageStats = {"pages_fetched": 0, "pages_failed": 0,
                         "pages_without_content": 0, "documents_stored": 0,
                         "probes_missed": 0, "index_children_fetched": 0,
                         "press_releases_stored": 0, "pages_tls_unverified": 0}
    page_urls: list[str] = []
    fetched: set[str] = set()

    def fetch(url: str, *, speculative: bool = False) -> bytes | None:
        key = url.rstrip("/")
        if key in fetched or len(fetched) >= MAX_SITE_PAGES:
            return None
        fetched.add(key)
        html = webdocs.client().get(url)
        if html is None:
            stats["probes_missed" if speculative else "pages_failed"] += 1
            return None
        stats["pages_fetched"] += 1
        page_urls.append(url)
        return html

    homepage = fetch(site_url)
    if homepage is None:
        return {"stats": stats, "page_urls": page_urls}
    _store_page(conn, politician_id, site_url, homepage,
                doc_type, source_type, homepage_title, stats)

    def walk_children(index_html: bytes, index_url: str) -> None:
        for child in webdocs.discover_child_links(index_html, index_url):
            child_html = fetch(child)
            if child_html is None:
                continue
            stats["index_children_fetched"] += 1
            _store_page(conn, politician_id, child, child_html,
                        doc_type, source_type, None, stats)

    candidates = webdocs.discover_issue_links(homepage, site_url)
    probes: set[str] = set()
    for probe in probe_paths:
        probe_url = site_url.rstrip("/") + probe
        if probe_url.rstrip("/") not in {c.rstrip("/") for c in candidates}:
            candidates.append(probe_url)
            probes.add(probe_url.rstrip("/"))

    for link in candidates:
        html = fetch(link, speculative=link.rstrip("/") in probes)
        if html is None:
            continue
        _store_page(conn, politician_id, link, html, doc_type, source_type, None, stats)
        if link.rstrip("/").rsplit("/", 1)[-1].lower() in _INDEX_TAILS:
            walk_children(html, link)

    if press_release_cap:
        press_index = webdocs.discover_press_index(homepage, site_url)
        if press_index:
            index_html = fetch(press_index)
            if index_html is not None:
                for leaf in webdocs.discover_child_links(
                    index_html, press_index, cap=press_release_cap
                ):
                    leaf_html = fetch(leaf)
                    if leaf_html is None:
                        continue
                    before = stats["documents_stored"]
                    _store_page(conn, politician_id, leaf, leaf_html,
                                "press_release", source_type, None, stats)
                    if stats["documents_stored"] > before:
                        stats["press_releases_stored"] += 1

    return {"stats": stats, "page_urls": page_urls}


def sync_campaign_site(
    conn: db.Connection, politician_id: int, campaign_url: str
) -> dict[str, Any]:
    """A candidate's own campaign site.

    The probe list is measured, not guessed. Campaign homepages are far more
    often a splash screen than an official site is -- a photo, a slogan and a
    donate button, with the positions one click away. Across 60 sampled 2026
    campaign sites, 11 homepages carried under 150 words, and probing these
    four paths recovered substantive text on 5 of them; adrianboafo.com went
    from 10 words to 2,967 at /issues. The other 6 were script-rendered
    shells that serve no prose to any fetcher, which is a limit of static
    crawling and is recorded as such rather than retried.

    Press releases are followed, fewer than on an official site. A campaign's
    own releases are first-party statements a promise can be verified
    against, but on a campaign site the issue pages are usually the real
    content, and the per-site page ceiling is shared between them.
    """
    return sync_site(
        conn, politician_id, campaign_url,
        probe_paths=("/issues", "/priorities", "/platform", "/about"),
        press_release_cap=4,
    )


def sync_official_site(
    conn: db.Connection, politician_id: int, official_url: str
) -> dict[str, Any]:
    """A sitting member's house.gov or senate.gov site.

    Stored as official_site, never campaign_site: this is a congressional
    office publishing under rules that restrict campaign content, and a reader
    is entitled to know which of the two they are looking at.

    /issues is probed unconditionally because the house.gov CMS renders its
    Issues dropdown with script, so the section is real and linked from
    nowhere a parser can see. Press releases are followed because on many of
    these sites they are where the member's commitments actually appear; they
    are stored under their own doc_type, which the schema has carried for
    exactly this since the beginning.
    """
    return sync_site(
        conn, politician_id, official_url,
        doc_type="official_site", source_type="official_site_html",
        homepage_title="Official congressional website",
        probe_paths=("/issues",),
        press_release_cap=6,
    )


def sync_wayback(conn: db.Connection, politician_id: int, page_urls: list[str]) -> StageStats:
    """Earliest cycle snapshot per page; unchanged content dedupes away."""
    stats: StageStats = {"snapshots_found": 0, "snapshots_missing": 0,
                         "pages_without_content": 0, "documents_stored": 0,
                         "pages_tls_unverified": 0}
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
