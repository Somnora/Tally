SELECT source_id
FROM sources
WHERE source_type = %(source_type)s
  AND content_hash = %(content_hash)s
