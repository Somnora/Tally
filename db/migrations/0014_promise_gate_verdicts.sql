-- 0014: persist the selectivity gate's verdict, and stop the public snapshot
-- from shipping things that were never promises.
--
-- The problem this fixes is live. app_export_promises gated only on
-- quote_verified, so all 122 promises were exportable including the 47 that
-- reviewers rejected as opinions, fragments, or not promises at all.
-- Invariant 1 was satisfied the whole time (every quote is genuinely in its
-- source document), but a voter reading the result would see "Chellie
-- believes these consumer protections are non-negotiable" presented as a
-- campaign promise. An accurate quote of a non-promise is still a false
-- claim about what someone pledged.
--
-- Two filters, because they cover different ground:
--
--   promise_reviews    human and triage verdicts. Authoritative, but only
--                      ever covers what someone has looked at. 107 of 122
--                      today, and a vanishing share once this runs past one
--                      state.
--   gate_keep          pipeline.promise_gate's deterministic screen, which
--                      measured 92% precision against the gold set versus
--                      60% ungated, losing one real promise. This is the
--                      part that scales, because nobody is hand-reviewing
--                      435 districts.
--
-- gate_keep is NULL until the screen has run, so an unscreened promise is
-- distinguishable from one the gate cleared. The export view treats NULL as
-- "not yet refused" rather than silently dropping unscreened rows, and the
-- export job refuses to build a snapshot while any exportable promise is
-- still unscreened. The database states the policy; the job enforces that
-- the policy has actually been applied.

ALTER TABLE promises ADD COLUMN gate_keep    BOOLEAN;
ALTER TABLE promises ADD COLUMN gate_reason  TEXT;
ALTER TABLE promises ADD COLUMN gate_version TEXT;

CREATE INDEX promises_gate_idx ON promises (gate_keep) WHERE gate_keep IS NOT TRUE;

-- Rebuild the export view with the selectivity filters in place.
DROP VIEW app_export_promises;

CREATE VIEW app_export_promises AS
SELECT p.promise_id, p.politician_id, p.document_id, p.verbatim_quote,
       p.char_start, p.char_end, p.topic, p.specificity, p.is_scoreable
FROM promises p
WHERE p.quote_verified
  -- A reviewer said this is not a promise. Their verdict is final and
  -- outranks the gate in both directions.
  AND NOT EXISTS (
      SELECT 1 FROM promise_reviews r
      WHERE r.promise_id = p.promise_id
        AND r.verdict IN ('opinion', 'fragment', 'not_a_promise')
  )
  -- The gate refused it. NULL means not yet screened, which the export job
  -- treats as a hard error rather than a pass.
  AND p.gate_keep IS NOT FALSE;
