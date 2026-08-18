-- Every stored citation, with the politician who made the promise, so
-- validation can be re-run later against facts that may have changed.
--
-- Validation at write time is a snapshot. Bill metadata arrives and gets
-- corrected, so a vote that was not known to be part of an omnibus can
-- become one, which retroactively means a citation calling it clean support
-- should no longer be trusted. Re-running turns invariant 2 from something
-- checked once into something that stays true.
-- The promise's topic rides along because the contested-subject rule needs
-- it: a firearms or abortion vote may only be scored when the promise itself
-- raises that subject. Without the topic, revalidation would judge every such
-- citation as though the promise were about nothing and demote all of them.
SELECT ev.evidence_id, ev.evaluation_id, ev.kind, ev.vote_id, ev.direction,
       ev.validated, p.politician_id, e.status, e.is_current, p.topic
FROM evaluation_evidence ev
JOIN promise_evaluations e ON e.evaluation_id = ev.evaluation_id
JOIN promises p ON p.promise_id = e.promise_id
ORDER BY ev.evaluation_id, ev.evidence_id
