-- Upsert for crosswalk-matched people. fec_candidate_id stays untouched here:
-- a person's FEC ids live in id_crosswalk.fec_candidate_ids (plural).
INSERT INTO politicians (full_name, party, state, bioguide_id, needs_linkage, source_id)
VALUES (%(full_name)s, %(party)s, %(state)s, %(bioguide_id)s, FALSE, %(source_id)s)
ON CONFLICT (bioguide_id) DO UPDATE SET
    full_name     = EXCLUDED.full_name,
    party         = COALESCE(EXCLUDED.party, politicians.party),
    state         = COALESCE(EXCLUDED.state, politicians.state),
    needs_linkage = FALSE,
    updated_at    = now()
RETURNING politician_id
