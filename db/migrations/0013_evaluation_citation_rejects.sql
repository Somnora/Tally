-- 0013: keep the citations that could not be stored as evidence.
--
-- evaluation_evidence points at real records through real foreign keys,
-- which is exactly the property that makes invariant 2 enforceable. It also
-- means the single most interesting failure, a model citing a vote_id that
-- does not exist, cannot be written to that table at all: the FK rejects it.
--
-- Losing that to a log line would repeat a mistake this project already
-- made once. The v1 extraction pilot discarded its rejected quotes through
-- log filtering and the evidence of what the prompt was doing wrong went
-- with them, which is why extraction_rejects exists. This table is the same
-- idea for evaluation: a fabricated citation is the clearest possible signal
-- that a prompt or a model is unfit, so it is kept, attributed, and
-- countable.
--
-- Deliberately no foreign key on cited_record_id. The whole point is that
-- the record is not there.

CREATE TABLE evaluation_citation_rejects (
    reject_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id    BIGINT NOT NULL REFERENCES promise_evaluations (evaluation_id)
                         ON DELETE CASCADE,
    kind             TEXT   NOT NULL,
    cited_record_id  BIGINT NOT NULL,   -- no FK on purpose: it may not exist
    direction        TEXT   NOT NULL,
    reason           TEXT   NOT NULL,   -- slug from pipeline.evidence
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX evaluation_citation_rejects_reason_idx
    ON evaluation_citation_rejects (reason);
CREATE INDEX evaluation_citation_rejects_evaluation_idx
    ON evaluation_citation_rejects (evaluation_id);
