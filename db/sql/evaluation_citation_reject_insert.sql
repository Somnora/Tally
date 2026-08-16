-- A citation that could not become evidence. Usually a fabricated id, which
-- is the reason this table exists: it is the clearest evidence that a prompt
-- or model is unfit, and it is invisible in the model's prose.
INSERT INTO evaluation_citation_rejects
    (evaluation_id, kind, cited_record_id, direction, reason)
VALUES (%(evaluation_id)s, %(kind)s, %(cited_record_id)s, %(direction)s, %(reason)s)
