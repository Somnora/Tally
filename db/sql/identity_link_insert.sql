-- Idempotent on the superseded id: re-running the linker must not stack rows.
INSERT INTO candidate_identity_links
    (incumbent_fec_id, other_fec_id, politician_id, superseded_politician_id,
     basis, source_id)
VALUES
    (%(incumbent_fec_id)s, %(other_fec_id)s, %(politician_id)s,
     %(superseded_politician_id)s, %(basis)s, %(source_id)s)
ON CONFLICT (other_fec_id) DO NOTHING
