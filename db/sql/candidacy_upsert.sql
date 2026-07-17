INSERT INTO candidacies
    (race_id, politician_id, fec_candidate_id, party, incumbent_challenger,
     cand_status, principal_cmte_id, source_id)
VALUES
    (%(race_id)s, %(politician_id)s, %(fec_candidate_id)s, %(party)s,
     %(incumbent_challenger)s, %(cand_status)s, %(principal_cmte_id)s, %(source_id)s)
ON CONFLICT (race_id, fec_candidate_id) DO UPDATE SET
    politician_id        = EXCLUDED.politician_id,
    party                = EXCLUDED.party,
    incumbent_challenger = EXCLUDED.incumbent_challenger,
    cand_status          = EXCLUDED.cand_status,
    principal_cmte_id    = EXCLUDED.principal_cmte_id,
    source_id            = EXCLUDED.source_id,
    updated_at           = now()
RETURNING candidacy_id
