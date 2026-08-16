-- Record the selectivity gate's verdict on one promise. Rerunnable: a new
-- gate version simply overwrites, because the gate is a pure function of the
-- quote and carries no history worth preserving. The promise itself is never
-- touched, only the screen's opinion of it.
UPDATE promises
SET gate_keep    = %(gate_keep)s,
    gate_reason  = %(gate_reason)s,
    gate_version = %(gate_version)s
WHERE promise_id = %(promise_id)s
