"""Pipeline stages (Milestones 2+). STUBS ONLY in Milestone 1.

Each stage will become one or more @DBOS.step() functions inside the
per-candidate workflow (see ingestion_blueprint.py for the pattern). Before
implementing any stage, verify the INSTALLED dbos / pydantic-ai versions'
current APIs — the blueprint warns its names may have drifted.

Execution-location note: stages that need a GPU (transcription in
sync_documents, LLM calls in extract_promises / evaluate_promises) will run
on Lambda instances orchestrated externally (Manifold), not on this machine.
The contracts below are location-agnostic on purpose: every stage reads and
writes the same Postgres tables through pipeline.db, and everything ingested
passes the same sources/verification gates no matter where the code ran.
Callers must check Settings.gpu_available / vllm_base_url before invoking
GPU-dependent paths.
"""

# Per-stage row/reject/cost counters, merged into ingestion_runs.stats.
StageStats = dict[str, int]
