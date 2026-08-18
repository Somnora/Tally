-- Who spent independently for or against each candidate, already aggregated.
-- spender_cmte_id travels so the app can deep-link each committee to its FEC
-- page, the same way top donors do. This is the uncapped money: direct
-- contributions are limited by law, independent expenditure is not, so for
-- most candidates carrying any of it this list matters more than the donor
-- list beside it.
SELECT candidacy_id, spender_cmte_id, spender_name, cmte_type,
       connected_org, stance, total_amount, spender_rank
FROM mv_top_outside_spenders
ORDER BY candidacy_id, stance, spender_rank
