-- 0004: transaction-type discipline in the finance rollups.
--
-- Found during the Maine pilot: the indiv bulk file contains contribution
-- REFUNDS (transaction type 22Y) as positive amounts. Summing every row
-- overstated Jared Golden's individual receipts by $685k. The views now:
--   * count only true receipt types toward contribution sums
--     (10, 11, 15, 15C, 15E for individuals);
--   * exclude refund types (22Y to individuals, 22Z to committees) from
--     PAC sums and the top-donors list;
--   * expose individual_refunds as its own column — visible, not hidden.
-- Materialized views cannot be altered in place, so: drop and recreate.

DROP MATERIALIZED VIEW mv_top_committee_donors;
DROP MATERIALIZED VIEW mv_candidacy_finance;

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
           ) AS individual_itemized_loaded,
           SUM(d.amount) FILTER (WHERE d.transaction_tp = '22Y') AS individual_refunds,
           COUNT(*) FILTER (
               WHERE COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E')
           ) AS itemized_rows
    FROM donations d
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
      AND COALESCE(d.transaction_tp, '') NOT IN ('24A', '24E', '22Y', '22Z')
    GROUP BY c.candidacy_id, d.contributor_cmte_id, cm.name, cm.cmte_type,
             cm.party, cm.connected_org
) ranked
WHERE donor_rank <= 15;

CREATE UNIQUE INDEX mv_top_committee_donors_key
    ON mv_top_committee_donors (candidacy_id, contributor_cmte_id);
