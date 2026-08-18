-- Finance ROLLUPS only. Itemized contributions stay server-side: they are the
-- bulk of the database, they carry donor names and addresses, and the app
-- deep-links to fec.gov for the raw records instead of republishing them.
--
-- The last column is a coverage disclosure, not a finance figure. The FEC's
-- own summary says how much this campaign raised in itemized individual
-- contributions; individual_itemized_loaded is how much of that we actually
-- hold row by row. The two are far apart, and the gap is the honest caveat
-- on the donor list beside them.
--
-- Individual money specifically, not all itemizable money. A combined ratio
-- looked tidier and was wrong: our committee sums include coordinated party
-- expenditures and in-kind transfers, which the FEC's PAC contribution total
-- does not count, and itemized filings routinely post-date the summary they
-- belong to. Both push a combined figure above 100 percent, which was
-- happening for 388 candidates. A coverage number that reports holding more
-- than exists discredits the very disclosure it is making. Individual money
-- has neither problem, and it is the larger share of what campaigns raise
-- and the channel organized bundling flows through, so it is also the gap
-- that actually matters.
--
-- Unitemized small-dollar giving is excluded from both sides, because no
-- itemized record of it exists at the FEC either; counting it against us
-- would describe a gap nobody could close.
SELECT candidacy_id, politician_id, total_receipts, total_disbursements,
       cash_on_hand, individual_itemized_official, individual_unitemized,
       pac_contributions_official, ie_support, ie_oppose, coverage_end,
       GREATEST(COALESCE(individual_itemized_loaded, 0), 0)
           AS individual_itemized_loaded
FROM mv_candidacy_finance
WHERE cycle = %(cycle)s
ORDER BY candidacy_id
