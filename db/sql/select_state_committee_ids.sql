-- All committees attached to a state's candidates for a cycle: authorized
-- committees (committees.cand_id) plus principal campaign committees.
SELECT DISTINCT cm.cmte_id
FROM committees cm
JOIN candidacies c ON cm.cand_id = c.fec_candidate_id
JOIN races r       USING (race_id)
WHERE r.state = %(state)s AND r.cycle = %(cycle)s
UNION
SELECT c.principal_cmte_id
FROM candidacies c
JOIN races r USING (race_id)
WHERE r.state = %(state)s AND r.cycle = %(cycle)s
  AND c.principal_cmte_id IS NOT NULL
