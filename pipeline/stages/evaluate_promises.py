"""Stage 5 — evaluate_promises: score verified promises against the record.

Milestone 5. For each scoreable, verified, unevaluated promise: build a
pre-digested context (SQL-aggregated donor summary + topic-filtered vote
list, every item carrying its DB id — never raw payloads), ask the model for
a status + score + cited evidence ids, then validate every citation in code
(record exists AND supports the stated direction) before flagging evidence
validated. The app_export_evaluations view already refuses evaluations with
any unvalidated evidence.
"""

from pipeline.db import Connection
from pipeline.stages import StageStats


def evaluate_promises(conn: Connection, politician_id: int, run_id: int) -> StageStats:
    """Evaluate scoreable promises for one candidate.

    Contract: evaluations are append-only (DB-enforced); new model/prompt =
    new row with is_current flipped; every evaluation_evidence row points at
    a real record via a real FK. LLM calls require Settings.vllm_base_url.
    """
    raise NotImplementedError("Milestone 5")
