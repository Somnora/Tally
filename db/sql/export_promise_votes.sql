-- The votes a reader should look at beside a promise, carrying no verdict.
--
-- This is the evidence-over-verdicts principle made literal. We know how to
-- find the roll calls related to a promise's subject; what we do not yet have
-- is a model that reliably reads what a vote MEANT for that promise. Rather
-- than publish a judgment we cannot stand behind, we publish the same short
-- vote list the evaluation stage would have been shown, deep-linked to
-- congress.gov, and let the reader draw the conclusion.
--
-- Everything a reader needs in order to distrust a naive reading is carried
-- with each row rather than left implicit:
--
--   summary          what the bill DOES. A congressional title is written to
--                    persuade and routinely names the opposite of its effect
--                    ("Homeowner Energy Freedom Act" repeals home energy
--                    efficiency rebates). Showing a title without its summary
--                    is how our own evaluation stage got verdicts backwards.
--   has_summary      false where Congress.gov published none, so the app can
--                    mark the row rather than let a bare title read as fact.
--   is_procedural    a vote on process, not policy.
--   is_omnibus       a bill bundling many unrelated provisions, so a position
--                    on it says little about any one of them.
--
-- One vote per bill, ranked so the substantive roll call wins over the
-- procedural one, matching select_votes_for_promise: a member genuinely votes
-- differently on the motion to recommit and on passage, and showing both
-- reads as self-contradiction on a single bill.
--
-- Only yea and nay. Present and not-voting carry no direction and would be
-- noise. A promise whose topic has no filter row simply contributes nothing
-- here, which is the honest outcome and is surfaced in the app as coverage we
-- do not have rather than as a member with nothing to show.
WITH promise AS (
    SELECT p.promise_id, p.politician_id, p.topic
    FROM app_export_promises p
),
filt AS (
    SELECT t.topic,
           coalesce(canon.policy_areas, t.policy_areas) AS policy_areas,
           coalesce(canon.subjects,     t.subjects)     AS subjects
    FROM topic_vote_filters t
    LEFT JOIN topic_vote_filters canon ON canon.topic = t.canonical_topic
),
matched AS (
    SELECT pr.promise_id,
           v.vote_id, v.congress, v.bill_key, v.position, v.vote_question,
           v.voted_at, v.roll_call_number, v.congress_gov_url,
           b.title, b.summary_text,
           coalesce(array_length(b.subjects, 1), 0) AS subject_count,
           CASE v.vote_question
               WHEN 'On Passage'                                   THEN 1
               WHEN 'On Motion to Suspend the Rules and Pass'       THEN 2
               WHEN 'On Motion to Suspend the Rules and Pass, as Amended' THEN 3
               WHEN 'On Agreeing to the Resolution'                 THEN 4
               WHEN 'On Concurring in the Senate Amendment'         THEN 5
               ELSE 9
           END AS question_rank
    FROM promise pr
    JOIN filt f ON f.topic = pr.topic
    JOIN voting_records v ON v.politician_id = pr.politician_id
    JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
    WHERE v.position IN ('yea', 'nay')
      AND (b.policy_area = ANY (f.policy_areas) OR b.subjects && f.subjects)
),
one_per_bill AS (
    SELECT DISTINCT ON (promise_id, congress, bill_key) *
    FROM matched
    ORDER BY promise_id, congress, bill_key, question_rank, roll_call_number DESC
),
ranked AS (
    SELECT *,
           row_number() OVER (
               PARTITION BY promise_id
               -- Clean, recent, substantive first, so the cap keeps the votes
               -- a reader is best served by seeing.
               ORDER BY (subject_count > 50), (question_rank > 5), voted_at DESC
           ) AS rn
    FROM one_per_bill
)
SELECT promise_id, vote_id, bill_key, title,
       left(summary_text, %(summary_chars)s) AS summary,
       (summary_text IS NOT NULL AND length(summary_text) > 20) AS has_summary,
       position, vote_question, voted_at, congress_gov_url,
       (question_rank > 5) AS is_procedural,
       (subject_count > 50) AS is_omnibus
FROM ranked
WHERE rn <= %(votes_per_promise)s
ORDER BY promise_id, voted_at DESC
