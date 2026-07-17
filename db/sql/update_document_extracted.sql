UPDATE documents
SET extracted_at = now(),
    extraction_model = %(model_name)s,
    extraction_prompt_version = %(prompt_version)s
WHERE document_id = %(document_id)s
