-- Schema additions: provenance, documents, LLM evaluation machinery, lobbying,
-- reference tables, and the app_export_* views. Layers on schema.sql
-- (applied as baseline migration 0002).

-- ---------------------------------------------------------------------------
-- sources: THE provenance table. Every ingested fact points here.
-- Small payloads are stored inline (raw_payload); bulk archives too big for
-- a DB row live on disk under data/raw/ with the path recorded (raw_path).
-- Either way the content_hash pins exactly what was retrieved.
-- ---------------------------------------------------------------------------
CREATE TABLE sources (
    source_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_type   TEXT        NOT NULL,   -- 'fec_bulk_cn', 'congress_legislators', ...
    url           TEXT        NOT NULL,
    retrieved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    content_hash  TEXT        NOT NULL,   -- sha256 hex of the raw bytes
    raw_payload   BYTEA,
    raw_path      TEXT,
    UNIQUE (source_type, content_hash),
    CHECK (raw_payload IS NOT NULL OR raw_path IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- documents: transcripts, press releases, campaign pages, Wayback snapshots.
-- full_text is what promises are verified against — it is immutable once
-- promises reference it (offsets would silently break otherwise).
-- ---------------------------------------------------------------------------
CREATE TABLE documents (
    document_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    politician_id  BIGINT      NOT NULL REFERENCES politicians (politician_id),
    source_id      BIGINT      NOT NULL REFERENCES sources (source_id),
    doc_type       TEXT        NOT NULL CHECK (doc_type IN
                       ('youtube_transcript', 'press_release', 'campaign_site',
                        'wayback_snapshot', 'debate_transcript', 'other')),
    title          TEXT,
    url            TEXT        NOT NULL,
    published_at   TIMESTAMPTZ,
    full_text      TEXT        NOT NULL,
    content_hash   TEXT        NOT NULL,   -- sha256 of full_text, for dedup
    transcribed_by TEXT,                   -- e.g. 'faster-whisper-large-v3'; NULL if not A/V
    meta           JSONB       NOT NULL DEFAULT '{}',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (politician_id, content_hash)
);

-- ---------------------------------------------------------------------------
-- ingestion_runs: one row per pipeline run (per candidate or per loader).
-- stats collects row counts, rejects, token/cost figures.
-- ---------------------------------------------------------------------------
CREATE TABLE ingestion_runs (
    run_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_type       TEXT        NOT NULL,   -- 'seed_crosswalk', 'fec_bulk_load', 'candidate_full', ...
    politician_id  BIGINT      REFERENCES politicians (politician_id),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at    TIMESTAMPTZ,
    status         TEXT        NOT NULL DEFAULT 'running'
                       CHECK (status IN ('running', 'succeeded', 'failed')),
    stats          JSONB       NOT NULL DEFAULT '{}',
    error          TEXT
);

-- ---------------------------------------------------------------------------
-- id_crosswalk: the ID Rosetta Stone (unitedstates/congress-legislators).
-- fec_candidate_ids is an array: one person accumulates ids across offices.
-- ---------------------------------------------------------------------------
CREATE TABLE id_crosswalk (
    bioguide_id        TEXT PRIMARY KEY,
    full_name          TEXT   NOT NULL,
    fec_candidate_ids  TEXT[] NOT NULL DEFAULT '{}',
    govtrack_id        INTEGER,
    icpsr_id           INTEGER,             -- joins to Voteview DW-NOMINATE
    opensecrets_id     TEXT,                -- CRP id, joins to OpenSecrets bulk data
    source_id          BIGINT NOT NULL REFERENCES sources (source_id),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Fast "which member owns this FEC id" lookups during candidate loading.
CREATE INDEX id_crosswalk_fec_ids_idx ON id_crosswalk USING GIN (fec_candidate_ids);

-- ---------------------------------------------------------------------------
-- industry_codes: OpenSecrets CRP category codes ("catcodes").
-- ---------------------------------------------------------------------------
CREATE TABLE industry_codes (
    catcode    TEXT PRIMARY KEY,   -- e.g. 'E1100'
    catname    TEXT NOT NULL,      -- e.g. 'Oil & Gas'
    catorder   TEXT,
    industry   TEXT,
    sector     TEXT,
    sector_long TEXT,
    source_id  BIGINT NOT NULL REFERENCES sources (source_id)
);

-- ---------------------------------------------------------------------------
-- lobbying_filings + lobbying_issues (Senate LDA API; loaded in a later
-- milestone, schema now so evidence can reference filings).
-- ---------------------------------------------------------------------------
CREATE TABLE lobbying_filings (
    filing_uuid     UUID PRIMARY KEY,       -- LDA's own natural key
    registrant_name TEXT     NOT NULL,
    client_name     TEXT     NOT NULL,
    filing_year     SMALLINT NOT NULL,
    filing_period   TEXT,
    filing_type     TEXT,
    income          NUMERIC(14, 2),
    expenses        NUMERIC(14, 2),
    source_id       BIGINT   NOT NULL REFERENCES sources (source_id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE lobbying_issues (
    issue_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    filing_uuid     UUID   NOT NULL REFERENCES lobbying_filings (filing_uuid) ON DELETE CASCADE,
    issue_area_code TEXT,                    -- LDA general issue area (e.g. 'ENG')
    description     TEXT,
    bill_numbers    TEXT[] NOT NULL DEFAULT '{}'  -- parsed out of description
);

-- ---------------------------------------------------------------------------
-- promise_evaluations: APPEND-ONLY. A new model or prompt version means a new
-- row with is_current = TRUE and the old row flipped to FALSE — never an
-- UPDATE of scores in place. The trigger below enforces that at the DB level.
-- ---------------------------------------------------------------------------
CREATE TABLE promise_evaluations (
    evaluation_id     BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    promise_id        BIGINT   NOT NULL REFERENCES promises (promise_id),
    status            TEXT     NOT NULL CHECK (status IN
                          ('completed', 'in_progress', 'broken', 'pending', 'unverifiable')),
    consistency_score SMALLINT NOT NULL CHECK (consistency_score BETWEEN 1 AND 100),
    llm_reasoning     TEXT     NOT NULL,
    model_name        TEXT     NOT NULL,
    prompt_version    TEXT     NOT NULL,
    is_current        BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- At most one current evaluation per promise.
CREATE UNIQUE INDEX promise_evaluations_current_idx
    ON promise_evaluations (promise_id) WHERE is_current;

-- Append-only guard: the only permitted UPDATE is flipping is_current.
CREATE FUNCTION promise_evaluations_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.promise_id, NEW.status, NEW.consistency_score, NEW.llm_reasoning,
        NEW.model_name, NEW.prompt_version, NEW.created_at)
       IS DISTINCT FROM
       (OLD.promise_id, OLD.status, OLD.consistency_score, OLD.llm_reasoning,
        OLD.model_name, OLD.prompt_version, OLD.created_at) THEN
        RAISE EXCEPTION 'promise_evaluations is append-only: only is_current may change';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER promise_evaluations_append_only_trg
    BEFORE UPDATE ON promise_evaluations
    FOR EACH ROW EXECUTE FUNCTION promise_evaluations_append_only();

-- ---------------------------------------------------------------------------
-- evaluation_evidence: citations backing an evaluation. Polymorphic by kind,
-- but each kind gets a REAL foreign key column — "the cited record exists"
-- is enforced by the database, not just by application code.
-- validated = TRUE additionally means code checked the record supports the
-- stated direction.
-- ---------------------------------------------------------------------------
CREATE TABLE evaluation_evidence (
    evidence_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    evaluation_id BIGINT NOT NULL REFERENCES promise_evaluations (evaluation_id) ON DELETE CASCADE,
    kind          TEXT   NOT NULL CHECK (kind IN ('vote', 'donation', 'lobbying_filing', 'document')),
    vote_id       BIGINT REFERENCES voting_records (vote_id),
    donation_id   BIGINT REFERENCES donations (donation_id),
    filing_uuid   UUID   REFERENCES lobbying_filings (filing_uuid),
    document_id   BIGINT REFERENCES documents (document_id),
    direction     TEXT   NOT NULL CHECK (direction IN ('supports', 'contradicts', 'contextual')),
    validated     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Exactly the id column matching `kind` must be set, all others NULL.
    CHECK (
        (kind = 'vote'            AND vote_id IS NOT NULL AND donation_id IS NULL AND filing_uuid IS NULL AND document_id IS NULL) OR
        (kind = 'donation'        AND donation_id IS NOT NULL AND vote_id IS NULL AND filing_uuid IS NULL AND document_id IS NULL) OR
        (kind = 'lobbying_filing' AND filing_uuid IS NOT NULL AND vote_id IS NULL AND donation_id IS NULL AND document_id IS NULL) OR
        (kind = 'document'        AND document_id IS NOT NULL AND vote_id IS NULL AND donation_id IS NULL AND filing_uuid IS NULL)
    )
);

CREATE INDEX evaluation_evidence_evaluation_idx ON evaluation_evidence (evaluation_id);

-- ---------------------------------------------------------------------------
-- Provenance + cross-file FKs onto the base tables (deferred from schema.sql
-- because sources/documents/industry_codes are created in this file).
-- ---------------------------------------------------------------------------
ALTER TABLE politicians    ADD CONSTRAINT politicians_source_fk    FOREIGN KEY (source_id) REFERENCES sources (source_id);
ALTER TABLE committees     ADD CONSTRAINT committees_source_fk     FOREIGN KEY (source_id) REFERENCES sources (source_id);
ALTER TABLE donations      ADD CONSTRAINT donations_source_fk      FOREIGN KEY (source_id) REFERENCES sources (source_id);
ALTER TABLE donations      ADD CONSTRAINT donations_industry_fk    FOREIGN KEY (industry_code) REFERENCES industry_codes (catcode);
ALTER TABLE voting_records ADD CONSTRAINT voting_records_source_fk FOREIGN KEY (source_id) REFERENCES sources (source_id);
ALTER TABLE races          ADD CONSTRAINT races_source_fk          FOREIGN KEY (source_id) REFERENCES sources (source_id);
ALTER TABLE candidacies    ADD CONSTRAINT candidacies_source_fk    FOREIGN KEY (source_id) REFERENCES sources (source_id);
ALTER TABLE promises       ADD CONSTRAINT promises_document_fk     FOREIGN KEY (document_id) REFERENCES documents (document_id);

-- ---------------------------------------------------------------------------
-- app_export_* views: EXACTLY what the public SQLite snapshot may contain.
-- Minimal placeholders for now — but the gates are real from day one:
--   * promises: only quote_verified rows leave the building.
--   * evaluations: only current rows where every citation is validated
--     (and at least one citation exists).
-- ---------------------------------------------------------------------------
CREATE VIEW app_export_promises AS
SELECT p.promise_id, p.politician_id, p.document_id, p.verbatim_quote,
       p.char_start, p.char_end, p.topic, p.specificity, p.is_scoreable
FROM promises p
WHERE p.quote_verified;

CREATE VIEW app_export_evaluations AS
SELECT e.evaluation_id, e.promise_id, e.status, e.consistency_score,
       e.llm_reasoning, e.model_name, e.prompt_version, e.created_at
FROM promise_evaluations e
WHERE e.is_current
  AND EXISTS (SELECT 1 FROM evaluation_evidence ev
              WHERE ev.evaluation_id = e.evaluation_id)
  AND NOT EXISTS (SELECT 1 FROM evaluation_evidence ev
                  WHERE ev.evaluation_id = e.evaluation_id
                    AND NOT ev.validated);

CREATE VIEW app_export_candidacies AS
SELECT c.candidacy_id, c.race_id, c.politician_id, c.party,
       c.incumbent_challenger, r.cycle, r.state, r.office, r.district,
       p.full_name, p.bioguide_id
FROM candidacies c
JOIN races r USING (race_id)
JOIN politicians p USING (politician_id);
