-- 0018: a broken-promise verdict does not publish on a model's say-so.
--
-- "X broke their promise" is the most damaging thing this product can say
-- about a named person, and today it said it wrongly at scale: evaluate_v3
-- produced 132 broken verdicts resting on inverted vote directions, and a
-- single dropped field in a renderer had put an earlier batch of them one
-- command away from the public site. Both times the only thing between a
-- false accusation and publication was somebody noticing.
--
-- So the strongest claim now needs a person. Every other status still
-- publishes automatically: this is not a review queue for the pipeline, it is
-- a stop on the one verdict that accuses.
--
-- Recorded, not just flagged. Who signed off and when is itself part of the
-- provenance chain the project promises, and "approved" without a name is the
-- kind of assurance that cannot be audited later.

ALTER TABLE promise_evaluations
    ADD COLUMN reviewed_at  TIMESTAMPTZ,
    ADD COLUMN reviewed_by  TEXT,
    ADD COLUMN review_note  TEXT;

-- Either both or neither: a timestamp with no reviewer is an unsigned
-- approval, which is worse than none because it looks like one.
ALTER TABLE promise_evaluations ADD CONSTRAINT promise_evaluations_review_check CHECK (
    (reviewed_at IS NULL AND reviewed_by IS NULL)
    OR (reviewed_at IS NOT NULL AND reviewed_by IS NOT NULL AND length(reviewed_by) > 0)
);

CREATE INDEX promise_evaluations_awaiting_review_idx
    ON promise_evaluations (status)
    WHERE status = 'broken' AND reviewed_at IS NULL;

-- Rebuild the export view with the sign-off requirement folded in. Every
-- pre-existing clause is carried over deliberately and verbatim: current
-- only, at least one citation, and no unvalidated citation. Dropping the
-- "at least one citation" clause while rewriting this view let unevidenced
-- pending verdicts export, which a test caught immediately.
DROP VIEW IF EXISTS app_export_evaluations;

CREATE VIEW app_export_evaluations AS
SELECT e.evaluation_id, e.promise_id, e.status, e.consistency_score,
       e.llm_reasoning, e.model_name, e.prompt_version, e.created_at
FROM promise_evaluations e
WHERE e.is_current
  AND EXISTS (
      SELECT 1 FROM evaluation_evidence ev
      WHERE ev.evaluation_id = e.evaluation_id
  )
  AND NOT EXISTS (
      SELECT 1 FROM evaluation_evidence ev
      WHERE ev.evaluation_id = e.evaluation_id AND NOT ev.validated
  )
  -- The new clause. A broken verdict with no signature never leaves the
  -- database, however well evidenced it looks.
  AND (e.status <> 'broken' OR e.reviewed_at IS NOT NULL);
