UPDATE ingestion_runs
SET finished_at = now(),
    status      = %(status)s,
    stats       = %(stats)s,
    error       = %(error)s
WHERE run_id = %(run_id)s
