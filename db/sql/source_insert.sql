-- Idempotent by (source_type, content_hash): re-downloading identical bytes
-- returns no row here; the caller then looks the existing row up.
INSERT INTO sources (source_type, url, content_hash, raw_payload, raw_path)
VALUES (%(source_type)s, %(url)s, %(content_hash)s, %(raw_payload)s, %(raw_path)s)
ON CONFLICT (source_type, content_hash) DO NOTHING
RETURNING source_id
