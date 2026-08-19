-- Documents we hold for a candidate, and how many the extractor has not read
-- yet. This is what lets the map distinguish "we have not looked at this yet"
-- from "we have nothing", which are different statements to make about a
-- person standing for office.
--
-- Counts only, never text. Nothing here is a promise and nothing here is
-- displayed as one; a document becomes quotable only after extraction and
-- both verification gates, and this table exists precisely to say when that
-- has not happened.
SELECT c.politician_id,
       count(*)::int AS documents,
       count(*) FILTER (WHERE d.extracted_at IS NULL)::int AS pending
  FROM app_export_candidacies c
  JOIN documents d ON d.politician_id = c.politician_id
 WHERE c.cycle = %(cycle)s
 GROUP BY c.politician_id
 ORDER BY c.politician_id
