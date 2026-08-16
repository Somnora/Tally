-- Evaluation health per model and prompt version.
--
-- `exportable` is the number that matters: it counts rows that satisfy
-- app_export_evaluations, meaning current, cited, and every citation
-- validated. The gap between `current` and `exportable` is the honest
-- measure of how often a model produced something that looked like an
-- answer but could not be backed up.
SELECT e.prompt_version, e.model_name, e.status,
       count(*)                                        AS evaluations,
       count(*) FILTER (WHERE e.is_current)            AS current,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM app_export_evaluations x
           WHERE x.evaluation_id = e.evaluation_id))   AS exportable
FROM promise_evaluations e
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
