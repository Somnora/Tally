-- Promises eligible for evaluation, for one member.
--
-- Four gates, in order of how much they matter:
--
--   quote_verified   invariant 1. An unverified quote is not displayable, so
--                    it is certainly not scoreable.
--   is_scoreable     rhetorical pledges ("always put Maine families first")
--                    are stored and shown for context but never scored.
--   review verdict   a promise a human or triage agent already called an
--                    opinion, a fragment, or not a promise never reaches the
--                    model. Extraction precision is about three in five, so
--                    without this gate two of every five evaluations would
--                    scoring something that was never a promise. Unreviewed
--                    promises DO pass, so this scales past the pilot.
--   has a record     evaluation compares a promise to a voting record.
--                    Challengers have none, so there is nothing to compare
--                    and the stage skips them rather than emitting a wall of
--                    'unverifiable'.
--
-- Re-running is cheap: a promise already carrying a current evaluation from
-- this exact model and prompt version is skipped. A new model or prompt
-- version makes every promise eligible again, which is what append-only
-- evaluation is for.
SELECT p.promise_id, p.politician_id, pol.full_name,
       p.verbatim_quote, p.topic, p.specificity
FROM promises p
JOIN politicians pol USING (politician_id)
WHERE p.politician_id = %(politician_id)s
  AND p.quote_verified
  AND p.is_scoreable
  AND NOT EXISTS (
      SELECT 1 FROM promise_reviews r
      WHERE r.promise_id = p.promise_id
        AND r.verdict IN ('opinion', 'fragment', 'not_a_promise')
  )
  AND NOT EXISTS (
      SELECT 1 FROM promise_evaluations e
      WHERE e.promise_id = p.promise_id
        AND e.is_current
        AND e.model_name = %(model_name)s
        AND e.prompt_version = %(prompt_version)s
  )
  AND EXISTS (
      SELECT 1 FROM voting_records v WHERE v.politician_id = p.politician_id
  )
ORDER BY p.promise_id
