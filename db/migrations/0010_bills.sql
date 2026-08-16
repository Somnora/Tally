-- 0010: bill metadata, so votes can be evaluated against promises.
--
-- Why this exists: voting_records stores a position ("nay"), a procedural
-- question ("On Passage"), and a bill number ("HR 8595"). vote_description
-- is NULL for all 344,425 rows. That is enough to display a vote but NOT
-- enough to evaluate one against a promise: nothing in the row says what
-- HR 8595 is ABOUT. Without a title and subjects, the evaluation stage can
-- only return 'unverifiable', or worse, recall the bill from the model's
-- pretraining and cite it as evidence. That second failure mode is exactly
-- what invariant 2 (no uncited evaluations) forbids, so the topical text
-- has to come from Congress.gov and be stored with provenance.
--
-- bill_key is the join. voting_records writes the same bill number several
-- ways ("HR 8595" and "H.R. 8595" are the same bill), so both sides
-- normalize to one form: letters uppercased, punctuation and spaces
-- dropped, a hyphen before the number. HR-8595. Nominations (PN11-22) are
-- not bills and resolve to NULL, which keeps them out of the join.

ALTER TABLE voting_records ADD COLUMN bill_key TEXT
    GENERATED ALWAYS AS (
        CASE
            WHEN bill_number ~ '^[A-Za-z.]+ ?[0-9]+$'
             AND upper(regexp_replace(bill_number, '[^A-Za-z]', '', 'g')) <> 'PN'
            THEN upper(regexp_replace(bill_number, '[^A-Za-z]', '', 'g'))
                 || '-' || regexp_replace(bill_number, '[^0-9]', '', 'g')
        END
    ) STORED;

CREATE INDEX voting_records_bill_key_idx ON voting_records (congress, bill_key);

CREATE TABLE bills (
    bill_id            BIGSERIAL PRIMARY KEY,
    congress           SMALLINT NOT NULL,
    bill_key           TEXT NOT NULL,     -- normalized; joins voting_records
    bill_type          TEXT NOT NULL,     -- API path segment: hr, s, hres, sjres, ...
    bill_number        INTEGER NOT NULL,
    title              TEXT,
    policy_area        TEXT,              -- Congress.gov's single top-level area
    subjects           TEXT[] NOT NULL DEFAULT '{}',  -- legislative subject terms
    summary_text       TEXT,              -- CRS summary, newest version
    introduced_date    DATE,
    latest_action      TEXT,
    latest_action_date DATE,
    sponsor_bioguide   TEXT,
    congress_gov_url   TEXT NOT NULL,
    source_id          BIGINT REFERENCES sources (source_id),
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- A bill number is unique only within its congress.
    UNIQUE (congress, bill_key)
);

-- The evaluation stage's vote pre-filter selects by subject term, so this
-- index is the one that has to be fast. GIN over the array supports
-- "subjects && ARRAY['Health care coverage and access']" containment.
CREATE INDEX bills_subjects_idx ON bills USING GIN (subjects);
CREATE INDEX bills_policy_area_idx ON bills (policy_area);
