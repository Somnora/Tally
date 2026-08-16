"""Stage 5 - evaluate_promises: compare verified promises to the record.

Flow per promise:
  topically filtered vote list (SQL, one vote per bill, omnibus and
  procedural marked)
    -> evaluation agent (local model, temperature 0, versioned prompt)
    -> pipeline.evidence validates every cited id against the DATABASE
    -> evaluation + citations stored; failures kept with is_current = FALSE

The model never sees a raw payload and never sees the whole voting record.
It sees a short pre-digested list where every line carries its vote_id, so
a citation is either a real id from that list or a detectable invention.

Two gates decide what becomes public, and neither trusts the model:
citations are validated in code (record exists, belongs to this member,
direction the record can actually carry), and the status has to agree with
whatever survived. An evaluation failing either is stored with
is_current = FALSE, so it is retained for review but can never satisfy the
app_export_evaluations view.

The model is injectable, exactly as in extract_promises: tests pass
pydantic-ai's TestModel or FunctionModel, production builds an
OpenAIChatModel against Settings.vllm_base_url.
"""

import logging
from collections import Counter
from functools import cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from pipeline import db, evidence
from pipeline.config import get_settings
from pipeline.stages import StageStats

logger = logging.getLogger(__name__)

PROMPT_VERSION = "evaluate_v1"
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"

# How many votes reach the prompt. Enough to cover a topic, small enough that
# the model reads all of them rather than skimming.
MAX_VOTES = 25

Status = Literal["completed", "in_progress", "broken", "pending", "unverifiable"]


class EvidenceItem(BaseModel):
    kind: Literal["vote"] = "vote"
    id: int = Field(description="A vote_id copied from the supplied list.")
    position: Literal["yea", "nay"] = Field(
        description="The position shown for that vote_id, copied exactly."
    )
    direction: Literal["supports", "contradicts", "contextual"]


class EvaluationResult(BaseModel):
    status: Status
    consistency_score: int | None = Field(
        default=None,
        description="1-100 for completed/in_progress/broken; null otherwise.",
    )
    llm_reasoning: str
    # Pydantic copies mutable defaults per instance, so the empty list is safe.
    evidence: list[EvidenceItem] = []


@cache
def load_prompt(version: str) -> str:
    return (PROMPTS_DIR / f"{version}.txt").read_text(encoding="utf-8")


def build_agent(model: Model | None = None) -> Agent[None, EvaluationResult]:
    if model is None:
        settings = get_settings()
        if not settings.vllm_base_url or not settings.local_model:
            raise RuntimeError(
                "evaluation needs a model endpoint: set VLLM_BASE_URL and "
                "LOCAL_MODEL in .env (start a vllm-serve/sglang-serve job in Manifold)"
            )
        model = OpenAIChatModel(
            settings.local_model,
            provider=OpenAIProvider(base_url=settings.vllm_base_url, api_key="not-needed"),
        )
    return Agent(
        model,
        output_type=EvaluationResult,
        system_prompt=load_prompt(PROMPT_VERSION),
        model_settings={"temperature": 0.0},
    )


def render_votes(votes: list[db.VoteContext]) -> str:
    """The vote list as the model sees it. One line per vote, id first.

    OMNIBUS and PROCEDURAL are stated inline rather than left implicit,
    because the prompt restricts both to contextual citations and the model
    cannot apply that rule to a fact it was not told.
    """
    lines: list[str] = []
    for vote in votes:
        flags = ""
        if vote.is_omnibus:
            flags += " [OMNIBUS: bundles many unrelated provisions]"
        if vote.is_procedural:
            flags += " [PROCEDURAL: a vote on process, not policy]"
        # Positions render lowercase so the text the model copies is exactly
        # the token the output schema accepts. Showing "NAY" and requiring
        # "nay" costs a validation retry on every single citation.
        lines.append(
            f"[vote_id: {vote.vote_id}] {vote.bill_key} | voted {vote.position}"
            f" on '{vote.vote_question}' | {vote.voted_at}{flags}\n"
            f"    {vote.title or '(untitled)'}"
        )
    return "\n".join(lines)


def build_prompt(promise: db.PromiseForEvaluation, votes: list[db.VoteContext]) -> str:
    return (
        f"PROMISE\n"
        f"Quote: {promise.verbatim_quote}\n"
        f"Topic: {promise.topic}\n"
        f"Specificity: {promise.specificity}\n\n"
        f"VOTES BY THIS LEGISLATOR RELATED TO {promise.topic.upper()}\n"
        f"{render_votes(votes) if votes else '(none found)'}\n"
    )


