-- Voting-record summary for every sitting member who is a candidate this
-- cycle. This is the answer to a card that would otherwise read "no promises
-- researched yet": an incumbent's whole roll-call history is already in the
-- database, and saying nothing about it while showing their fundraising
-- would be the least informative choice available.
--
-- Yea/nay counts are reported without interpretation. A high yea rate is not
-- a virtue or a vice, it is a fact about which bills reached the floor, and
-- the app presents it as context beside the votes themselves.
SELECT v.politician_id,
       count(*)                                   AS roll_calls,
       count(*) FILTER (WHERE v.position = 'yea') AS yea,
       count(*) FILTER (WHERE v.position = 'nay') AS nay,
       min(v.voted_at)                            AS first_vote,
       max(v.voted_at)                            AS last_vote
FROM voting_records v
WHERE v.politician_id IN (
    SELECT politician_id FROM candidacies c JOIN races r USING (race_id)
    WHERE r.cycle = %(cycle)s
)
GROUP BY v.politician_id
