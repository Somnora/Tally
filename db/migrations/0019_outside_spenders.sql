-- 0019: name the outside spenders.
--
-- mv_candidacy_finance already totals independent expenditures into two
-- scalars, ie_support and ie_oppose, and the app printed those totals with
-- nobody's name attached. That was the largest disclosure gap on the site:
-- direct committee contributions are capped by law at $10,000 per candidate,
-- so the "largest committee donors" list is a row of identical $10,000
-- entries, while independent expenditure is uncapped and routinely an order
-- of magnitude larger. For 408 of the 636 candidates carrying outside money,
-- outside spending exceeds their single largest direct donor. Naming the
-- capped money and withholding the uncapped money reads as evasion, and the
-- committee id was sitting in the row the whole time.
--
-- Ranked WITHIN stance, not across it. Ranking a candidate's spenders on
-- amount alone would let ten supporters bury the one committee spending
-- against them, which is exactly the fact a reader most wants.
--
-- Same accounting discipline as the other finance views: memo rows are
-- excluded so conduit detail is not double counted, and the cycle join runs
-- through races so a spender cannot be attached to the wrong election.

CREATE MATERIALIZED VIEW mv_top_outside_spenders AS
SELECT *
FROM (
    SELECT c.candidacy_id,
           d.contributor_cmte_id AS spender_cmte_id,
           cm.name               AS spender_name,
           cm.cmte_type,
           cm.connected_org,
           CASE WHEN d.transaction_tp = '24E' THEN 'supporting' ELSE 'opposing' END
               AS stance,
           SUM(d.amount) AS total_amount,
           ROW_NUMBER() OVER (
               PARTITION BY c.candidacy_id,
                            CASE WHEN d.transaction_tp = '24E'
                                 THEN 'supporting' ELSE 'opposing' END
               ORDER BY SUM(d.amount) DESC
           ) AS spender_rank
    FROM donations d
    JOIN candidacies c ON c.fec_candidate_id = d.fec_candidate_id
    JOIN races r       ON r.race_id = c.race_id AND r.cycle = d.cycle
    JOIN committees cm ON cm.cmte_id = d.contributor_cmte_id
    WHERE d.transaction_tp IN ('24A', '24E')
      AND COALESCE(d.memo_cd, '') <> 'X'
    GROUP BY c.candidacy_id, d.contributor_cmte_id, cm.name, cm.cmte_type,
             cm.connected_org,
             CASE WHEN d.transaction_tp = '24E' THEN 'supporting' ELSE 'opposing' END
) ranked
WHERE spender_rank <= 10;

CREATE UNIQUE INDEX mv_top_outside_spenders_key
    ON mv_top_outside_spenders (candidacy_id, spender_cmte_id, stance);
