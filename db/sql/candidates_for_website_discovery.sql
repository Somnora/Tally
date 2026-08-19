-- Every candidate the snapshot publishes, so discovery covers exactly the
-- people a reader can look up and nobody less.
--
-- Deliberately NOT filtered to cand_status = 'C'. That flag marks a filer who
-- has crossed the $5,000 threshold, and 1,733 of this cycle's 4,079 published
-- candidates have not; excluding them would rebuild, one level down, the same
-- asymmetry this whole pass exists to remove — a page that shows a candidate
-- and then reports nothing they have said.
--
-- Incumbents are included. Their house.gov site is government speech under
-- rules restricting campaign content; their campaign site is where they make
-- campaign promises. The two are different claims about a person.
SELECT c.fec_candidate_id, c.politician_id, p.full_name
  FROM candidacies c
  JOIN races r USING (race_id)
  JOIN politicians p ON p.politician_id = c.politician_id
 WHERE r.cycle = %(cycle)s
   AND (%(rediscover)s OR NOT EXISTS (
         SELECT 1 FROM candidate_website_scans s
          WHERE s.fec_candidate_id = c.fec_candidate_id AND s.cycle = %(cycle)s))
 ORDER BY c.fec_candidate_id
