SELECT c.candidacy_id, c.politician_id, c.fec_candidate_id, c.principal_cmte_id,
       p.full_name, r.office, r.district, r.is_special
FROM candidacies c
JOIN races r       USING (race_id)
JOIN politicians p USING (politician_id)
-- 'ALL' runs every state in one pass, which the itemized loader uses so a
-- national load streams the bulk files once instead of fifty times.
WHERE (%(state)s = 'ALL' OR r.state = %(state)s)
  AND r.cycle = %(cycle)s
ORDER BY r.office, r.district NULLS FIRST, p.full_name
