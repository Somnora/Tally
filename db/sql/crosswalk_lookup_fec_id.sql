SELECT bioguide_id, full_name
FROM id_crosswalk
WHERE %(fec_candidate_id)s = ANY (fec_candidate_ids)
