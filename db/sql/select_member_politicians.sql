-- bioguide + LIS ids to politician_id, for attributing vote positions.
SELECT x.bioguide_id, x.lis_id, p.politician_id
FROM id_crosswalk x
JOIN politicians p ON p.bioguide_id = x.bioguide_id
