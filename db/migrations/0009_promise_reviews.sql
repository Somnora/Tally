-- 0009: human review verdicts on extracted promises.
--
-- Purpose: quantify extraction precision per prompt_version before writing
-- any v3 prompt, and feed the M5 evaluation stage only human-confirmed
-- promises where a reviewer has weighed in. One verdict per promise;
-- re-reviewing overwrites (the CLI is the only writer). The verdict a
-- reviewer gives applies to the promise AS STORED (its quote, topic, and
-- specificity together).

CREATE TABLE promise_reviews (
    promise_id     BIGINT PRIMARY KEY REFERENCES promises (promise_id) ON DELETE CASCADE,
    verdict        TEXT NOT NULL CHECK (verdict IN (
                       'correct',            -- real promise, right specificity
                       'opinion',            -- stance/approval, no commitment
                       'fragment',           -- too thin to mean anything
                       'not_a_promise',      -- some other leak class
                       'wrong_specificity',  -- real promise, mislabeled tier
                       'wrong_topic'         -- real promise, wrong topic tag
                   )),
    note           TEXT,
    prompt_version TEXT NOT NULL,  -- copied from the promise at review time
    model_name     TEXT NOT NULL,
    reviewed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX promise_reviews_verdict_idx ON promise_reviews (prompt_version, verdict);
