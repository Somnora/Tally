"""
Reference implementation: durable per-candidate ingestion pipeline.
Pydantic-AI + DBOS. This is a PATTERN document for the coding agent —
verify the installed pydantic-ai / dbos versions' current APIs against
their docs before finalizing, and pin exact versions in requirements.

Corrections vs. the earlier Gemini blueprint:
- One workflow PER CANDIDATE (enqueued), not one loop over 435 districts.
- Postgres (not SQLite) for the DBOS system database.
- Raw API payloads are never placed in prompts; SQL pre-aggregation first.
- Extraction output is quote-verified against source text before storage.
- Evaluations must cite DB ids; citations are validated before acceptance.
- pydantic-ai: `output_type` / `result.output` (the old `result_type` /
  `.data` names are deprecated — confirm against installed version).
"""

import hashlib
import os
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry
from pydantic_ai.models.openai import OpenAIModel  # vLLM serves an OpenAI-compatible API
from dbos import DBOS, DBOSConfig, Queue

# ---------------------------------------------------------------------------
# DBOS setup — system DB on Postgres, alongside (not inside) the app DB.
# ---------------------------------------------------------------------------
DBOS(config=DBOSConfig(
    name="civic_ingestion",
    system_database_url=os.environ["DBOS_SYSTEM_DATABASE_URL"],  # postgres://...
))

candidate_queue = Queue("candidates", concurrency=4)  # tune to GPU/API budget

# ---------------------------------------------------------------------------
# Local model via vLLM on the Lambda GPU box (OpenAI-compatible endpoint).
# ---------------------------------------------------------------------------
local_model = OpenAIModel(
    model_name=os.environ.get("LOCAL_MODEL", "deepseek-v3"),
    base_url=os.environ.get("VLLM_BASE_URL", "http://localhost:8000/v1"),
    api_key="not-needed",
)

# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ExtractedPromise(BaseModel):
    verbatim_quote: str = Field(description="EXACT text copied from the transcript, unmodified.")
    char_start: int = Field(description="Start offset of the quote within THIS chunk.")
    char_end: int
    topic_category: str
    specificity: Literal["measurable", "directional", "rhetorical"]


class ExtractionResult(BaseModel):
    promises: list[ExtractedPromise]


class CitedEvidence(BaseModel):
    kind: Literal["vote", "donation", "lobbying_filing"]
    db_id: str = Field(description="The exact id string provided in the context for this record.")
    direction: Literal["supports", "contradicts", "contextual"]


class PromiseEvaluation(BaseModel):
    status: Literal["completed", "in_progress", "broken", "pending", "unverifiable"]
    consistency_score: int = Field(ge=1, le=100)
    llm_reasoning: str = Field(description="Concise, neutral, 2-3 sentences, referencing cited evidence only.")
    evidence: list[CitedEvidence] = Field(min_length=1)


# ---------------------------------------------------------------------------
# Agents — temperature 0, prompts loaded from versioned files in /prompts.
# ---------------------------------------------------------------------------
def load_prompt(name: str) -> str:
    with open(f"prompts/{name}.txt") as f:
        return f.read()

extraction_agent = Agent(
    local_model,
    output_type=ExtractionResult,
    system_prompt=load_prompt("extract_v1"),
    model_settings={"temperature": 0.0},
)

evaluation_agent = Agent(
    local_model,
    output_type=PromiseEvaluation,
    system_prompt=load_prompt("evaluate_v1"),
    model_settings={"temperature": 0.0},
)


@evaluation_agent.output_validator
def evidence_ids_must_exist(output: PromiseEvaluation) -> PromiseEvaluation:
    """Reject evaluations citing ids that weren't in the provided context.
    (The step below stores the allowed-id set; ModelRetry feeds the error back
    to the model for a corrected attempt.)"""
    allowed = _current_allowed_ids.get()  # contextvar set per evaluation call
    bad = [e.db_id for e in output.evidence if e.db_id not in allowed]
    if bad:
        raise ModelRetry(f"These ids were not in the provided records: {bad}. "
                         f"Cite only ids that appear in the context.")
    return output


# ---------------------------------------------------------------------------
# Steps — each external effect is its own durable, retriable step.
# ---------------------------------------------------------------------------
@DBOS.step(retries_allowed=True, max_attempts=5, interval_seconds=10, backoff_rate=2.0)
async def fetch_fec_incremental(fec_candidate_id: str) -> dict:
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"https://api.open.fec.gov/v1/candidate/{fec_candidate_id}/totals/",
            params={"api_key": os.environ["FEC_API_KEY"], "cycle": 2026},
        )
        r.raise_for_status()
        return r.json()


