INSERT INTO candidate_website_scans (fec_candidate_id, cycle, websites_found)
VALUES (%(fec_candidate_id)s, %(cycle)s, %(websites_found)s)
ON CONFLICT (fec_candidate_id, cycle) DO UPDATE SET
    websites_found = EXCLUDED.websites_found,
    scanned_at     = now()
