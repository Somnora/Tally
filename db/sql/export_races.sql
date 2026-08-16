-- Every race in the cycle. Small and complete: the app needs the full list to
-- resolve an address to a district even where no candidate data exists yet.
SELECT race_id, cycle, state, office, district, senate_class, is_special
FROM races
WHERE cycle = %(cycle)s
ORDER BY state, office, district
