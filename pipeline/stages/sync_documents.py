"""Stage 3 — sync_documents: transcripts, press releases, campaign pages.

Milestone 4. YouTube discovery (captions where available, faster-whisper
transcription on a GPU instance where not), campaign-site scraping with
Wayback Machine snapshots to catch scrubbed promises.

GPU boundary: transcription is the first GPU-dependent step. It runs on a
Lambda instance; this module only ever sees the resulting text, which flows
through the same documents/sources gates regardless of where it was produced.
"""

from pipeline.db import Connection
from pipeline.stages import StageStats


def sync_documents(conn: Connection, politician_id: int, run_id: int) -> StageStats:
    """Discover and ingest new documents for one candidate.

    Contract: every document stores full_text plus a sources row for the raw
    artifact (caption file, audio hash, HTML snapshot); documents dedupe on
    (politician_id, content_hash); full_text is immutable once promises
    reference it. Transcription requires Settings.gpu_available.
    """
    raise NotImplementedError("Milestone 4")


def transcribe_media(media_url: str) -> str:
    """Transcribe audio/video to text (faster-whisper, GPU required).

    Runs only where Settings.gpu_available is true; callers on non-GPU
    machines must enqueue for remote execution instead of calling this.
    """
    raise NotImplementedError("Milestone 4 — GPU path, runs on Lambda instances")
