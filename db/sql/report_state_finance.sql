SELECT f.full_name, f.office, f.district, f.party, f.is_special,
       f.total_receipts, f.cash_on_hand,
       f.individual_itemized_official, f.individual_itemized_loaded,
       f.individual_refunds, f.pac_itemized,
       f.ie_support, f.ie_oppose,
       f.itemized_rows, f.coverage_end
FROM mv_candidacy_finance f
WHERE f.state = %(state)s AND f.cycle = %(cycle)s
  AND (f.total_receipts IS NOT NULL OR f.itemized_rows IS NOT NULL)
ORDER BY f.total_receipts DESC NULLS LAST
