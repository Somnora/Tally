-- Precision report: verdict counts per prompt_version, plus overall
-- precision (correct + label-only errors still count as real promises).
SELECT
    prompt_version,
    verdict,
    count(*) AS n
FROM promise_reviews
GROUP BY prompt_version, verdict
ORDER BY prompt_version, n DESC
