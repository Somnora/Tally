-- Retire the current evaluation for a promise before a new one is inserted.
-- promise_evaluations is append-only: the old row keeps its score and
-- reasoning forever, and only is_current flips. That is the one UPDATE the
-- append-only trigger permits, and it is what keeps a unique current
-- evaluation per promise without ever destroying the history that produced
-- an earlier public verdict.
UPDATE promise_evaluations
SET is_current = FALSE
WHERE promise_id = %(promise_id)s AND is_current
