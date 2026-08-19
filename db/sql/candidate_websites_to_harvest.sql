-- Declared sites not yet visited, or revisited when the caller asks.
--
-- outcome filters to sites that previously recorded one particular result,
-- which is how a transient class of failure gets retried without re-crawling
-- every campaign in the country: sites recorded 'unreachable' before robots
-- was checked separately, for instance, need re-asking and nothing else does.
SELECT w.candidate_website_id, w.politician_id, w.url, p.full_name
  FROM candidate_websites w
  JOIN politicians p ON p.politician_id = w.politician_id
 WHERE w.cycle = %(cycle)s
   AND (%(outcome)s::text IS NULL OR w.fetch_outcome = %(outcome)s)
   AND (%(recheck)s OR %(outcome)s::text IS NOT NULL OR w.last_checked_at IS NULL)
 ORDER BY w.candidate_website_id
