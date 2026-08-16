-- Finance ROLLUPS only. Itemized contributions stay server-side: they are the
-- bulk of the database, they carry donor names and addresses, and the app
-- deep-links to fec.gov for the raw records instead of republishing them.
SELECT candidacy_id, politician_id, total_receipts, total_disbursements,
       cash_on_hand, individual_itemized_official, individual_unitemized,
       pac_contributions_official, ie_support, ie_oppose, coverage_end
FROM mv_candidacy_finance
WHERE cycle = %(cycle)s
ORDER BY candidacy_id
