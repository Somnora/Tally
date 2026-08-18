-- Everything needed to judge one candidate-identity pair, from our own data
-- rather than from the FEC file that proposed it. The FEC's incumbency flag
-- is candidate-declared and stale rows carry it, so a link also has to be
-- corroborated here: real roll-call votes on the incumbent side, none on the
-- other, and a bioguide id proving the incumbent side is a known member.
SELECT inc.politician_id                                        AS incumbent_politician_id,
       oth.politician_id                                        AS other_politician_id,
       inc_p.bioguide_id                                        AS incumbent_bioguide,
       (SELECT count(*) FROM voting_records v
         WHERE v.politician_id = inc.politician_id)             AS incumbent_votes,
       (SELECT count(*) FROM voting_records v
         WHERE v.politician_id = oth.politician_id)             AS other_votes,
       EXISTS (SELECT 1 FROM candidate_identity_links l
                WHERE l.other_fec_id = %(other_fec_id)s)        AS already_linked
FROM (SELECT politician_id FROM candidacies
       WHERE fec_candidate_id = %(incumbent_fec_id)s LIMIT 1) inc
CROSS JOIN (SELECT c.politician_id FROM candidacies c JOIN races r USING (race_id)
             WHERE c.fec_candidate_id = %(other_fec_id)s AND r.cycle = %(cycle)s
             LIMIT 1) oth
JOIN politicians inc_p ON inc_p.politician_id = inc.politician_id
