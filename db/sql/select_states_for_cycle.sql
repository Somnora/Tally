-- Every state with a race in the cycle, for national runs. Driven off races
-- rather than a hardcoded list so a cycle with territories or a special
-- election picks them up without a code change.
SELECT DISTINCT state
FROM races
WHERE cycle = %(cycle)s
ORDER BY state
