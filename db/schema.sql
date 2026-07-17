-- Base schema: core entities for the civic transparency platform.
-- Applied as baseline migration 0001. Provenance FKs (source_id -> sources)
-- are added in schema_additions.sql because the sources table lives there.
--
-- Conventions:
--   * Natural keys (FEC ids, bioguide) get UNIQUE constraints so loaders can
--     upsert idempotently with ON CONFLICT.
--   * source_id columns are NOT NULL wherever the row represents an ingested
--     fact ("no source, no store"). races is the exception: the 435 House
--     races + Senate classes are structural facts, not ingested ones.

-- ---------------------------------------------------------------------------
-- politicians: one row per person (incumbent or challenger).
-- Incumbents are keyed by bioguide_id (via id_crosswalk). Challengers who
-- aren't in the crosswalk yet are keyed provisionally by fec_candidate_id and
-- flagged needs_linkage for later merge.
-- ---------------------------------------------------------------------------
CREATE TABLE politicians (
    politician_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    full_name        TEXT        NOT NULL,
    party            TEXT,                 -- FEC party code (DEM, REP, IND, ...)
    state            CHAR(2),
    bioguide_id      TEXT UNIQUE,          -- natural key for current/former members
    fec_candidate_id TEXT UNIQUE,          -- provisional natural key for unmatched candidates
    needs_linkage    BOOLEAN     NOT NULL DEFAULT FALSE,  -- no crosswalk match yet
    source_id        BIGINT      NOT NULL, -- FK added in schema_additions.sql
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (bioguide_id IS NOT NULL OR fec_candidate_id IS NOT NULL)
);

