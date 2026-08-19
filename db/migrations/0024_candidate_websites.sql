-- 0024: record the campaign website each candidate declared to the FEC.
--
-- The document pipeline reached 461 of 4,079 candidates, and the 3,618 it
-- missed were not a long tail of obscure filers: they were, almost exactly,
-- everyone who is not already in Congress. Official-site harvesting keys on
-- the congress-legislators roster, which lists sitting members and nobody
-- else, so a challenger's page showed their donors and then fell silent about
-- what they had said. Incumbents got to speak and their opponents did not,
-- which is a fairness defect of the same kind as naming only the capped money.
--
-- The URL comes from FEC Form 1, where a committee states its own web address
-- under penalty of law. That matters more than the coverage: the alternative
-- sources were a name search (which is how Sherrod Brown nearly became
-- Shontel Brown) or a third-party database. Here the campaign names its own
-- site and we join on the committee id, so there is no matching step in which
-- one candidate's words can be attributed to another.
--
-- declared_url is stored verbatim beside the normalized url because the filed
-- value is the evidence. Filings carry "WWW.EXAMPLE.COM", trailing spaces and
-- mixed case; normalizing in place would leave us asserting the campaign said
-- something slightly different from what it said.
CREATE TABLE candidate_websites (
    candidate_website_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    politician_id        bigint      NOT NULL REFERENCES politicians (politician_id),
    fec_candidate_id     text        NOT NULL,
    cycle                smallint    NOT NULL,
    cmte_id              text        NOT NULL REFERENCES committees (cmte_id),
    url                  text        NOT NULL,
    declared_url         text        NOT NULL,
    source_id            bigint      NOT NULL REFERENCES sources (source_id),
    discovered_at        timestamptz NOT NULL DEFAULT now(),
    -- Filled by the harvest pass, not discovery: what actually happened when
    -- we asked. A declared URL that 404s or that robots.txt closes is not a
    -- failure to hide, it is a coverage fact the methodology page reports.
    last_checked_at      timestamptz,
    fetch_outcome        text,
    UNIQUE (fec_candidate_id, cycle, url),
    CONSTRAINT candidate_websites_outcome_check CHECK (
        fetch_outcome IS NULL OR fetch_outcome IN
        ('documents_stored', 'no_content', 'unreachable', 'robots_disallowed')
    )
);

CREATE INDEX candidate_websites_politician_idx
    ON candidate_websites (politician_id);

COMMENT ON COLUMN candidate_websites.declared_url IS
    'The web address exactly as filed on FEC Form 1, before normalization.';
COMMENT ON COLUMN candidate_websites.cmte_id IS
    'The authorized committee that declared this address (designation P or A).';
