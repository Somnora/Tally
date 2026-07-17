SELECT c.politician_id
FROM candidacies c
JOIN races r USING (race_id)
WHERE c.fec_candidate_id = %(fec_candidate_id)s AND r.cycle = %(cycle)s
