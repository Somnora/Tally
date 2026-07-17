-- 0003: finance pipeline schema (Milestone 2).
-- Extends donations for FEC itemized records (pas2 + indiv bulk files),
-- adds candidate_totals (official FEC aggregates via the OpenFEC API), and
-- the finance rollup materialized views the app snapshot will read.

-- ---------------------------------------------------------------------------
-- donations: columns for real FEC itemized records.
--   * recipient_cmte_id becomes nullable: independent expenditures (24A/24E)
--     spend money ABOUT a candidate without giving TO any committee.
--   * fec_candidate_id is the target/benefiting candidate, set by loaders
--     (pas2 carries it directly; indiv resolves via the recipient committee).
--   * employer/occupation are stored now so donors can be industry-coded
--     retroactively once OpenSecrets access arrives.
--   * memo_cd = 'X' marks informational rows (e.g. conduit earmark detail)
--     that would double-count money; rollup views exclude them.
-- ---------------------------------------------------------------------------
ALTER TABLE donations
    ALTER COLUMN recipient_cmte_id DROP NOT NULL,
    ADD COLUMN fec_candidate_id TEXT,
    ADD COLUMN transaction_tp   TEXT,
    ADD COLUMN entity_tp        TEXT,
    ADD COLUMN transaction_pgi  TEXT,   -- primary/general indicator
    ADD COLUMN employer         TEXT,
    ADD COLUMN occupation       TEXT,
    ADD COLUMN donor_city       TEXT,
    ADD COLUMN donor_state      TEXT,
    ADD COLUMN donor_zip        TEXT,
    ADD COLUMN image_num        TEXT,   -- deep link to the scanned filing on fec.gov
    ADD COLUMN memo_cd          TEXT,
    ADD COLUMN memo_text        TEXT;

-- Every row must attach to a recipient committee or a target candidate.
ALTER TABLE donations ADD CONSTRAINT donations_attached
    CHECK (recipient_cmte_id IS NOT NULL OR fec_candidate_id IS NOT NULL);

-- Independent expenditures are meaningless without a target candidate.
ALTER TABLE donations ADD CONSTRAINT donations_ie_has_target
    CHECK (transaction_tp NOT IN ('24A', '24E') OR fec_candidate_id IS NOT NULL);

CREATE INDEX donations_recipient_idx ON donations (recipient_cmte_id);
CREATE INDEX donations_fec_candidate_idx ON donations (fec_candidate_id, cycle);

-- ---------------------------------------------------------------------------
-- candidate_totals: FEC's own per-candidate aggregates (OpenFEC /totals).
-- Mirrors the latest official numbers, refreshed weekly and updated in place
-- (unlike evaluations these are not our judgments, just FEC's aggregate;
-- provenance still tracked per refresh via source_id).
-- ---------------------------------------------------------------------------
CREATE TABLE candidate_totals (
    totals_id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fec_candidate_id      TEXT     NOT NULL,
    cycle                 SMALLINT NOT NULL,
    politician_id         BIGINT   NOT NULL REFERENCES politicians (politician_id),
    total_receipts        NUMERIC(14, 2),
    total_disbursements   NUMERIC(14, 2),
    cash_on_hand          NUMERIC(14, 2),
    debts_owed            NUMERIC(14, 2),
    individual_itemized   NUMERIC(14, 2),
    individual_unitemized NUMERIC(14, 2),
    pac_contributions     NUMERIC(14, 2),
    coverage_end          DATE,
    source_id             BIGINT   NOT NULL REFERENCES sources (source_id),
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (fec_candidate_id, cycle)
);

-- ---------------------------------------------------------------------------
-- mv_candidacy_finance: one row per candidacy with official totals plus
-- rollups of the itemized rows we hold. memo rows and IEs are excluded from
-- contribution sums; IEs get their own support/oppose columns.
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW mv_candidacy_finance AS
WITH direct AS (
    SELECT d.fec_candidate_id, d.cycle,
           SUM(d.amount) FILTER (WHERE d.contributor_cmte_id IS NOT NULL) AS pac_itemized,
           SUM(d.amount) FILTER (WHERE d.entity_tp = 'IND') AS individual_itemized_loaded,
           COUNT(*) AS itemized_rows
    FROM donations d
    WHERE COALESCE(d.memo_cd, '') <> 'X'
      AND COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E')
    GROUP BY d.fec_candidate_id, d.cycle
),
ie AS (
    SELECT d.fec_candidate_id, d.cycle,
           SUM(d.amount) FILTER (WHERE d.transaction_tp = '24E') AS ie_support,
           SUM(d.amount) FILTER (WHERE d.transaction_tp = '24A') AS ie_oppose
    FROM donations d
    WHERE d.transaction_tp IN ('24A', '24E')
      AND COALESCE(d.memo_cd, '') <> 'X'
    GROUP BY d.fec_candidate_id, d.cycle
)
SELECT c.candidacy_id, c.race_id, c.politician_id, c.fec_candidate_id, c.party,
       r.cycle, r.state, r.office, r.district, r.is_special,
       p.full_name, p.bioguide_id,
       t.total_receipts, t.total_disbursements, t.cash_on_hand, t.debts_owed,
       t.individual_itemized  AS individual_itemized_official,
       t.individual_unitemized,
       t.pac_contributions    AS pac_contributions_official,
       t.coverage_end,
       direct.pac_itemized,
       direct.individual_itemized_loaded,
       direct.itemized_rows,
       ie.ie_support,
       ie.ie_oppose
FROM candidacies c
JOIN races r        USING (race_id)
JOIN politicians p  USING (politician_id)
LEFT JOIN candidate_totals t
       ON t.fec_candidate_id = c.fec_candidate_id AND t.cycle = r.cycle
LEFT JOIN direct
       ON direct.fec_candidate_id = c.fec_candidate_id AND direct.cycle = r.cycle
LEFT JOIN ie
       ON ie.fec_candidate_id = c.fec_candidate_id AND ie.cycle = r.cycle;

-- Unique index enables REFRESH MATERIALIZED VIEW CONCURRENTLY later.
CREATE UNIQUE INDEX mv_candidacy_finance_key ON mv_candidacy_finance (candidacy_id);

-- ---------------------------------------------------------------------------
-- mv_top_committee_donors: top 15 contributing committees per candidacy
-- (direct money only: no IEs, no memo rows).
-- ---------------------------------------------------------------------------
CREATE MATERIALIZED VIEW mv_top_committee_donors AS
SELECT *
FROM (
    SELECT c.candidacy_id,
           d.contributor_cmte_id,
           cm.name      AS committee_name,
           cm.cmte_type,
           cm.party     AS committee_party,
           cm.connected_org,
           SUM(d.amount) AS total_amount,
           ROW_NUMBER() OVER (PARTITION BY c.candidacy_id
                              ORDER BY SUM(d.amount) DESC) AS donor_rank
    FROM donations d
    JOIN candidacies c ON c.fec_candidate_id = d.fec_candidate_id
    JOIN races r       ON r.race_id = c.race_id AND r.cycle = d.cycle
    JOIN committees cm ON cm.cmte_id = d.contributor_cmte_id
    WHERE d.contributor_cmte_id IS NOT NULL
      AND COALESCE(d.memo_cd, '') <> 'X'
      AND COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E')
    GROUP BY c.candidacy_id, d.contributor_cmte_id, cm.name, cm.cmte_type,
             cm.party, cm.connected_org
) ranked
WHERE donor_rank <= 15;

CREATE UNIQUE INDEX mv_top_committee_donors_key
    ON mv_top_committee_donors (candidacy_id, contributor_cmte_id);
