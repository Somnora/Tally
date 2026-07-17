-- Same predicate as select_documents_for_extraction: a document needs work
-- when never extracted OR extracted under a different prompt/model.
SELECT DISTINCT politician_id
FROM documents
WHERE extracted_at IS NULL
   OR extraction_prompt_version IS DISTINCT FROM %(prompt_version)s
   OR extraction_model IS DISTINCT FROM %(model_name)s
