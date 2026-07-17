-- Documents needing extraction under the current prompt/model: never
-- processed, or processed with a different prompt version or model.
SELECT document_id, doc_type, title, url, full_text
FROM documents
WHERE politician_id = %(politician_id)s
  AND (extracted_at IS NULL
       OR extraction_prompt_version IS DISTINCT FROM %(prompt_version)s
       OR extraction_model IS DISTINCT FROM %(model_name)s)
ORDER BY document_id
