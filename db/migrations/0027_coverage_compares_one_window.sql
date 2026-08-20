-- 0027: compare the money we hold against the money reported for the SAME
-- period.
--
-- The coverage bar exists to admit how much of a candidate's itemized
-- individual money we actually hold. For 20 candidates in the published
-- snapshot it claimed more than all of it: Christopher Swann's card offered
-- itemized records for $10,700 of the $1,100 his campaign reports, and the
-- bar read 100% because the app clamps it, which makes a wrong number look
-- like a deliberate one.
--
-- Neither figure was wrong. They described different windows. The official
-- total comes from the FEC's own summary and stops at coverage_end, a date
-- that differs per candidate and ran anywhere from March to July in this
-- snapshot; the loaded figure summed every itemized receipt in the bulk file,
-- which runs to whenever we last downloaded it. Dividing one by the other
-- compares eleven months of receipts against four months of reporting.
--
-- So the numerator is now bounded by the same date as the denominator. A
-- contribution with no usable date is excluded rather than assumed to fall
-- inside the window: we cannot place it, and this is the one number on the
-- card whose whole job is to understate rather than overstate what we have.
-- Where a candidate has no official totals at all there is nothing to compare
-- against and the app shows no bar, so the sum stays unbounded there.
DROP MATERIALIZED VIEW mv_candidacy_finance CASCADE;

CREATE MATERIALIZED VIEW mv_candidacy_finance AS
WITH direct AS (
    SELECT d.fec_candidate_id, d.cycle,
           SUM(d.amount) FILTER (
               WHERE d.contributor_cmte_id IS NOT NULL
                 AND COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E', '22Y', '22Z')
           ) AS pac_itemized,
           SUM(d.amount) FILTER (
               WHERE d.entity_tp = 'IND'
                 AND d.transaction_tp IN ('10', '11', '15', '15C', '15E')
                 AND (ct.coverage_end IS NULL
                      OR (d.contributed_at IS NOT NULL
                          AND d.contributed_at <= ct.coverage_end))
           ) AS individual_itemized_loaded,
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

COMMENT ON MATERIALIZED VIEW mv_candidacy_finance IS
    'Per-candidacy finance rollup. individual_itemized_loaded is bounded by '
    'the official totals coverage_end so the coverage ratio compares one '
    'period with itself.';
