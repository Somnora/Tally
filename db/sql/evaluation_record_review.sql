-- Sign off (or reject) one broken verdict. Rejecting flips is_current, which
-- takes it out of the export by the same mechanism that withdrew evaluate_v3,
-- while keeping the row and the reason for it.
UPDATE promise_evaluations
   SET reviewed_at = now(),
       reviewed_by = %(reviewed_by)s,
       review_note = %(review_note)s,
       is_current  = CASE WHEN %(approved)s THEN is_current ELSE FALSE END
 WHERE evaluation_id = %(evaluation_id)s
