-- Idempotent on FEC's sub_id. DO UPDATE (not NOTHING) because amended
-- filings revise amounts/dates for the same sub_id on re-download.
INSERT INTO donations
    (fec_sub_id, recipient_cmte_id, fec_candidate_id, politician_id,
     contributor_name, contributor_cmte_id, amount, contributed_at, cycle,
     transaction_tp, entity_tp, transaction_pgi, employer, occupation,
     donor_city, donor_state, donor_zip, image_num, memo_cd, memo_text, source_id)
VALUES
    (%(fec_sub_id)s, %(recipient_cmte_id)s, %(fec_candidate_id)s, %(politician_id)s,
     %(contributor_name)s, %(contributor_cmte_id)s, %(amount)s, %(contributed_at)s, %(cycle)s,
     %(transaction_tp)s, %(entity_tp)s, %(transaction_pgi)s, %(employer)s, %(occupation)s,
     %(donor_city)s, %(donor_state)s, %(donor_zip)s, %(image_num)s, %(memo_cd)s,
     %(memo_text)s, %(source_id)s)
ON CONFLICT (fec_sub_id) DO UPDATE SET
    recipient_cmte_id = EXCLUDED.recipient_cmte_id,
    fec_candidate_id  = EXCLUDED.fec_candidate_id,
    politician_id     = EXCLUDED.politician_id,
    contributor_name  = EXCLUDED.contributor_name,
    contributor_cmte_id = EXCLUDED.contributor_cmte_id,
    amount            = EXCLUDED.amount,
    contributed_at    = EXCLUDED.contributed_at,
    transaction_tp    = EXCLUDED.transaction_tp,
    entity_tp         = EXCLUDED.entity_tp,
    transaction_pgi   = EXCLUDED.transaction_pgi,
    employer          = EXCLUDED.employer,
    occupation        = EXCLUDED.occupation,
    donor_city        = EXCLUDED.donor_city,
    donor_state       = EXCLUDED.donor_state,
    donor_zip         = EXCLUDED.donor_zip,
    image_num         = EXCLUDED.image_num,
    memo_cd           = EXCLUDED.memo_cd,
    memo_text         = EXCLUDED.memo_text,
    source_id         = EXCLUDED.source_id
