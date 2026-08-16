-- Alignment verdicts. The view already refuses anything that is not current,
-- not cited, or carrying an unvalidated citation, so this query adds no gate
-- of its own; it just shapes the columns.
--
-- consistency_score is nullable on purpose. pending and unverifiable carry no
-- score because the record did not settle the question, and the app must show
-- that as "not established" rather than as a middling number.
SELECT e.evaluation_id, e.promise_id, e.status, e.consistency_score,
       e.llm_reasoning, e.model_name, e.prompt_version, e.created_at
FROM app_export_evaluations e
ORDER BY e.promise_id
