-- Set or clear one evaluation's is_current flag. This is the only column the
-- append-only trigger permits an UPDATE to touch, so demoting an evaluation
-- that no longer validates never destroys the score or reasoning that
-- produced an earlier public verdict.
UPDATE promise_evaluations
SET is_current = %(is_current)s
WHERE evaluation_id = %(evaluation_id)s
