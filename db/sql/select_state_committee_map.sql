-- Committee -> candidate map for one state's candidates, or every state when
-- %(state)s is 'ALL'. Used to decide which rows of the individual
-- contributions bulk file belong to which candidate.
--
-- AUTHORIZED committees only: designation P (principal campaign committee)
-- and A (other authorized committee). This restriction is the whole
-- correctness of the map, and it is not obvious, so:
--
-- committees.cand_id is populated in the FEC master for committees that are
-- merely ASSOCIATED with a candidate, not only those that raise money as
-- them. The NRSC carries a Senate candidate's id in that column. Joining on
-- cand_id alone therefore attributed all 755,501 individual contributions to
-- the NRSC, $51.5 million of them, to Dan Sullivan personally, whose own
-- committee raised $2.3 million. Joint fundraising committees (J), leadership
-- PACs (D) and registrant PACs (B) misattribute the same way, and because
-- party and leadership committees are the large ones, the error landed
-- hardest on leadership figures: Scalise, Pelosi, Emmer, Hudson, Daines. A
-- funding figure inflated twentyfold for one side's leadership is not a
-- rounding error, it is a false claim about named people.
--
-- The FEC's own per-candidate totals count authorized committees only, which
-- is why coverage measured against those totals is what caught this.
SELECT cm.cmte_id, c.fec_candidate_id
FROM committees cm
JOIN candidacies c ON cm.cand_id = c.fec_candidate_id
JOIN races r       USING (race_id)
WHERE (%(state)s = 'ALL' OR r.state = %(state)s)
  AND r.cycle = %(cycle)s
  AND cm.cmte_designation IN ('P', 'A')
UNION
-- Fallback only where the FEC has published no linkage of its own. A member
-- who runs for a different seat keeps their committee and redesignates it,
-- so the committee is the principal committee of BOTH candidacies while the
-- FEC's cand_id names the live one. Trusting principal_cmte_id alongside it
-- made one committee map to two candidates, and since the map is a dict, one
-- of them silently won: Chris Pappas's $5.6 million landed on the House seat
-- he is not running for while his Senate campaign, which the FEC credits
-- with $7.5 million, showed nothing. 40 live candidacies were reading zero
-- for this reason.
SELECT c.principal_cmte_id, c.fec_candidate_id
FROM candidacies c
JOIN races r USING (race_id)
LEFT JOIN committees cm ON cm.cmte_id = c.principal_cmte_id
WHERE (%(state)s = 'ALL' OR r.state = %(state)s) AND r.cycle = %(cycle)s
  AND c.principal_cmte_id IS NOT NULL
  AND cm.cand_id IS NULL
