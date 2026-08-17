-- 0016: cover the topics the extractor actually produces, and stop the
-- filter table from carrying the same policy filter in a dozen places.
--
-- Two problems, one table.
--
-- COVERAGE. 0012 seeded 19 topics chosen from a five-candidate pilot. The
-- national harvest produced 183 distinct topics, and 600 of 1,848 promises
-- (32%) carry one the table has never heard of. An unknown topic matches no
-- filter row, returns no votes, and the evaluation stage records 'pending'.
-- So a third of the corpus was not being judged badly; it was not being
-- judged at all, and the reason was a vocabulary gap rather than a missing
-- record.
--
-- ALIASES. Most of that gap is not new subject matter. 'climate',
-- 'climate_change' and 'clean_energy' all want the environment filter;
-- 'seniors' and 'medicare' want social_security; 'voting_rights' and
-- 'campaign_finance' want democracy. Copying the arrays into each row would
-- work today and drift tomorrow: improving the environment filter would mean
-- remembering every synonym that quietly holds a stale copy of it. So an
-- alias row stores no filter at all, only a pointer at the topic whose filter
-- it shares, and select_votes_for_promise resolves the pointer.
--
-- The model is not asked to pick from a fixed topic list, deliberately: a
-- closed vocabulary would push it to mislabel a promise into whatever bucket
-- was nearest. Extraction stays free to name the subject and this table
-- decides what that name means, which keeps the mapping reviewable in git
-- instead of buried in a prompt.
--
-- Every subject string below was checked against the subject vocabulary
-- actually present in `bills`. A filter that names a term Congress.gov does
-- not use is indistinguishable from no filter at all.

ALTER TABLE topic_vote_filters
    ADD COLUMN canonical_topic TEXT REFERENCES topic_vote_filters (topic);

-- A row is either a real filter or a pointer to one, never both and never
-- neither. Without this an alias could quietly carry half a filter and match
-- on it, which is the exact drift the pointer exists to prevent.
ALTER TABLE topic_vote_filters ADD CONSTRAINT topic_vote_filters_alias_check CHECK (
    (canonical_topic IS NULL)
    OR (canonical_topic <> topic
        AND policy_areas = '{}'::text[]
        AND subjects = '{}'::text[])
);

-- -- new canonical filters ---------------------------------------------------

INSERT INTO topic_vote_filters (topic, policy_areas, subjects) VALUES

-- The largest single gap: 69 promises. Farm policy is its own world and
-- nothing already in the table comes close to it.
('agriculture', ARRAY['Agriculture and Food'],
 ARRAY['Agricultural prices, subsidies, credit', 'Agricultural trade', 'Farmland',
       'Food industry and services', 'Food assistance and relief', 'Nutrition and diet']),

('civil_rights', ARRAY['Civil Rights and Liberties, Minority Issues'],
 ARRAY['Racial and ethnic relations', 'Sex, gender, sexual orientation discrimination',
       'Commission on Civil Rights', 'Minority education']),

-- Distinct from 'economy': this is appropriations and the debt, not wages
-- and prices, and the two draw different roll calls.
('budget', ARRAY['Economics and Public Finance'],
 ARRAY['Appropriations', 'Budget process', 'Budget deficits and national debt',
       'Executive agency funding and structure']),

('consumer_protection', ARRAY['Commerce'],
 ARRAY['Consumer affairs', 'Financial services and investments']),

('small_business', ARRAY['Commerce'],
 ARRAY['Small business', 'Minority and disadvantaged businesses']),

('infrastructure', ARRAY['Transportation and Public Works'],
 ARRAY['Roads and highways', 'Public transit', 'Aviation and airports',
       'Internet, web applications, social media']),

('transportation', ARRAY['Transportation and Public Works'],
 ARRAY['Roads and highways', 'Public transit', 'Aviation and airports']),

('trade', ARRAY['Foreign Trade and International Finance'],
 ARRAY['Tariffs', 'Trade agreements and negotiations', 'Agricultural trade']),

