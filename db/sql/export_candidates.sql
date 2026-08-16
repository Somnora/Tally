-- Who is running, joined through the export view so the snapshot can never
-- contain a candidacy the view would have withheld.
SELECT candidacy_id, race_id, politician_id, party, incumbent_challenger,
       state, office, district, full_name, bioguide_id
FROM app_export_candidacies
WHERE cycle = %(cycle)s
ORDER BY state, office, district, full_name
