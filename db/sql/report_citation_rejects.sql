-- Why citations were refused, most common first. This is the diagnostic that
-- says whether a prompt or a model is fit: unknown_record means outright
-- fabrication, not_offered means the model ignored the supplied list, and
-- position_mismatch means it misread the rows it was given.
SELECT r.reason, count(*) AS citations,
       count(DISTINCT r.evaluation_id) AS evaluations_affected
FROM evaluation_citation_rejects r
GROUP BY 1
ORDER BY 2 DESC
