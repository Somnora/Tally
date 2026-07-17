INSERT INTO ingestion_runs (run_type, politician_id)
VALUES (%(run_type)s, %(politician_id)s)
RETURNING run_id
