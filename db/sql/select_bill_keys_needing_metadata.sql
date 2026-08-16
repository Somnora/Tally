-- Distinct bills that someone actually voted on but which have no metadata
-- row yet. Driving the backfill off votes (rather than off every bill in
-- the congress) keeps it small: members share roll calls, so the whole
-- pilot needs a few hundred bills, not tens of thousands.
--
-- %(politician_id)s NULL means "every bill anyone voted on"; passing an id
-- restricts the fetch to one member's record, which is how the pilot runs.
SELECT DISTINCT v.congress, v.bill_key
FROM voting_records v
LEFT JOIN bills b
       ON b.congress = v.congress AND b.bill_key = v.bill_key
WHERE v.bill_key IS NOT NULL
  AND b.bill_id IS NULL
  AND (%(politician_id)s::BIGINT IS NULL OR v.politician_id = %(politician_id)s)
ORDER BY v.congress, v.bill_key