('government', ARRAY['Government Operations and Politics'],
 ARRAY['Congressional oversight', 'Executive agency funding and structure',
       'Government information and archives']),

('families', ARRAY['Families'],
 ARRAY['Child care and development', 'Family services', 'Adoption and foster care']),

('criminal_justice', ARRAY['Crime and Law Enforcement'],
 ARRAY['Crime prevention', 'Law enforcement officers',
       'Law enforcement administration and funding']),

('technology', ARRAY['Science, Technology, Communications'],
 ARRAY['Internet, web applications, social media', 'Telecommunication rates and fees',
       'Advanced technology and technological innovations']),

('disaster_recovery', ARRAY['Emergency Management'],
 ARRAY['Disaster relief and insurance', 'Forests, forestry, trees']),

('poverty', ARRAY['Social Welfare'],
 ARRAY['Poverty and welfare assistance', 'Food assistance and relief']),

('finance', ARRAY['Finance and Financial Sector'],
 ARRAY['Financial services and investments', 'Currency']),

('public_lands', ARRAY['Public Lands and Natural Resources'],
 ARRAY['Forests, forestry, trees', 'Land use and conservation']),

('china', ARRAY['International Affairs', 'Foreign Trade and International Finance'],
 ARRAY['China', 'Trade agreements and negotiations', 'Tariffs'])

ON CONFLICT (topic) DO NOTHING;

-- -- aliases -----------------------------------------------------------------
-- Left column is what the extractor wrote; right column is the topic whose
-- filter answers it. Spelling variants with a space ('gun violence') are real
-- extractor output and are mapped rather than corrected in place, because the
-- promise row records what the run produced.

INSERT INTO topic_vote_filters (topic, canonical_topic) VALUES
    ('climate',               'environment'),
    ('climate_change',        'environment'),
    ('clean_energy',          'energy'),
    ('water',                 'environment'),
    ('oceans',                'environment'),
    ('wildfires',             'disaster_recovery'),
    ('land_use',              'public_lands'),
    ('animals',               'public_lands'),
    ('conservation',          'public_lands'),

    ('defense',               'national_security'),
    ('military',              'national_security'),
    ('foreign_affairs',       'foreign_policy'),

    ('voting_rights',         'democracy'),
    ('campaign_finance',      'democracy'),
    ('oversight',             'government'),
    ('government_efficiency', 'government'),
    ('governance',            'government'),
    ('regulation',            'government'),
    ('constitution',          'democracy'),

    ('border_security',       'immigration'),

    ('seniors',               'social_security'),
    ('medicare',              'social_security'),

    ('jobs',                  'labor'),
    ('workers',               'labor'),
    ('fiscal_policy',         'budget'),
    ('spending',              'budget'),
    ('taxes_and_spending',    'budget'),

    ('childcare',             'families'),
    ('child_care',            'families'),

    ('lgbtq',                 'civil_rights'),
    ('lgbtq_rights',          'civil_rights'),
    ('equality',              'civil_rights'),
    ('womens_rights',         'civil_rights'),
    ('reproductive_rights',   'abortion'),

    ('gun_violence',          'guns'),
    ('gun violence',          'guns'),
    ('gun_safety',            'guns'),

    ('police_reform',         'criminal_justice'),
    ('crime_and_safety',      'criminal_justice'),
    ('justice',               'criminal_justice'),
    ('human_trafficking',     'criminal_justice'),
    ('cannabis',              'law_enforcement'),
    ('drugs',                 'law_enforcement'),

    ('cryptocurrency',        'finance'),
    ('financial_services',    'finance'),
    ('tariffs',               'trade'),
    ('auto industry',         'transportation'),
    ('broadband',             'technology'),
    ('nutrition',             'agriculture'),
    ('farming',               'agriculture'),
    ('rural',                 'agriculture'),

    ('health_care',           'healthcare'),
    ('prescription_drugs',    'healthcare'),
    ('mental_health',         'healthcare'),
    ('public_health',         'healthcare'),
    ('opioids',               'healthcare')
ON CONFLICT (topic) DO NOTHING;
