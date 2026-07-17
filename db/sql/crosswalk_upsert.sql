INSERT INTO id_crosswalk
    (bioguide_id, full_name, fec_candidate_ids, govtrack_id, icpsr_id, opensecrets_id, source_id)
VALUES
    (%(bioguide_id)s, %(full_name)s, %(fec_candidate_ids)s, %(govtrack_id)s,
     %(icpsr_id)s, %(opensecrets_id)s, %(source_id)s)
ON CONFLICT (bioguide_id) DO UPDATE SET
    full_name         = EXCLUDED.full_name,
    fec_candidate_ids = EXCLUDED.fec_candidate_ids,
    govtrack_id       = EXCLUDED.govtrack_id,
    icpsr_id          = EXCLUDED.icpsr_id,
    opensecrets_id    = EXCLUDED.opensecrets_id,
    source_id         = EXCLUDED.source_id,
    updated_at        = now()
