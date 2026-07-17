-- 0005: votes pipeline schema (Milestone 3).
--
--   * position gains 'guilty' / 'not_guilty' — impeachment votes are real
--     roll calls and mapping them onto yea/nay would misstate the record.
--   * vote_result: the chamber's outcome ("Passed", "Rejected", "Nomination
--     Confirmed"), so the app can show "voted Nay; passed 51-49" context.
--   * id_crosswalk.lis_id: senate.gov roll-call XML identifies senators by
--     LIS id (e.g. S428), not bioguide; the legislators file provides the
--     mapping and the seeder now stores it.

ALTER TABLE voting_records DROP CONSTRAINT voting_records_position_check;
ALTER TABLE voting_records ADD CONSTRAINT voting_records_position_check
    CHECK (position IN ('yea', 'nay', 'present', 'not_voting', 'guilty', 'not_guilty'));

ALTER TABLE voting_records ADD COLUMN vote_result TEXT;

-- Incremental sync asks "what is the newest roll call stored per chamber?"
CREATE INDEX voting_records_chamber_roll_idx
    ON voting_records (chamber, congress, session, roll_call_number);

ALTER TABLE id_crosswalk ADD COLUMN lis_id TEXT;
