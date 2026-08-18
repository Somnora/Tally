-- Point the live candidacy at the person who actually holds the record.
-- Safe against the (race_id, politician_id) unique constraint because the two
-- candidacies are for different seats, and therefore different races.
UPDATE candidacies c
SET politician_id = %(politician_id)s
FROM races r
WHERE r.race_id = c.race_id
  AND c.fec_candidate_id = %(other_fec_id)s
  AND r.cycle = %(cycle)s
  AND c.politician_id <> %(politician_id)s
