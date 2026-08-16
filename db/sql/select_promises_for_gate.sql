-- Verified promises and their quotes, for the selectivity screen.
--
-- %(only_unscreened)s TRUE limits the run to promises the current gate
-- version has not seen, which is the cheap incremental path. FALSE rescreens
-- everything, which is what a gate version bump needs.
SELECT p.promise_id, p.verbatim_quote
FROM promises p
WHERE p.quote_verified
  AND (NOT %(only_unscreened)s
       OR p.gate_version IS DISTINCT FROM %(gate_version)s)
ORDER BY p.promise_id
