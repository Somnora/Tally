INSERT INTO candidate_totals
    (fec_candidate_id, cycle, politician_id, total_receipts, total_disbursements,
     cash_on_hand, debts_owed, individual_itemized, individual_unitemized,
     pac_contributions, coverage_end, source_id)
VALUES
    (%(fec_candidate_id)s, %(cycle)s, %(politician_id)s, %(total_receipts)s,
     %(total_disbursements)s, %(cash_on_hand)s, %(debts_owed)s,
     %(individual_itemized)s, %(individual_unitemized)s, %(pac_contributions)s,
     %(coverage_end)s, %(source_id)s)
ON CONFLICT (fec_candidate_id, cycle) DO UPDATE SET
    politician_id         = EXCLUDED.politician_id,
    total_receipts        = EXCLUDED.total_receipts,
    total_disbursements   = EXCLUDED.total_disbursements,
    cash_on_hand          = EXCLUDED.cash_on_hand,
    debts_owed            = EXCLUDED.debts_owed,
    individual_itemized   = EXCLUDED.individual_itemized,
    individual_unitemized = EXCLUDED.individual_unitemized,
    pac_contributions     = EXCLUDED.pac_contributions,
    coverage_end          = EXCLUDED.coverage_end,
    source_id             = EXCLUDED.source_id,
    updated_at            = now()
