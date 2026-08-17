-- Topically relevant roll-call votes for one promise, pre-digested for the
-- evaluation prompt. Every row carries its vote_id so the model can only
-- cite records that exist.
--
-- Three things this query exists to get right, all of them learned from the
-- real data rather than assumed:
--
--  1. ONE VOTE PER BILL. A bill draws several roll calls and a member
--     genuinely votes differently on them: on HR 1181 Pingree voted yea on
--     the Motion to Recommit and nay On Passage. Handing both to the model
--     looks like self-contradiction on a single bill. So each bill
--     contributes its most substantive vote, ranked by question, and the
--     question travels with it so nothing is hidden.
--
--  2. PROCEDURAL VOTES ARE MARKED. A vote on recommittal or the previous
--     question is a position on process, not on the policy. It is offered as
--     context, never as clean support, and is_procedural says so.
--
--  3. OMNIBUS BILLS ARE MARKED. HR 8595 carries 153 subject terms, so it
--     matches almost any topic and means almost nothing about any of them.
--     The corpus makes the cutoff easy: the median bill has 5 subjects and
--     only 20 of 461 exceed 50.
--
-- Only yea and nay are returned. Present and not-voting carry no direction,
-- so they cannot support or contradict anything and would only be noise in
-- an evidence list.
--
-- An unknown topic matches the empty filter row and returns nothing, which
-- the stage turns into 'unverifiable'. That is the honest answer.
--
-- The LEFT JOIN resolves an alias to the filter it points at, so the dozen
-- ways a run can say "climate" all reach the environment filter without any
-- of them holding a copy of it that can go stale.
WITH filt AS (
    SELECT coalesce(canon.policy_areas, t.policy_areas) AS policy_areas,
           coalesce(canon.subjects,     t.subjects)     AS subjects
    FROM topic_vote_filters t
    LEFT JOIN topic_vote_filters canon ON canon.topic = t.canonical_topic
    WHERE t.topic = %(topic)s
),
matched AS (
    SELECT v.vote_id, v.congress, v.bill_key, v.position, v.vote_question,
           v.voted_at, v.roll_call_number, v.congress_gov_url,
           b.title, b.policy_area, b.summary_text,
           coalesce(array_length(b.subjects, 1), 0) AS subject_count,
           CASE v.vote_question
               WHEN 'On Passage'                                   THEN 1
               WHEN 'On Motion to Suspend the Rules and Pass'       THEN 2
               WHEN 'On Motion to Suspend the Rules and Pass, as Amended' THEN 3
               WHEN 'On Agreeing to the Resolution'                 THEN 4
               WHEN 'On Concurring in the Senate Amendment'         THEN 5
               ELSE 9   -- recommittal, previous question, amendments
           END AS question_rank
    FROM voting_records v
    JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
    CROSS JOIN filt f
    WHERE v.politician_id = %(politician_id)s
      AND v.position IN ('yea', 'nay')
      AND (b.policy_area = ANY (f.policy_areas) OR b.subjects && f.subjects)
),
one_per_bill AS (
    SELECT DISTINCT ON (congress, bill_key) *
    FROM matched
    ORDER BY congress, bill_key, question_rank, roll_call_number DESC
)
SELECT vote_id, bill_key, title, policy_area, summary_text, position,
       vote_question, voted_at, congress_gov_url,
       question_rank > 5   AS is_procedural,
       subject_count > 50  AS is_omnibus
FROM one_per_bill
-- Clean, recent, substantive votes first, so the cap keeps the best evidence.
ORDER BY is_omnibus, is_procedural, voted_at DESC
LIMIT %(limit)s
