-- The receipts behind each verdict, denormalized so the app never needs the
-- 344,000-row voting_records table.
--
-- Only votes cited by an exported evaluation travel, and only validated
-- citations: an unvalidated one already disqualifies its whole evaluation
-- from the export view, so it can never reach here. Every row carries its
-- congress.gov URL, which is the point. The reader checks the receipt at the
-- source rather than taking the snapshot's word for it.
SELECT ev.evidence_id, ev.evaluation_id, ev.kind, ev.direction,
       v.vote_id, v.bill_number, v.vote_question, v.position, v.voted_at,
       v.vote_result, v.congress_gov_url,
       b.title AS bill_title, b.policy_area,
       coalesce(array_length(b.subjects, 1), 0) > 50 AS bill_is_omnibus
FROM evaluation_evidence ev
JOIN app_export_evaluations x ON x.evaluation_id = ev.evaluation_id
LEFT JOIN voting_records v ON v.vote_id = ev.vote_id
LEFT JOIN bills b ON b.congress = v.congress AND b.bill_key = v.bill_key
WHERE ev.validated
ORDER BY ev.evaluation_id, ev.evidence_id
