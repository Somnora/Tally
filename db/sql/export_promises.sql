-- Displayable promises, with just enough surrounding text to read the quote
-- in context.
--
-- The snapshot carries a context WINDOW, not the source document. A promise
-- is a receipt: the exact words, enough context to show they were not
-- clipped into a different meaning, and a link to the original. Republishing
-- whole transcripts and campaign pages would multiply the snapshot size by
-- the number of documents and copy other people's content wholesale, for no
-- gain the link does not already provide.
--
-- Offsets are 1-based in SQL and 0-based in the promises table, hence the
-- +1 in each substring position.
SELECT e.promise_id, e.politician_id, e.topic, e.specificity, e.is_scoreable,
       e.verbatim_quote,
       d.doc_type, d.title AS document_title, d.url AS document_url,
       d.published_at,
       substring(
           d.full_text
           FROM greatest(1, e.char_start + 1 - %(context_chars)s)
           FOR least(e.char_start, %(context_chars)s)
       ) AS context_before,
       substring(d.full_text FROM e.char_end + 1 FOR %(context_chars)s) AS context_after
FROM app_export_promises e
JOIN documents d ON d.document_id = e.document_id
ORDER BY e.politician_id, e.promise_id
