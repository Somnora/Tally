-- 0028: stop counting a candidate's own money as individual contributions.
--
-- 0027 made the coverage ratio compare one period with itself and 16
-- candidates still showed more itemized individual money than they reported.
-- The window was never their problem. Christopher Swann's $1,100 official
-- figure is exactly the sum of his type 15 rows; the other $9,600 is three
-- type 15C rows from SWANN, CHRISTOPHER A. 15C is a contribution from the
-- candidate to their own campaign, which the FEC reports on its own line and
-- deliberately not under individual contributions.
--
-- Nationally that is $27,470,352 across 8,083 rows and 860 candidates, and
-- the error it produced is worse than an arithmetic one. This project exists
-- to answer who is backing a candidate. Counting the candidate's own money as
-- individual contributions makes a self-funded campaign look like it has
-- support from other people, which is the single most misleading thing this
-- column could say.
--
-- Exposed, not merely removed, following individual_refunds in 0004: a reader
-- is better served seeing that a candidate put in their own money than seeing
-- it disappear. mv_top_conduits also lists 15C among its transaction types;
-- verified zero rows there, since a self contribution has no conduit, so it
-- is left alone rather than rebuilt for no behavioural change.
DROP MATERIALIZED VIEW mv_candidacy_finance CASCADE;

CREATE MATERIALIZED VIEW mv_candidacy_finance AS
WITH direct AS (
    SELECT d.fec_candidate_id, d.cycle,
           SUM(d.amount) FILTER (
               WHERE d.contributor_cmte_id IS NOT NULL
                 AND COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E', '22Y', '22Z')
           ) AS pac_itemized,
           -- 15C removed: see above. The date bound is 0027's fix, kept.
           SUM(d.amount) FILTER (
               WHERE d.entity_tp = 'IND'
                 AND d.transaction_tp IN ('10', '11', '15', '15E')
                 AND (ct.coverage_end IS NULL
                      OR (d.contributed_at IS NOT NULL
                          AND d.contributed_at <= ct.coverage_end))
           ) AS individual_itemized_loaded,
           SUM(d.amount) FILTER (WHERE d.transaction_tp = '15C')
               AS candidate_self_funding,
           SUM(d.amount) FILTER (WHERE d.transaction_tp = '22Y') AS individual_refunds,
           COUNT(*) FILTER (
               WHERE COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E')
           ) AS itemized_rows
    FROM donations d
    LEFT JOIN candidate_totals ct
           ON ct.fec_candidate_id = d.fec_candidate_id AND ct.cycle = d.cycle
    WHERE COALESCE(d.memo_cd, '') <> 'X'
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
       direct.candidate_self_funding,
       direct.individual_refunds,
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

CREATE UNIQUE INDEX mv_candidacy_finance_key ON mv_candidacy_finance (candidacy_id);

COMMENT ON COLUMN mv_candidacy_finance.candidate_self_funding IS
    'FEC type 15C: money the candidate gave their own campaign. Not other '
    'people''s support, and excluded from individual_itemized_loaded.';