def evaluate_promise(
    conn: db.Connection,
    agent: Agent[None, EvaluationResult],
    promise: db.PromiseForEvaluation,
    model_name: str,
    stats: StageStats,
) -> None:
    votes = db.votes_for_promise(conn, promise.politician_id, promise.topic, MAX_VOTES)
    if not votes:
        # No topically related votes at all. That is a real finding, not an
        # error, and it is recorded rather than skipped so the promise is not
        # re-evaluated every run.
        _store(conn, promise, EvaluationResult(
            status="pending", consistency_score=None,
            llm_reasoning=("No roll-call votes related to this topic were found in "
                           "the legislator's record for this Congress."),
            evidence=[],
        ), [], model_name, is_current=True, stats=stats)
        stats["no_related_votes"] += 1
        return

    result = agent.run_sync(build_prompt(promise, votes)).output
    stats["evaluated"] += 1

    claims = [
        evidence.ClaimedEvidence(
            kind=item.kind, record_id=item.id, direction=item.direction,
            claimed_position=item.position,
        )
        for item in result.evidence
    ]
    facts = db.vote_facts(conn, [c.record_id for c in claims if c.kind == "vote"])
    checks = evidence.check_citations(
        claims, politician_id=promise.politician_id, vote_facts=facts,
        # Only the votes this promise was actually shown are admissible.
        offered_vote_ids=frozenset(v.vote_id for v in votes),
    )
    coherent, coherence_reason = evidence.status_is_supported(result.status, checks)

    for check in checks:
        stats[f"citation_{check.reason}"] += 1
    rejected = [c for c in checks if not c.accepted]
    if rejected or not coherent:
        stats["evaluations_failed_validation"] += 1
        logger.warning(
            "promise %d: %d/%d citations rejected (%s); status %s %s",
            promise.promise_id, len(rejected), len(checks),
            ", ".join(sorted({c.reason for c in rejected})) or "none",
            result.status, coherence_reason,
        )
    else:
        stats["evaluations_validated"] += 1

    _store(conn, promise, result, checks, model_name,
           is_current=coherent and not rejected, stats=stats)


def _store(
    conn: db.Connection,
    promise: db.PromiseForEvaluation,
    result: EvaluationResult,
    checks: list[evidence.CitationCheck],
    model_name: str,
    *,
    is_current: bool,
    stats: StageStats,
) -> None:
    # The DB pairs status and score: a score is required for the evidenced
    # statuses and must be NULL for the others. Normalise here so a model that
    # returns 50 for 'unverifiable' fails validation rather than the insert.
    score = result.consistency_score
    if result.status in evidence.UNEVIDENCED_STATUSES:
        if score is not None:
            stats["score_dropped_for_unevidenced_status"] += 1
        score = None
    elif score is None:
        # An evidenced status with no score cannot be stored; the record said
        # something, so refusing to score it is incoherent.
        stats["missing_score_for_evidenced_status"] += 1
        is_current = False
        score = 50

    if is_current:
        db.supersede_current_evaluation(conn, promise.promise_id)
    evaluation_id = db.insert_evaluation(
        conn, promise_id=promise.promise_id, status=result.status,
        consistency_score=score, llm_reasoning=result.llm_reasoning,
        model_name=model_name, prompt_version=PROMPT_VERSION, is_current=is_current,
    )
    for check in checks:
        if evidence.can_store_as_evidence(check):
            # Stored even when rejected, so the export view's "no unvalidated
            # citation" rule catches it independently of is_current.
            db.insert_evaluation_evidence(
                conn, evaluation_id=evaluation_id, kind=check.claim.kind,
                record_id=check.claim.record_id, direction=check.claim.direction,
                validated=check.accepted,
            )
        else:
            # No row exists to point a foreign key at. Keep it as QA data
            # rather than losing the fabrication to a log line.
            db.insert_citation_reject(
                conn, evaluation_id=evaluation_id, kind=check.claim.kind,
                cited_record_id=check.claim.record_id,
                direction=check.claim.direction, reason=check.reason,
            )


def evaluate_promises(
    conn: db.Connection,
    politician_id: int,
    model_name: str,
    agent: Agent[None, EvaluationResult] | None = None,
) -> StageStats:
    """Evaluate every eligible promise for one member.

    Contract: evaluations are append-only (DB-enforced); a new model or
    prompt version means a new row with is_current flipped, never an edit.
    Every citation points at a real record through a real foreign key, and
    only citations code has checked are marked validated.
    """
    stats: StageStats = Counter()
    agent = agent or build_agent()
    promises = db.promises_for_evaluation(
        conn, politician_id, model_name=model_name, prompt_version=PROMPT_VERSION
    )
    stats["eligible"] = len(promises)
    for promise in promises:
        try:
            evaluate_promise(conn, agent, promise, model_name, stats)
        except Exception:
            # One promise the model cannot answer in schema must not abandon
            # the other twenty one. Same rule as one workflow per candidate:
            # a single failure is logged and counted, never fatal. The promise
            # stays unevaluated and is retried on the next run.
            stats["model_failures"] += 1
            logger.exception(
                "promise %d (%s) failed; continuing",
                promise.promise_id, promise.topic,
            )
    return stats
