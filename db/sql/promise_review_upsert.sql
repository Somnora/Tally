INSERT INTO promise_reviews
    (promise_id, verdict, note, prompt_version, model_name)
VALUES
    (%(promise_id)s, %(verdict)s, %(note)s, %(prompt_version)s, %(model_name)s)
ON CONFLICT (promise_id) DO UPDATE SET
    verdict        = EXCLUDED.verdict,
    note           = EXCLUDED.note,
    prompt_version = EXCLUDED.prompt_version,
    model_name     = EXCLUDED.model_name,
    reviewed_at    = now()
