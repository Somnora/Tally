-- Each sitting member's most recent substantive votes, with the receipt.
--
-- Substantive only: passage and suspension votes, never recommittal or the
-- previous question. A member's last three procedural votes say nothing a
-- reader can use, and presenting them as "recent votes" would imply they do.
--
-- Three per member is a deliberate ceiling. This is a prompt to go look at
-- the full record on the Clerk's site, not a replacement for it, and 451
-- members times a longer list is real weight in a file the reader downloads.
SELECT politician_id, bill_number, vote_question, position, voted_at,
       congress_gov_url, bill_title, policy_area
FROM (
    SELECT v.politician_id, v.bill_number, v.vote_question, v.position,
           v.voted_at, v.congress_gov_url, b.title AS bill_title, b.policy_area,
           row_number() OVER (
               PARTITION BY v.politician_id ORDER BY v.voted_at DESC, v.roll_call_number DESC
           ) AS rank
    FROM voting_records v
    LEFT JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
    WHERE v.position IN ('yea', 'nay')
      AND v.vote_question IN (
          'On Passage',
          'On Motion to Suspend the Rules and Pass',
          'On Motion to Suspend the Rules and Pass, as Amended',
          'On Agreeing to the Resolution'
      )
      AND b.title IS NOT NULL
      AND v.politician_id IN (
          SELECT politician_id FROM candidacies c JOIN races r USING (race_id)
          WHERE r.cycle = %(cycle)s
      )
    GROUP BY v.politician_id, v.bill_number, v.vote_question, v.position,
             v.voted_at, v.congress_gov_url, b.title, b.policy_area, v.roll_call_number
) ranked
WHERE rank <= 3
ORDER BY politician_id, voted_at DESC
