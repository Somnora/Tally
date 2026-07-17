INSERT INTO media_assets
    (politician_id, platform, external_id, title, channel_title, url,
     published_at, has_captions, document_id, source_id)
VALUES
    (%(politician_id)s, %(platform)s, %(external_id)s, %(title)s, %(channel_title)s,
     %(url)s, %(published_at)s, %(has_captions)s, %(document_id)s, %(source_id)s)
ON CONFLICT (platform, external_id, politician_id) DO UPDATE SET
    title         = EXCLUDED.title,
    channel_title = EXCLUDED.channel_title,
    published_at  = EXCLUDED.published_at,
    has_captions  = COALESCE(EXCLUDED.has_captions, media_assets.has_captions),
    document_id   = COALESCE(EXCLUDED.document_id, media_assets.document_id),
    updated_at    = now()
RETURNING asset_id
