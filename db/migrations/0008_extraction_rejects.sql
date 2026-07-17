-- 0008: persist rejected extraction quotes for QA.
--
-- Lesson from the v1 pilot: rejects were only logged to stderr, and the
-- log lines were lost to display filtering — 24 rejections with no record
-- of WHAT was rejected. Rejections are data: they measure prompt quality
-- per model/prompt_version and prove the gate is working. Append-only;
-- rows are never displayed in the product.

CREATE TABLE extraction_rejects (
    reject_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id    BIGINT NOT NULL REFERENCES documents (document_id),
    politician_id  BIGINT NOT NULL REFERENCES politicians (politician_id),
    rejected_quote TEXT   NOT NULL,
    chunk_offset   INTEGER NOT NULL,
    model_name     TEXT   NOT NULL,
    prompt_version TEXT   NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX extraction_rejects_version_idx
    ON extraction_rejects (prompt_version, model_name);
