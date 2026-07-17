"""Stage 4 — extract_promises: chunked LLM extraction through the quote gate.

Flow per document:
  chunk (~10k chars, 800 overlap, absolute offsets carried)
    -> extraction agent (local model via OpenAI-compatible endpoint,
       temperature 0, versioned prompt from pipeline/prompts/)
    -> pipeline.verify.verify_quote against the FULL document text
    -> verified promises stored (exact or relocated offsets);
       rejections COUNTED AND LOGGED, never stored.

The model is injectable (tests pass pydantic-ai's TestModel; production
builds an OpenAIChatModel against Settings.vllm_base_url — a Manifold
vllm-serve/sglang-serve job). Execution location never touches the data
path: whatever produced the output, the same gate decides what is stored.

Verified against installed pydantic-ai 2.12.0: Agent(output_type=...),
result.output, OpenAIChatModel(model_name, provider=OpenAIProvider(...)).
The blueprint's OpenAIModel class no longer exists.
"""

import logging
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pipeline import db
from pipeline.config import get_settings
from pipeline.stages import StageStats
from pipeline.verify import verify_quote

logger = logging.getLogger(__name__)

PROMPT_VERSION = "extract_v2"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

CHUNK_CHARS = 10_000   # ~2.5k tokens
OVERLAP_CHARS = 800    # ~200 tokens


class ExtractedPromise(BaseModel):
    verbatim_quote: str = Field(description="EXACT text copied from the chunk, unmodified.")
    char_start: int = Field(description="Start offset of the quote within THIS chunk.")
    char_end: int = Field(description="End offset (exclusive) within THIS chunk.")
    topic: str
    specificity: Literal["measurable", "directional", "rhetorical"]


class ExtractionResult(BaseModel):
    promises: list[ExtractedPromise]


@cache
def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")


def chunk_document(full_text: str) -> list[tuple[int, str]]:
    """(absolute_offset, chunk_text) pairs with overlap so no promise
    straddles a boundary unseen."""
    if len(full_text) <= CHUNK_CHARS:
        return [(0, full_text)]
    chunks: list[tuple[int, str]] = []
    start = 0
    while start < len(full_text):
        chunks.append((start, full_text[start : start + CHUNK_CHARS]))
        if start + CHUNK_CHARS >= len(full_text):
            break
        start += CHUNK_CHARS - OVERLAP_CHARS
    return chunks


def build_agent(model: Model | None = None) -> Agent[None, ExtractionResult]:
    """Extraction agent; tests inject TestModel, production uses the
    OpenAI-compatible endpoint from settings (Manifold vLLM/sglang job)."""
    if model is None:
        settings = get_settings()
        if not settings.vllm_base_url or not settings.local_model:
            raise RuntimeError(
                "extraction needs a model endpoint: set VLLM_BASE_URL and "
                "LOCAL_MODEL in .env (start a vllm-serve/sglang-serve job in Manifold)"
            )
        model = OpenAIChatModel(
            settings.local_model,
            provider=OpenAIProvider(base_url=settings.vllm_base_url, api_key="not-needed"),
        )
    return Agent(
        model,
        output_type=ExtractionResult,
        system_prompt=load_prompt(PROMPT_VERSION),
        model_settings={"temperature": 0.0},
    )


def extract_document(
    conn: db.Connection,
    agent: Agent[None, ExtractionResult],
    politician_id: int,
    document: db.DocumentForExtraction,
    model_name: str,
    stats: StageStats,
) -> None:
    # A document reaching this point is being (re)extracted under the current
    # prompt+model; drop any prior-version promises so the result is clean.
    db.delete_promises_for_document(conn, document.document_id)
    for chunk_offset, chunk_text in chunk_document(document.full_text):
        result = agent.run_sync(chunk_text)
        stats["chunks_processed"] += 1
        for promise in result.output.promises:
            verification = verify_quote(
                document.full_text, chunk_offset, promise.verbatim_quote,
                promise.char_start, promise.char_end,
            )
            if not verification.verified:
                # THE gate: hallucinated/paraphrased quotes die here, loudly.
                stats["quotes_rejected"] += 1
                logger.warning(
                    "REJECTED quote (doc %d, %s): %r",
                    document.document_id, document.url, promise.verbatim_quote[:120],
                )
                continue
            stats[f"quotes_{verification.method}"] += 1
            db.insert_verified_promise(
                conn,
                politician_id=politician_id,
                document_id=document.document_id,
                verbatim_quote=promise.verbatim_quote,
                char_start=verification.char_start,
                char_end=verification.char_end,
                topic=promise.topic.strip().lower() or "other",
                specificity=promise.specificity,
                model_name=model_name,
                prompt_version=PROMPT_VERSION,
            )
            stats["promises_stored"] += 1
    db.mark_document_extracted(conn, document.document_id, model_name, PROMPT_VERSION)
    stats["documents_extracted"] += 1


def extract_promises(
    conn: db.Connection,
    politician_id: int,
    agent: Agent[None, ExtractionResult] | None = None,
    model_name: str | None = None,
) -> StageStats:
    """Extract and verify promises from a candidate's unprocessed documents."""
    if agent is None:
        agent = build_agent()
    if model_name is None:
        model_name = get_settings().local_model or "unknown"

    stats: StageStats = {
        "documents_extracted": 0, "chunks_processed": 0, "promises_stored": 0,
        "quotes_exact": 0, "quotes_relocated": 0, "quotes_rejected": 0,
    }
    documents = db.documents_for_extraction(conn, politician_id, PROMPT_VERSION, model_name)
    for document in documents:
        extract_document(conn, agent, politician_id, document, model_name, stats)
    return stats
