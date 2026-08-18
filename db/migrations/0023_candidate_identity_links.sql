-- 0023: record that two FEC candidate ids are the same person.
--
-- The FEC issues a new candidate id per office. A sitting member running for
-- a different seat therefore has two, and nothing in the bulk data joins
-- them, so we held their voting record under one identity and rendered their
-- live campaign under the other. Haley Stevens's Senate page showed a
-- Congresswoman with no voting record and no promises, as though we had
-- never heard of her, while 644 roll calls sat in the database under her
-- House id. Fourteen candidates were in that state.
--
-- Recorded rather than merged. Repointing a candidacy at a different person
-- is the kind of edit that, wrong, puts one member's votes on another
-- member's page, so the basis for every link is stored beside it and the
-- superseded identity is kept. Nothing here is inferred from a name we
-- matched across two systems: see pipeline/etl/link_candidacies.py for the
-- evidence each link requires.
CREATE TABLE candidate_identity_links (
    link_id                  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    incumbent_fec_id         text   NOT NULL,
    other_fec_id             text   NOT NULL UNIQUE,
    politician_id            bigint NOT NULL REFERENCES politicians (politician_id),
    superseded_politician_id bigint NOT NULL REFERENCES politicians (politician_id),
    basis                    text   NOT NULL,
    source_id                bigint NOT NULL REFERENCES sources (source_id),
    created_at               timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT identity_link_is_between_two_people
        CHECK (politician_id <> superseded_politician_id)
);

CREATE INDEX candidate_identity_links_politician_idx
    ON candidate_identity_links (politician_id);
