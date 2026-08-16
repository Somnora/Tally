-- 0015: official congressional sites as their own document type.
--
-- A sitting member's house.gov or senate.gov site carries issue positions and
-- press releases: statements about what they intend to do in office, which is
-- exactly what the evaluation stage compares against their roll-call record.
-- It is also the only promise source that costs no API quota, which is why it
-- can run today while YouTube discovery cannot.
--
-- It gets its own doc_type rather than being folded into campaign_site,
-- because the two are not the same thing and the difference is one a reader
-- deserves to see. An official site is government speech, published by a
-- member's congressional office under rules that restrict campaign content; a
-- campaign site is the candidate asking for a vote. Both are that person
-- stating what they will do, but only one of them is a campaign promise, and
-- collapsing them would quietly overstate what we found.

ALTER TABLE documents DROP CONSTRAINT documents_doc_type_check;

ALTER TABLE documents ADD CONSTRAINT documents_doc_type_check CHECK (
    doc_type IN (
        'youtube_transcript',
        'press_release',
        'campaign_site',
        'official_site',      -- house.gov / senate.gov issue pages
        'wayback_snapshot',
        'debate_transcript',
        'other'
    )
);
