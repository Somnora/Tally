-- Upsert for candidates with no crosswalk match yet: provisionally keyed by
-- their FEC candidate id and flagged for later linkage.
INSERT INTO politicians (full_name, party, state, fec_candidate_id, needs_linkage, source_id)
VALUES (%(full_name)s, %(party)s, %(state)s, %(fec_candidate_id)s, TRUE, %(source_id)s)
ON CONFLICT (fec_candidate_id) DO UPDATE SET
    full_name  = EXCLUDED.full_name,
    party      = COALESCE(EXCLUDED.party, politicians.party),
    state      = COALESCE(EXCLUDED.state, politicians.state),
    updated_at = now()
RETURNING politician_id
