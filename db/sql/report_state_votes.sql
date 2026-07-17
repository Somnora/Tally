-- Voting summary for a state's incumbent candidates (2026 races).
SELECT p.full_name, v.chamber,
       count(*)                                        AS votes_recorded,
       count(*) FILTER (WHERE v.position = 'yea')        AS yea,
       count(*) FILTER (WHERE v.position = 'nay')        AS nay,
       count(*) FILTER (WHERE v.position = 'present')    AS present,
       count(*) FILTER (WHERE v.position = 'not_voting') AS not_voting,
       max(v.voted_at)                                  AS latest_vote
FROM candidacies c
JOIN races r          USING (race_id)
JOIN politicians p    USING (politician_id)
JOIN voting_records v USING (politician_id)
WHERE r.state = %(state)s AND r.cycle = %(cycle)s
  AND c.incumbent_challenger = 'I'
GROUP BY p.full_name, v.chamber
ORDER BY p.full_name
