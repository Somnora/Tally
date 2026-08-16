-- 0012: which roll-call votes are topically relevant to a promise.
--
-- The evaluation stage must not see all 610 of a member's votes. The rule is
-- pre-digest before prompting: hand the model a short, relevant list where
-- every item carries its vote_id, never the raw record. This table is that
-- filter, expressed as data rather than as a query, so revising the editorial
-- judgment is a migration and a diff instead of a code change.
--
-- Two vocabularies do the work, both from Congress.gov:
--   policy_areas  one coarse area per bill, good for broad topics like
--                 Immigration or Taxation.
--   subjects      many precise terms per bill, needed where the policy area
--                 is too blunt. "guns" is the clearest case: its policy area
--                 (Crime and Law Enforcement) would drag in every drug and
--                 sentencing bill, while the subject Firearms and explosives
--                 is exact. veterans is the same story against Armed Forces
--                 and National Security.
--
-- A bill matches a topic if it hits EITHER list. Every term seeded below was
-- checked against the 461 bills actually loaded; nothing here is guessed.
-- Terms absent from our corpus were dropped rather than left in to rot.
--
-- topic 'other' is deliberately empty. A promise that fits none of the
-- vocabulary matches no votes and is evaluated as unverifiable, which is the
-- honest outcome rather than a forced guess.
--
-- This mapping is an editorial judgment and belongs on the public
-- methodology page.

CREATE TABLE topic_vote_filters (
    topic        TEXT PRIMARY KEY,
    policy_areas TEXT[] NOT NULL DEFAULT '{}',
    subjects     TEXT[] NOT NULL DEFAULT '{}'
);

INSERT INTO topic_vote_filters (topic, policy_areas, subjects) VALUES
('abortion',
 ARRAY['Health'],
 ARRAY['Abortion']),

('democracy',
 ARRAY['Government Operations and Politics', 'Congress', 'Law'],
 ARRAY['Elections, voting, political campaign regulation', 'Voting rights',
       'Election Assistance Commission', 'Federal Election Commission (FEC)']),

('economy',
 ARRAY['Economics and Public Finance', 'Commerce', 'Finance and Financial Sector'],
 ARRAY['Inflation and prices', 'Wages and earnings', 'Unemployment']),

('education',
 ARRAY['Education'],
 ARRAY['Student aid and college costs', 'Elementary and secondary education',
       'School administration']),

('energy',
 ARRAY['Energy'],
 ARRAY['Oil and gas', 'Alternative and renewable resources']),

('environment',
 ARRAY['Environmental Protection', 'Public Lands and Natural Resources',
       'Water Resources Development'],
 ARRAY['Climate change and greenhouse gases', 'Marine pollution',
       'Marine and coastal resources, fisheries']),

-- Congressional stock trading and conflicts of interest. The policy area
-- Congress is broad, so the corruption subject carries most of the weight.
('ethics',
 ARRAY[]::TEXT[],
 ARRAY['Government ethics and transparency, public corruption',
       'House Committee on Ethics', 'Office of Government Ethics']),

('foreign_policy',
 ARRAY['International Affairs', 'Foreign Trade and International Finance'],
 ARRAY[]::TEXT[]),

-- Subject only: the policy area would pull in unrelated crime bills.
('guns',
 ARRAY[]::TEXT[],
 ARRAY['Firearms and explosives']),

('healthcare',
 ARRAY['Health'],
 ARRAY['Health care coverage and access', 'Health care costs and insurance',
       'Prescription drugs', 'Medicaid', 'Drug therapy']),

('housing',
 ARRAY['Housing and Community Development'],
 ARRAY[]::TEXT[]),

('immigration',
 ARRAY['Immigration'],
 ARRAY['Border security and unlawful immigration',
       'Immigration status and procedures']),

('labor',
 ARRAY['Labor and Employment'],
 ARRAY['Labor-management relations', 'Labor standards', 'Wages and earnings']),

('law_enforcement',
 ARRAY['Crime and Law Enforcement'],
 ARRAY['Drug trafficking and controlled substances',
       'Drug Enforcement Administration (DEA)']),

('national_security',
 ARRAY['Armed Forces and National Security', 'Emergency Management'],
 ARRAY[]::TEXT[]),

-- Retirement security. Medicare sits here as well as under healthcare
-- because promises about solvency are almost always framed this way.
('social_security',
 ARRAY['Social Welfare'],
 ARRAY['Social security and elderly assistance', 'Medicare']),

('taxes',
 ARRAY['Taxation'],
 ARRAY[]::TEXT[]),

-- Subject only, for the same reason as guns.
('veterans',
 ARRAY[]::TEXT[],
 ARRAY['Veterans'' medical care', 'Veterans'' pensions and compensation',
       'Department of Veterans Affairs',
       'Veterans'' education, employment, rehabilitation',
       'Veterans'' loans, housing, homeless programs',
       'Veterans'' organizations and recognition']),

('other', ARRAY[]::TEXT[], ARRAY[]::TEXT[]);
