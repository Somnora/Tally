INSERT INTO committees
    (cmte_id, name, cmte_type, cmte_designation, party, connected_org, cand_id, state, cycle, source_id)
VALUES
    (%(cmte_id)s, %(name)s, %(cmte_type)s, %(cmte_designation)s, %(party)s,
     %(connected_org)s, %(cand_id)s, %(state)s, %(cycle)s, %(source_id)s)
ON CONFLICT (cmte_id) DO UPDATE SET
    name             = EXCLUDED.name,
    cmte_type        = EXCLUDED.cmte_type,
    cmte_designation = EXCLUDED.cmte_designation,
    party            = EXCLUDED.party,
    connected_org    = EXCLUDED.connected_org,
    cand_id          = EXCLUDED.cand_id,
    state            = EXCLUDED.state,
    cycle            = GREATEST(committees.cycle, EXCLUDED.cycle),
    source_id        = EXCLUDED.source_id,
    updated_at       = now()
