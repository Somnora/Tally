-- 0021: record the conduit that transmitted an earmarked contribution.
--
-- The individual-contributions bulk file carries OTHER_ID, which on an
-- earmarked receipt names the committee that collected the money and passed
-- it on. The loader was discarding it, so we could see that a contribution
-- was bundled (the memo says "EARMARKED CONTRIBUTION: SEE BELOW") without
-- being able to say who bundled it. For Haley Stevens that is 9,463 of
-- 11,573 individual contributions: the mechanism organised giving actually
-- uses, invisible in the one place a reader would look for it.
--
-- Its own column, deliberately, NOT contributor_cmte_id. The contributor is
-- the individual who gave the money; the conduit only transmitted it. Writing
-- the conduit into contributor_cmte_id would have swept every conduit into
-- mv_top_committee_donors as though it had donated, inventing million dollar
-- committee donors out of money those committees never gave and double
-- counting it against the individuals who did. Separate roles, separate
-- columns.
ALTER TABLE donations
    ADD COLUMN conduit_cmte_id text REFERENCES committees (cmte_id);

CREATE INDEX donations_conduit_idx
    ON donations (conduit_cmte_id) WHERE conduit_cmte_id IS NOT NULL;

COMMENT ON COLUMN donations.conduit_cmte_id IS
    'Committee that transmitted an earmarked contribution (FEC OTHER_ID). '
    'The contributor is still contributor_name / contributor_cmte_id; this '
    'is who routed it.';
