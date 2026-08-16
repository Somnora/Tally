-- One citation. `validated` stays FALSE until code has confirmed the record
-- exists AND actually supports the stated direction; the app_export view
-- refuses any evaluation carrying an unvalidated citation, so an unchecked
-- claim simply never reaches the public snapshot.
--
-- Exactly one id column is set, matching kind. The schema CHECK enforces it,
-- and the per-kind foreign keys mean "the cited record exists" is guaranteed
-- by the database rather than trusted from the model.
INSERT INTO evaluation_evidence
    (evaluation_id, kind, vote_id, donation_id, filing_uuid, document_id,
     direction, validated)
VALUES (%(evaluation_id)s, %(kind)s, %(vote_id)s, %(donation_id)s,
        %(filing_uuid)s, %(document_id)s, %(direction)s, %(validated)s)
RETURNING evidence_id
