-- What happened when we asked. Written by the harvest pass so coverage can be
-- reported from the same table that holds the claim.
UPDATE candidate_websites
   SET last_checked_at = now(),
       fetch_outcome   = %(fetch_outcome)s
 WHERE candidate_website_id = %(candidate_website_id)s
