-- Insert a VERIFIED promise. Only verified quotes reach this statement —
-- the extraction stage rejects failures before any SQL runs.
-- Idempotent on (document_id, char_start, char_end): the same span
-- re-extracted (same or new run) is one promise.
INSERT INTO promises
    (politician_id, document_id, verbatim_quote, char_start, char_end,
     quote_verified, topic, specificity, is_scoreable, model_name, prompt_version)
VALUES
    (%(politician_id)s, %(document_id)s, %(verbatim_quote)s, %(char_start)s,
     %(char_end)s, TRUE, %(topic)s, %(specificity)s, %(is_scoreable)s,
     %(model_name)s, %(prompt_version)s)
ON CONFLICT (document_id, char_start, char_end) DO NOTHING
