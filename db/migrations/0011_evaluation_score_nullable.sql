-- 0011: a score is a claim, so it may only exist when evidence backs it.
--
-- consistency_score was NOT NULL, which forced every evaluation to carry a
-- number even when the record could not settle the question. The tempting
-- fill value is 50, and 50 is a lie: it sits in the middle of the scale, so
-- a reader (and any averaging rollup) takes it as "evidence on both sides,
-- roughly balanced" when the truth is "no evidence at all". The two states
-- are opposites and must not share a representation.
--
-- After this migration the score exists exactly when the status is one the
-- evidence can support, and is NULL otherwise. The database enforces the
-- pairing so no prompt change, model swap, or careless insert can smuggle a
-- number onto an unevidenced verdict.
--
-- Note the append-only trigger needs no change: it compares with IS DISTINCT
-- FROM, which already treats NULL correctly.

ALTER TABLE promise_evaluations ALTER COLUMN consistency_score DROP NOT NULL;

ALTER TABLE promise_evaluations DROP CONSTRAINT promise_evaluations_consistency_score_check;

ALTER TABLE promise_evaluations ADD CONSTRAINT promise_evaluations_score_matches_status CHECK (
    -- The record spoke: a score is required.
    (status IN ('completed', 'in_progress', 'broken')
     AND consistency_score BETWEEN 1 AND 100)
    OR
    -- The record was silent or unreadable: there is nothing to score.
    (status IN ('pending', 'unverifiable')
     AND consistency_score IS NULL)
);
