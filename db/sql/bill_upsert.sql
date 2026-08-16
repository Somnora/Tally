-- Idempotent on (congress, bill_key). Re-fetching a bill refreshes the
-- mutable fields (a bill's title, subjects, summary and latest action all
-- change as it moves through Congress) and repoints provenance at the
-- newest payload.
INSERT INTO bills (congress, bill_key, bill_type, bill_number, title,
                   policy_area, subjects, summary_text, introduced_date,
                   latest_action, latest_action_date, sponsor_bioguide,
                   congress_gov_url, source_id)
VALUES (%(congress)s, %(bill_key)s, %(bill_type)s, %(bill_number)s, %(title)s,
        %(policy_area)s, %(subjects)s, %(summary_text)s, %(introduced_date)s,
        %(latest_action)s, %(latest_action_date)s, %(sponsor_bioguide)s,
        %(congress_gov_url)s, %(source_id)s)
ON CONFLICT (congress, bill_key) DO UPDATE SET
    title              = EXCLUDED.title,
    policy_area        = EXCLUDED.policy_area,
    subjects           = EXCLUDED.subjects,
    summary_text       = EXCLUDED.summary_text,
    introduced_date    = EXCLUDED.introduced_date,
    latest_action      = EXCLUDED.latest_action,
    latest_action_date = EXCLUDED.latest_action_date,
    sponsor_bioguide   = EXCLUDED.sponsor_bioguide,
    congress_gov_url   = EXCLUDED.congress_gov_url,
    source_id          = EXCLUDED.source_id,
    fetched_at         = now()
RETURNING bill_id
