-- Broken-promise verdicts waiting on a human signature.
--
-- Everything a reviewer needs to judge without opening the database: the
-- promise in the member's own words, the model's reasoning, and every vote it
-- cited with what that bill actually does. A reviewer asked to approve
-- "broken, score 15" with nothing else in front of them is a rubber stamp,
-- which is the opposite of the point.
SELECT e.evaluation_id, pol.full_name, p.topic, p.verbatim_quote,
       e.consistency_score, e.llm_reasoning,
       coalesce(
           json_agg(
               json_build_object(
                   'bill_key', v.bill_key,
                   'position', v.position,
                   'direction', ev.direction,
                   'title', b.title,
                   'summary', left(b.summary_text, 300),
                   'url', v.congress_gov_url
               ) ORDER BY ev.evidence_id
           ) FILTER (WHERE ev.evidence_id IS NOT NULL),
           '[]'::json
       ) AS citations
FROM promise_evaluations e
JOIN promises p ON p.promise_id = e.promise_id
JOIN politicians pol ON pol.politician_id = p.politician_id
LEFT JOIN evaluation_evidence ev ON ev.evaluation_id = e.evaluation_id
LEFT JOIN voting_records v ON v.vote_id = ev.vote_id
LEFT JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
WHERE e.status = 'broken' AND e.is_current AND e.reviewed_at IS NULL
GROUP BY e.evaluation_id, pol.full_name, p.topic, p.verbatim_quote,
         e.consistency_score, e.llm_reasoning
ORDER BY e.evaluation_id
