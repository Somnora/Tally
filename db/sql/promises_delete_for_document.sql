-- Clear a document's promises before re-extracting it under a new prompt/model.
-- Safe while no evaluations exist yet (Milestone 5 introduces promise_evaluations,
-- which will require a re-extraction policy that preserves or supersedes them).
DELETE FROM promises WHERE document_id = %(document_id)s
