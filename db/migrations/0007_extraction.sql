-- 0007: extraction bookkeeping on documents.
-- A document is (re)extracted when it has never been processed OR when the
-- prompt/model changed — extraction under a new prompt_version is a new
-- pass, never an edit of old promises (promises rows are keyed to the
-- version that produced them).

ALTER TABLE documents
    ADD COLUMN extracted_at             TIMESTAMPTZ,
    ADD COLUMN extraction_model         TEXT,
    ADD COLUMN extraction_prompt_version TEXT;

CREATE INDEX documents_unextracted_idx
    ON documents (politician_id) WHERE extracted_at IS NULL;
