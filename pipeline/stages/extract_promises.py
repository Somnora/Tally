"""Stage 4 — extract_promises: chunked LLM extraction + quote verification.

Milestone 4. Chunks documents (~2-3k tokens, ~200 overlap, chunk offsets
carried so extracted offsets become absolute), extracts candidate promises
with the local LLM (temperature 0, versioned prompt), then passes every
extraction through pipeline.verify.verify_quote — the gate that already
exists and is tested. Failed verifications are logged and dropped, never
stored as verified.
"""

from pipeline.db import Connection
from pipeline.stages import StageStats


def extract_promises(conn: Connection, politician_id: int, run_id: int) -> StageStats:
    """Extract and verify promises from a candidate's unprocessed documents.

    Contract: promises insert with quote_verified set from verify_quote's
    outcome only; specificity 'rhetorical' forces is_scoreable = FALSE (also
    DB-enforced); model_name and prompt_version are recorded on every row.
    LLM calls require Settings.vllm_base_url (GPU instance).
    """
    raise NotImplementedError("Milestone 4")
