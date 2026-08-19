-- Declared sites not yet visited, or revisited when the caller asks.
-- Candidates who already have campaign_site documents are skipped by default
-- so a rerun resumes rather than re-crawling every campaign in the country.
SELECT w.candidate_website_id, w.politician_id, w.url, p.full_name
  FROM candidate_websites w
  JOIN politicians p ON p.politician_id = w.politician_id
 WHERE w.cycle = %(cycle)s
   AND (%(recheck)s OR w.last_checked_at IS NULL)
 ORDER BY w.candidate_website_id
