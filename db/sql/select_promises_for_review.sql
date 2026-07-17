-- Promises awaiting a human verdict, with enough document context for the
-- reviewer to judge without opening the source. Ordered by politician then
-- document so the reviewer keeps one speaker's voice in their head at a time.
SELECT
    p.promise_id,
    pol.full_name,
    d.title,
    d.doc_type,
    d.url,
    p.verbatim_quote,
    p.char_start,
    p.char_end,
    p.topic,
    p.specificity,
    p.is_scoreable,
    p.prompt_version,
    p.model_name,
    substring(d.full_text FROM greatest(1, p.char_start - %(context_chars)s + 1)
                          FOR least(p.char_start, %(context_chars)s)) AS context_before,
    substring(d.full_text FROM p.char_end + 1 FOR %(context_chars)s) AS context_after
FROM promises p
JOIN politicians pol USING (politician_id)
JOIN documents d ON d.document_id = p.document_id
WHERE p.promise_id NOT IN (SELECT promise_id FROM promise_reviews)
ORDER BY pol.full_name, d.document_id, p.char_start
