-- 0020: index the foreign key that points AT donations.
--
-- evaluation_evidence.donation_id references donations(donation_id) with no
-- index on the referencing column. Postgres does not create one
-- automatically, and every DELETE from donations must then prove no evidence
-- row cites the departing id, which without an index means a sequential scan
-- of evaluation_evidence per deleted row.
--
-- Found while removing 876,497 misattributed contribution rows: the delete
-- ran twelve minutes without finishing, because it was performing roughly
-- five and a half billion row comparisons. donations is now a five million
-- row table that bulk loads will correct in place, so an unindexed inbound
-- foreign key is no longer a theoretical cost.
CREATE INDEX IF NOT EXISTS evaluation_evidence_donation_idx
    ON evaluation_evidence (donation_id);