-- ---------------------------------------------------------------------------
-- committees: FEC committee master (cm file). PACs, party committees, and
-- candidate committees all live here; `pacs` below is a filtered view.
-- ---------------------------------------------------------------------------
CREATE TABLE committees (
    cmte_id           TEXT PRIMARY KEY,    -- FEC committee id (C00xxxxxx)
    name              TEXT        NOT NULL,
    cmte_type         CHAR(1),             -- FEC CMTE_TP (N/Q = PAC, O = super PAC, ...)
    cmte_designation  CHAR(1),             -- FEC CMTE_DSGN (P = principal campaign, ...)
    party             TEXT,                -- FEC CMTE_PTY_AFFILIATION
    connected_org     TEXT,                -- sponsor/connected organization
    cand_id           TEXT,                -- linked candidate id for authorized committees
    state             CHAR(2),
    cycle             SMALLINT    NOT NULL, -- election cycle this row was last seen in
    source_id         BIGINT      NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- pacs: the subset of committees that are PACs (traditional, super, hybrid).
-- A view, not a table — one source of truth, no sync problem.
CREATE VIEW pacs AS
SELECT * FROM committees
WHERE cmte_type IN ('N', 'Q', 'O', 'V', 'W');

-- ---------------------------------------------------------------------------
-- donations: itemized contributions (loaded in Milestone 2; schema now so
-- evaluation_evidence can reference it). Natural key: FEC sub_id.
-- ---------------------------------------------------------------------------
CREATE TABLE donations (
    donation_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fec_sub_id              TEXT UNIQUE NOT NULL,  -- FEC record id from bulk itemized files
    recipient_cmte_id       TEXT        NOT NULL REFERENCES committees (cmte_id),
    politician_id           BIGINT      REFERENCES politicians (politician_id),
    contributor_name        TEXT,
    contributor_cmte_id     TEXT        REFERENCES committees (cmte_id),
    industry_code           TEXT,                  -- CRP catcode; FK added in additions
    amount                  NUMERIC(14, 2) NOT NULL,
    contributed_at          DATE,
    cycle                   SMALLINT    NOT NULL,
    source_id               BIGINT      NOT NULL,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX donations_politician_cycle_idx ON donations (politician_id, cycle);

-- ---------------------------------------------------------------------------
-- voting_records: one row per member per roll call (incumbents only).
-- congress_gov_url is the receipt: every displayed vote deep-links there.
-- ---------------------------------------------------------------------------
CREATE TABLE voting_records (
    vote_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    politician_id     BIGINT      NOT NULL REFERENCES politicians (politician_id),
    congress          SMALLINT    NOT NULL,          -- e.g. 119
    chamber           TEXT        NOT NULL CHECK (chamber IN ('house', 'senate')),
    session           SMALLINT    NOT NULL,          -- 1 or 2
    roll_call_number  INTEGER     NOT NULL,
    bill_number       TEXT,                          -- e.g. 'H.R.123'
    vote_question     TEXT,
    vote_description  TEXT,
    position          TEXT        NOT NULL CHECK (position IN ('yea', 'nay', 'present', 'not_voting')),
    voted_at          DATE,
    congress_gov_url  TEXT        NOT NULL,
    source_id         BIGINT      NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (politician_id, congress, chamber, session, roll_call_number)
);

-- ---------------------------------------------------------------------------
-- promises: extracted from documents, displayable ONLY when quote_verified.
-- The FK to documents is added in schema_additions.sql (documents lives there).
-- ---------------------------------------------------------------------------
CREATE TABLE promises (
    promise_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    politician_id   BIGINT      NOT NULL REFERENCES politicians (politician_id),
    document_id     BIGINT      NOT NULL,  -- FK added in schema_additions.sql
    verbatim_quote  TEXT        NOT NULL,
    char_start      INTEGER     NOT NULL CHECK (char_start >= 0),
    char_end        INTEGER     NOT NULL,
    quote_verified  BOOLEAN     NOT NULL DEFAULT FALSE,  -- set ONLY by exact-match verification
    topic           TEXT        NOT NULL,
    specificity     TEXT        NOT NULL CHECK (specificity IN ('measurable', 'directional', 'rhetorical')),
    is_scoreable    BOOLEAN     NOT NULL DEFAULT FALSE,
    model_name      TEXT        NOT NULL,  -- extraction provenance
    prompt_version  TEXT        NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT promises_offsets_ordered CHECK (char_end > char_start),
    -- Editorial invariant: rhetorical promises are displayed but never scored.
    CONSTRAINT promises_rhetorical_never_scoreable
        CHECK (NOT (specificity = 'rhetorical' AND is_scoreable)),
    UNIQUE (document_id, char_start, char_end)
);

CREATE INDEX promises_politician_idx ON promises (politician_id);

-- ---------------------------------------------------------------------------
-- races: every federal contest in a cycle (435 House + Senate class up).
-- district is '00'..'53' for House ('00' = at-large); NULL for Senate.
-- NULLS NOT DISTINCT so (2026, 'GA', 'senate', NULL, 2, false) can't repeat.
-- ---------------------------------------------------------------------------
CREATE TABLE races (
    race_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cycle         SMALLINT NOT NULL,
    state         CHAR(2)  NOT NULL,
    office        TEXT     NOT NULL CHECK (office IN ('house', 'senate')),
    district      CHAR(2),           -- House only
    senate_class  SMALLINT CHECK (senate_class IN (1, 2, 3)),  -- Senate only
    is_special    BOOLEAN  NOT NULL DEFAULT FALSE,
    source_id     BIGINT,            -- nullable: House races are structural, not ingested
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((office = 'house' AND district IS NOT NULL AND senate_class IS NULL)
        OR (office = 'senate' AND district IS NULL AND senate_class IS NOT NULL)),
    CONSTRAINT races_natural_key
        UNIQUE NULLS NOT DISTINCT (cycle, state, office, district, senate_class, is_special)
);

-- ---------------------------------------------------------------------------
-- candidacies: a politician running in a race. Natural key: the FEC candidate
-- id within the race (FEC issues distinct ids per office sought).
-- ---------------------------------------------------------------------------
CREATE TABLE candidacies (
    candidacy_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    race_id               BIGINT   NOT NULL REFERENCES races (race_id),
    politician_id         BIGINT   NOT NULL REFERENCES politicians (politician_id),
    fec_candidate_id      TEXT     NOT NULL,
    party                 TEXT,
    incumbent_challenger  CHAR(1)  CHECK (incumbent_challenger IN ('I', 'C', 'O')),  -- FEC CAND_ICI
    cand_status           CHAR(1),  -- FEC CAND_STATUS (C=statutory candidate, F=future, N, P)
    principal_cmte_id     TEXT     REFERENCES committees (cmte_id),
    source_id             BIGINT   NOT NULL,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (race_id, fec_candidate_id),
    UNIQUE (race_id, politician_id)
);

CREATE INDEX candidacies_politician_idx ON candidacies (politician_id);
