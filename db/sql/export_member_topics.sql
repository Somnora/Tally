-- The policy areas a member has actually voted on, most frequent first.
-- Derived from the bills behind their roll calls rather than from any
-- self-description, so it reflects the floor they served on.
-- Capped at five per member: enough to characterise a record, small enough
-- that 451 members do not bloat the snapshot.
SELECT politician_id, policy_area, votes
FROM (
    SELECT v.politician_id, b.policy_area, count(*) AS votes,
           row_number() OVER (PARTITION BY v.politician_id ORDER BY count(*) DESC) AS rank
    FROM voting_records v
    JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
    WHERE b.policy_area IS NOT NULL
      AND v.position IN ('yea', 'nay')
      AND v.politician_id IN (
          SELECT politician_id FROM candidacies c JOIN races r USING (race_id)
          WHERE r.cycle = %(cycle)s
      )
    GROUP BY v.politician_id, b.policy_area
) ranked
WHERE rank <= 5
ORDER BY politician_id, votes DESC
