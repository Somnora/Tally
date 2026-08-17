-- 0017: the alias tail the national extraction produced.
--
-- 0016 mapped the vocabulary of a 1,848 promise corpus. Extraction then
-- finished the remaining 1,104 documents and the corpus reached 3,373, which
-- brought a fresh tail of labels for subjects already covered: 'unions' and
-- 'labor rights' and 'workers_rights' all want the labor filter, 'aviation'
-- wants transportation, 'appropriations' wants budget.
--
-- This is what an open topic vocabulary costs, and it is still the right
-- trade. The model is not handed a fixed list, because a closed one pushes it
-- to file a promise under whichever bucket is nearest rather than name the
-- subject, and a mislabelled promise is compared against the wrong votes.
-- Naming is cheap to correct here in git; a wrong comparison is not.
--
-- Coverage before: 3,079 of 3,373 (91.3%). Everything below is an alias of a
-- filter that already exists; no new canonical rows, because the tail brought
-- no new subject matter, only new words for old subjects.
--
-- Deliberately NOT mapped: 'general', 'values', 'america_first',
-- 'representation', 'safety', 'funding', 'youth', 'religion', 'arts',
-- 'sports'. These name no policy area a roll call could be filtered by, and
-- inventing a mapping for them would manufacture comparisons rather than find
-- them. A promise under one of those records 'pending' and shows no score,
-- which is the honest outcome.
--
-- Spelling variants with a space ('criminal justice', 'labor rights') are
-- real extractor output and are mapped rather than corrected in the promises
-- table, because the promise row records what the run actually produced.

INSERT INTO topic_vote_filters (topic, canonical_topic) VALUES
    -- labor
    ('unions',                   'labor'),
    ('labor unions',             'labor'),
    ('labor rights',             'labor'),
    ('labor_rights',             'labor'),
    ('workers_rights',           'labor'),
    ('worker safety',            'labor'),
    ('workplace_safety',         'labor'),
    ('paid_leave',               'labor'),
    ('minimum_wage',             'labor'),
    ('wages',                    'labor'),
    ('employment',               'labor'),
    ('workforce',                'labor'),
    ('working_families',         'labor'),
    ('federal_workers',          'labor'),

    -- transportation and infrastructure
    ('aviation',                 'transportation'),
    ('shipping',                 'transportation'),

    -- government and its budget
    ('regulations',              'government'),
    ('government_reform',        'government'),
    ('government_transparency',  'government'),
    ('transparency',             'government'),
    ('waste_fraud_abuse',        'government'),
    ('appropriations',           'budget'),
    ('government_spending',      'budget'),
    ('debt_deficit',             'budget'),

    -- democracy
    ('voting',                   'democracy'),
    ('elections',                'democracy'),

    -- crime and justice
    ('crime',                    'criminal_justice'),
    ('criminal justice',         'criminal_justice'),
    ('violence_against_women',   'criminal_justice'),

    -- health
    ('health',                   'healthcare'),
    ('cancer',                   'healthcare'),
    ('reproductive rights',      'abortion'),
    ('reproductive_choice',      'abortion'),

    -- civil rights
    ('gender_equality',          'civil_rights'),
    ('antisemitism',             'civil_rights'),
    ('language_access',          'civil_rights'),

    -- national security and foreign affairs
    ('armed_services',           'national_security'),
    ('homeland_security',        'national_security'),
    ('nuclear_disarmament',      'foreign_policy'),
    ('war_powers',               'foreign_policy'),

    -- land, environment, agriculture
    ('forestry',                 'public_lands'),
    ('federal_lands',            'public_lands'),
    ('animal_rights',            'public_lands'),
    ('environmental justice',    'environment'),
    ('rural_development',        'agriculture'),
    ('food',                     'agriculture'),
    ('food nutrition',           'agriculture'),

    -- economy and finance
    ('cost_of_living',           'economy'),
    ('corporate_accountability', 'finance'),
    ('small_businesses',         'small_business'),

    -- other
    ('border',                   'immigration'),
    ('student_debt',             'education'),
    ('privacy',                  'technology'),
    ('disaster_preparedness',    'disaster_recovery'),
    ('government_service',       'government')
ON CONFLICT (topic) DO NOTHING;
