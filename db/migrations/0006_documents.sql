-- 0006: documents pipeline support (Milestone 4).
--
-- media_assets: discovered audio/video (YouTube for now) per candidate.
-- Videos WITH public captions become documents immediately; videos without
-- wait here for whisper transcription on a GPU instance. This table is the
-- GPU work queue — execution location changes nothing about the data path,
-- because the transcript still lands in documents via the same gates.

CREATE TABLE media_assets (
    asset_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    politician_id  BIGINT NOT NULL REFERENCES politicians (politician_id),
    platform       TEXT   NOT NULL DEFAULT 'youtube' CHECK (platform IN ('youtube', 'other')),
    external_id    TEXT   NOT NULL,             -- YouTube video id
    title          TEXT,
    channel_title  TEXT,
    url            TEXT   NOT NULL,
    published_at   TIMESTAMPTZ,
    has_captions   BOOLEAN,                     -- NULL = not yet checked
    document_id    BIGINT REFERENCES documents (document_id),  -- set once transcript stored
    source_id      BIGINT NOT NULL REFERENCES sources (source_id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (platform, external_id, politician_id)
);

CREATE INDEX media_assets_pending_idx
    ON media_assets (politician_id) WHERE document_id IS NULL;
