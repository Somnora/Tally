-- Members with at least one promise still awaiting evaluation.
--
-- The batch runner asks this instead of walking every politician, because
-- most of them are challengers with no voting record and would return an
-- empty list one connection at a time. Resumability falls out of it: a
-- member whose promises all carry a current evaluation from this model and
-- prompt version simply stops appearing, so an interrupted run continues
-- where it stopped rather than starting over.
--
-- The eligibility rules live in select_promises_for_evaluation.sql and are
-- deliberately repeated here rather than approximated. If the two ever
-- disagree, this query hands the runner a member whose promise list then
-- comes back empty, which wastes a connection and reports a member as done
-- that was never started.
SELECT p.politician_id, pol.full_name, count(*) AS pending
FROM promises p
JOIN politicians pol USING (politician_id)
WHERE p.quote_verified
  AND p.is_scoreable
  AND p.gate_keep
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
GROUP BY p.politician_id, pol.full_name
ORDER BY count(*) DESC, pol.full_name
