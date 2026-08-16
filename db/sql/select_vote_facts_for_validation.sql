-- The facts needed to check a cited vote, fetched from the database rather
-- than trusted from the model's output.
--
-- Validation asks three questions and this query answers all of them:
--   does the vote exist at all (no row means the id was invented);
--   does it belong to the politician whose promise is being evaluated
--   (citing another member's vote is a real failure mode, and one the FK
--   alone cannot catch since every vote_id is a valid vote_id);
--   and is it substantive, or procedural or omnibus, which caps how strongly
--   it may be cited.
SELECT v.vote_id, v.politician_id, v.position, v.vote_question, v.bill_key,
       coalesce(array_length(b.subjects, 1), 0) > 50 AS is_omnibus,
       v.vote_question NOT IN (
           'On Passage',
           'On Motion to Suspend the Rules and Pass',
           'On Motion to Suspend the Rules and Pass, as Amended',
           'On Agreeing to the Resolution',
           'On Concurring in the Senate Amendment'
       ) AS is_procedural
FROM voting_records v
LEFT JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
WHERE v.vote_id = ANY (%(vote_ids)s)
