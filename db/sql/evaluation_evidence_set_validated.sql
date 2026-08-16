-- Flip one citation's validated flag after a revalidation pass.
UPDATE evaluation_evidence
SET validated = %(validated)s
WHERE evidence_id = %(evidence_id)s
