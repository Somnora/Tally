-- 0022: rank the organisations that bundle contributions to each candidate.
--
-- An earmarked contribution has two parties worth naming. The individual
-- gave the money and is capped like any individual. The conduit collected it
-- alongside many others and delivered them together, which is how an
-- organisation directs far more than any single contribution limit would
-- allow while every underlying gift stays legal. Reporting only the
-- individuals describes the letter of the transaction and misses the thing a
-- reader is actually asking about.
--
-- Counted from receipts only: memo lines are the same dollars restated, and
-- summing both would double every bundled figure on the site.
--
-- Ranked across the candidate's conduits by amount, with the contribution
-- count carried too, because "one organisation routed $400,000 across 900
-- contributions" and "across 3" describe different things.

CREATE MATERIALIZED VIEW mv_top_conduits AS
SELECT *
FROM (
    SELECT c.candidacy_id,
           d.conduit_cmte_id,
           cm.name          AS conduit_name,
           cm.cmte_type,
           cm.connected_org,
           COUNT(*)         AS contribution_count,
           SUM(d.amount)    AS total_amount,
           ROW_NUMBER() OVER (PARTITION BY c.candidacy_id
                              ORDER BY SUM(d.amount) DESC) AS conduit_rank
    FROM donations d
    JOIN candidacies c ON c.fec_candidate_id = d.fec_candidate_id
    JOIN races r       ON r.race_id = c.race_id AND r.cycle = d.cycle
    JOIN committees cm ON cm.cmte_id = d.conduit_cmte_id
    WHERE d.conduit_cmte_id IS NOT NULL
      AND COALESCE(d.memo_cd, '') <> 'X'
      AND d.entity_tp = 'IND'
      AND d.transaction_tp IN ('10', '11', '15', '15C', '15E')
    GROUP BY c.candidacy_id, d.conduit_cmte_id, cm.name, cm.cmte_type,
             cm.connected_org
) ranked
WHERE conduit_rank <= 10;

CREATE UNIQUE INDEX mv_top_conduits_key
    ON mv_top_conduits (candidacy_id, conduit_cmte_id);
