-- 0026: stop requiring a declared website's committee to be in our snapshot.
--
-- The committee master is a weekly bulk file; the API that carries the
-- website is live. A committee registered since the last bulk load therefore
-- fails the foreign key, and the candidate's website is lost -- for exactly
-- the candidates whose sites we most want, since a brand new committee means
-- a newly declared campaign.
--
-- The alternative, inserting the committee from the API payload to satisfy
-- the key, is worse and was rejected. Committee rows drive money
-- attribution: state_committee_map keys on cmte_designation and cand_id, and
-- a partial row written by this pass could quietly move contributions onto or
-- off a candidate. That failure has already happened once in this project,
-- for $51.5M. A website is not worth risking it.
--
-- The id is still recorded exactly as filed; it is simply no longer required
-- to resolve locally.
ALTER TABLE candidate_websites
    DROP CONSTRAINT candidate_websites_cmte_id_fkey;

COMMENT ON COLUMN candidate_websites.cmte_id IS
    'The authorized committee that declared this address (designation P or A), '
    'as filed. Deliberately not a foreign key: the live API knows committees '
    'the weekly bulk snapshot does not.';