@DBOS.step()
def store_source(source_type: str, url: str, payload: str, run_id: int) -> int:
    """Insert into sources with content_hash dedup; return source_id."""
    digest = hashlib.sha256(payload.encode()).hexdigest()
    # INSERT ... ON CONFLICT (source_type, content_hash) DO NOTHING RETURNING source_id
    ...


@DBOS.step()
def verify_and_store_promises(document_id: int, chunk_offset: int,
                              full_text: str, extracted: ExtractionResult) -> dict:
    """THE anti-hallucination gate. Only exact-match quotes are stored verified."""
    stored, rejected = 0, 0
    for p in extracted.promises:
        abs_start = chunk_offset + p.char_start
        abs_end = chunk_offset + p.char_end
        if full_text[abs_start:abs_end] == p.verbatim_quote:
            pass  # store with quote_verified=TRUE, offsets as-is
        elif (idx := full_text.find(p.verbatim_quote)) != -1:
            abs_start, abs_end = idx, idx + len(p.verbatim_quote)  # offsets drifted; fix
        else:
            rejected += 1  # log to ingestion_runs.stats; DO NOT store as verified
            continue
        # INSERT INTO promises (... quote_verified=TRUE, is_scoreable = specificity != 'rhetorical')
        stored += 1
    return {"stored": stored, "rejected": rejected}


@DBOS.step()
def build_evaluation_context(politician_id: int, promise_id: int) -> dict:
    """SQL pre-digestion: NEVER raw payloads in prompts.
    Returns compact records, each carrying its DB id, e.g.:
      votes:     [{id: 'vote:8812', bill: 'H.R.123', desc: ..., position: 'Yea', date: ...}]
      donations: [{id: 'donation:991', industry: 'Oil & Gas', total: 45000, cycle: 2026}]
      filings:   [{id: 'filing:<uuid>', client: ..., issue: ..., bills: [...]}]
    Filter votes/filings by promise topic; cap each list (~top 15) to keep
    the context small and the citations checkable."""
    ...


# ---------------------------------------------------------------------------
# Per-candidate workflow — crash-safe: on resume, completed steps are skipped.
# ---------------------------------------------------------------------------
@DBOS.workflow()
async def process_candidate(politician_id: int, cycle: int = 2026) -> dict:
    run_id = start_ingestion_run("candidate_full", politician_id)  # step
    stats = {}

    # 1. Finance (all candidates)
    for fec_id in get_fec_ids(politician_id):                      # step
        payload = await fetch_fec_incremental(fec_id)
        store_source("fec_api", f".../{fec_id}/totals/", str(payload), run_id)
        upsert_finance_rollups(politician_id, payload)             # step

    # 2. Votes (incumbents only)
    if is_incumbent(politician_id, cycle):                         # step
        await sync_votes_from_congress_gov(politician_id)          # step(s)

    # 3. Documents -> 4. Extraction -> verification
    for doc in pending_documents(politician_id):                   # step
        for chunk_offset, chunk_text in chunk_document(doc.full_text):
            result = await extraction_agent.run(chunk_text)        # durable via step wrapper
            stats |= verify_and_store_promises(doc.document_id, chunk_offset,
                                               doc.full_text, result.output)

    # 5. Evaluation (scoreable, verified promises only)
    for promise in scoreable_unevaluated_promises(politician_id):  # step
        ctx = build_evaluation_context(politician_id, promise.id)
        _current_allowed_ids.set(collect_ids(ctx))
        result = await evaluation_agent.run(render_eval_prompt(promise, ctx))
        # Second, independent validation in code (not just the output_validator):
        # for each cited id, check the record's content actually matches the
        # stated direction where checkable; then insert promise_evaluations +
        # evaluation_evidence rows with validated flags.
        persist_evaluation(promise.id, result.output,
                           model_name=local_model.model_name,
                           prompt_version="evaluate_v1")           # step

    finish_ingestion_run(run_id, "succeeded", stats)               # step
    return stats


# ---------------------------------------------------------------------------
# Coordinator — enqueue everything; DBOS drains the queue durably.
# ---------------------------------------------------------------------------
@DBOS.workflow()
def weekly_run(cycle: int = 2026) -> None:
    for politician_id in all_active_candidacies(cycle):            # step
        candidate_queue.enqueue(process_candidate, politician_id, cycle)


if __name__ == "__main__":
    DBOS.launch()
    weekly_run()
    # keep process alive to drain queue; or run under `dbos start`
