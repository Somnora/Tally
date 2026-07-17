INSERT INTO extraction_rejects
    (document_id, politician_id, rejected_quote, chunk_offset, model_name, prompt_version)
VALUES
    (%(document_id)s, %(politician_id)s, %(rejected_quote)s, %(chunk_offset)s,
     %(model_name)s, %(prompt_version)s)
