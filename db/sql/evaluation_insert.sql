-- One evaluation row. consistency_score is NULL for pending and unverifiable;
-- the schema CHECK added in migration 0011 enforces that pairing, so a score
-- can never ride along with a verdict the evidence did not support.
INSERT INTO promise_evaluations
    (promise_id, status, consistency_score, llm_reasoning, model_name, prompt_version)
VALUES (%(promise_id)s, %(status)s, %(consistency_score)s, %(llm_reasoning)s,
        %(model_name)s, %(prompt_version)s)
RETURNING evaluation_id
