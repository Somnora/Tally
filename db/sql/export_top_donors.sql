-- Top committee donors per candidacy, already aggregated. contributor_cmte_id
-- travels so the app can deep-link each one to its FEC page.
SELECT candidacy_id, contributor_cmte_id, committee_name, cmte_type,
       connected_org, total_amount, donor_rank
FROM mv_top_committee_donors
ORDER BY candidacy_id, donor_rank
