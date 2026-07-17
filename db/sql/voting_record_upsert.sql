INSERT INTO voting_records
    (politician_id, congress, chamber, session, roll_call_number, bill_number,
     vote_question, position, vote_result, voted_at, congress_gov_url, source_id)
VALUES
    (%(politician_id)s, %(congress)s, %(chamber)s, %(session)s, %(roll_call_number)s,
     %(bill_number)s, %(vote_question)s, %(position)s, %(vote_result)s, %(voted_at)s,
     %(congress_gov_url)s, %(source_id)s)
ON CONFLICT (politician_id, congress, chamber, session, roll_call_number) DO UPDATE SET
    bill_number      = EXCLUDED.bill_number,
    vote_question    = EXCLUDED.vote_question,
    position         = EXCLUDED.position,
    vote_result      = EXCLUDED.vote_result,
    voted_at         = EXCLUDED.voted_at,
    congress_gov_url = EXCLUDED.congress_gov_url,
    source_id        = EXCLUDED.source_id
