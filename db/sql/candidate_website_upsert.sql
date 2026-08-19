-- Idempotent on (candidate, cycle, normalized url). A campaign that refiles
-- the same address must not stack rows; one that files a genuinely different
-- address gets a second row, because both were declared and we do not get to
-- decide which one the campaign meant.
INSERT INTO candidate_websites
    (politician_id, fec_candidate_id, cycle, cmte_id, url, declared_url, source_id)
VALUES
    (%(politician_id)s, %(fec_candidate_id)s, %(cycle)s, %(cmte_id)s,
     %(url)s, %(declared_url)s, %(source_id)s)
ON CONFLICT (fec_candidate_id, cycle, url) DO UPDATE SET
    declared_url = EXCLUDED.declared_url,
    cmte_id      = EXCLUDED.cmte_id
RETURNING candidate_website_id
