-- The money follows the person too, so a later join through politician_id
-- does not disagree with the one through fec_candidate_id.
UPDATE donations
SET politician_id = %(politician_id)s
WHERE fec_candidate_id = %(other_fec_id)s
  AND politician_id IS DISTINCT FROM %(politician_id)s
