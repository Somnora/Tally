-- Organisations that bundled contributions to each candidate, aggregated.
-- conduit_cmte_id travels so the app can deep-link each one to its FEC page.
-- This is money given by individuals, so it is NOT independent expenditure
-- and NOT a committee contribution; it is the third channel, and the one
-- organised giving mostly uses.
SELECT candidacy_id, conduit_cmte_id, conduit_name, cmte_type, connected_org,
       contribution_count, total_amount, conduit_rank
FROM mv_top_conduits
-- Positive net only. Receipt lines can carry negative amounts when a
-- contribution is reattributed or corrected, and one conduit nets below zero:
-- WinRed against Tommy Tuberville, 352 contributions totalling minus $1,606.
-- The arithmetic is right and the sentence it produces is not, because
-- "bundled -$1,606" states nothing a reader can use. On net that organisation
-- did not route money to that candidate, so it is not listed as having done
-- so. The rollup keeps the full figure server-side either way.
WHERE total_amount > 0
ORDER BY candidacy_id, conduit_rank
