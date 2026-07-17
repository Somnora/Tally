-- Idempotent by (politician_id, content_hash): identical text for the same
-- candidate is one document no matter how many times it is fetched.
-- No-op DO UPDATE so RETURNING always yields the document_id.
INSERT INTO documents
    (politician_id, source_id, doc_type, title, url, published_at, full_text,
     content_hash, transcribed_by, meta)
VALUES
    (%(politician_id)s, %(source_id)s, %(doc_type)s, %(title)s, %(url)s,
     %(published_at)s, %(full_text)s, %(content_hash)s, %(transcribed_by)s, %(meta)s)
ON CONFLICT (politician_id, content_hash) DO UPDATE SET politician_id = EXCLUDED.politician_id
RETURNING document_id
