-- 0025: record that we asked a candidate's committees for a website, even
-- when the answer was no.
--
-- Without this, resume only covered candidates who HAD a site: a rerun
-- re-queried every candidate who did not, which is most of a four-hour pass.
-- The sources table cannot stand in for it, because it is unique on
-- (source_type, content_hash) and every "this candidate has no committees"
-- payload hashes identically, so four thousand negative answers collapse into
-- a single row that names none of them.
--
-- It is also the honest denominator. "We have no promises for this candidate"
-- is a different statement from "this candidate declared no website", and
-- reporting the second requires having written it down.
CREATE TABLE candidate_website_scans (
    fec_candidate_id text     NOT NULL,
    cycle            smallint NOT NULL,
    websites_found   smallint NOT NULL,
    scanned_at       timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (fec_candidate_id, cycle)
);

COMMENT ON TABLE candidate_website_scans IS
    'One row per candidate we asked the FEC about, whatever the answer. '
    'websites_found = 0 means the campaign declared no usable web address.';
