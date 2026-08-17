# Methodology

This page explains where our data comes from, how quotes and evaluations are
checked before they appear in the app, and how often everything updates. It is
a first-class part of the product: if you cannot tell how we know something,
you should not have to trust it.

Nothing is displayed in the app before its verification path, described
below, is live. Where a section describes something we intend to do rather
than something we currently do, it says so.

## Our editorial principle: evidence over verdicts

We show receipts: verbatim quotes with links to their source, roll call votes
linked to Congress.gov, donations linked to FEC records. Model-generated
scores are secondary. They always appear together with their reasoning, their
citations, and a link to this page. All candidates are processed by the same
pipeline, with the same prompts, the same scoring rules, and the same display
treatment, regardless of party.

## Data sources and credits

| What | Source |
|---|---|
| Candidates, committees, contributions, independent expenditures | Federal Election Commission bulk data and the OpenFEC API |
| Bills, roll call votes, member records | Congress.gov API (Library of Congress) |
| Lobbying filings | U.S. Senate Lobbying Disclosure Act database |
| Member ID crosswalk | the unitedstates/congress-legislators open data project |
| Donor industry classification | OpenSecrets (opensecrets.org), used under their bulk data license. We thank OpenSecrets for making this work possible. |
| Ideology scores | Voteview (voteview.com), DW-NOMINATE |
| Candidate statements | Official congressional websites (house.gov, senate.gov), campaign websites, press releases, and public video, with archived snapshots from the Internet Archive Wayback Machine |
| District lookup | U.S. Census Bureau geocoder and TIGER shapefiles |

Every fact stored in our database traces back to a recorded retrieval: the
source URL, the retrieval time, and a cryptographic hash of the raw payload.
Deep links in the app point to the official record (fec.gov, congress.gov)
wherever one exists.

## How money data is assembled

Campaign finance figures come from two independent FEC channels, and we
show our work by keeping both:

1. **Itemized records** from FEC bulk data: every contribution from a
   committee to a candidate, every itemized individual contribution, and
   every independent expenditure. Each record keeps the FEC image number,
   which links to the actual scanned filing.
2. **Official totals** from the FEC API: the FEC's own per-candidate
   aggregates for the cycle.

Comparing our itemized sums against the official totals is a permanent
accuracy check. Divergence beyond normal bulk-processing lag is
investigated before the affected numbers ship.

Accounting rules applied to itemized records:

- Memo-flagged rows (informational detail that would double-count money,
  such as conduit earmark breakdowns) are excluded from all sums.
- Contribution refunds are never counted as receipts; they are tracked in
  their own column.
- Independent expenditures are money spent about a candidate, not given to
  them. They are reported separately as supporting or opposing, never mixed
  into contribution totals.
- Amended filings replace earlier versions of the same record rather than
  being counted twice.

## How voting records are collected

Roll-call votes come directly from each chamber's official record: House
votes from the Congress.gov API (which mirrors the House Clerk's records)
and Senate votes from the Senate's own published roll-call XML. For every
vote we display, you can click through to the official government page for
that roll call.

- A member's position is stored exactly as the chamber recorded it,
  normalized only across vocabulary (the House says "Aye", the Senate says
  "Yea"; both mean yes).
- Impeachment votes are kept as Guilty or Not Guilty, never converted to
  yes or no.
- Procedural events that are not positions, such as Speaker elections where
  the recorded vote is a candidate's name, are excluded rather than
  reinterpreted.
- Each vote shows the chamber's outcome alongside the member's position, so
  you can see whether the member was in the majority.

## How promises are verified

A promise appears in the app only after it passes two independent checks:
the quote has to be real, and the statement has to be a promise.

1. We collect source documents: official congressional websites, campaign
   websites, press releases, transcripts of town halls and interviews, and
   archived snapshots of pages that have since changed.
2. A language model reads each document and proposes promise quotes with
   their positions in the text.
3. **Is the quote real?** Our code checks, character for character, that the
   quoted text actually appears in the source document. Quotes that match at
   a slightly different position are corrected. Quotes that do not appear
   anywhere in the document are rejected and never shown, and we log every
   rejection.
4. **Is it actually a promise?** A model asked to find promises will also
   return things that are not promises: a belief ("I believe every family
   deserves affordable housing"), a biography, a reason for running, or a
   bullet point lifted out of a list ("reforming the tax code"), which names
   a subject without committing anyone to anything. A set of written rules,
   not a model, screens every verified quote and drops those. The rules are
   published in our repository, they are versioned, and each stored quote
   records which version judged it and why. In our most recent run they
   dropped 13 percent of verified quotes.
5. Promises are labeled by specificity: measurable, directional, or
   rhetorical. Rhetorical statements are shown for context but are never
   scored.

We separate what a member says in their official capacity from what a
campaign says. Official congressional websites are published by a government
office under rules that restrict campaign content; a campaign website is the
candidate asking for your vote. Both are that person stating what they intend
to do, but only one is a campaign promise, and we label them differently
rather than merging them.

Two limits worth stating plainly. First, we measured the screening rules
against a hand reviewed set of 118 quotes drawn from video transcripts, and
they are less well measured on written material, which currently supplies
most of what we hold. Second, dropping a quote is not a claim that the
candidate never made the underlying commitment; it means this particular
quote is not usable as evidence of one.

## How evaluations are validated

For promises that are specific enough to check, we compare the promise with
the incumbent's roll call voting record. Evaluations currently cite votes
only. Campaign finance is displayed alongside a candidate, and you can read
both on the same page, but we do not generate scored claims about a donation
having influenced a vote, and no evaluation cites a donation as evidence.

1. The model receives only verified promises and a pre-summarized list of
   that member's votes on the promise's subject, each carrying a database
   identifier.
2. Any evaluation it returns must cite those identifiers as evidence.
3. Our code independently validates every citation: the cited record must
   exist, it must belong to this member, it must have been one of the votes
   we actually showed the model, and the member's recorded position must be
   the one the citation claims. A citation that fails any of these is
   rejected, and we keep the rejected citation rather than discarding it.
4. Some votes cannot carry a clean verdict, and we mark them rather than
   letting them stand as proof. A vote on a large bill bundling many
   unrelated provisions, and a vote on procedure rather than on policy, are
   both usable as context only, never as evidence that a promise was kept
   or broken.
5. An evaluation with any unvalidated citation is excluded from the app by
   construction. So is one whose conclusion is not supported by whatever
   evidence survived validation. Evaluations are never edited in place; a
   new model or prompt version produces a new evaluation, and the app shows
   which version produced what you see.
6. Where a member has no votes on a promise's subject, we record that and
   show no score. An unscored promise means we found nothing to check it
   against, not that the member failed to act.
7. Model settings are pinned and deterministic, and every prompt version is
   tracked in our public repository.

Scores are the least important thing on the page, and they are the part most
likely to be wrong. They exist to order and summarize evidence you can read
yourself. Where the score and the underlying votes disagree, believe the
votes, and please tell us.

## Update cadence

The app loads a published data snapshot with a version stamp, and the "data
as of" date is always visible in the app. That date, not this section, is the
authoritative answer to how current what you are reading is.

Our intended cadence is weekly: campaign finance following the FEC bulk data
publication schedule, and votes and documents once per candidate per week
during the cycle. We are not there yet. Updates currently run when we run
them, which is why the snapshot date is displayed rather than a promise of
freshness. This section will say "automated, weekly" when that is true and
not before.

## Coverage, and what is missing

Coverage is uneven, and the app says so per candidate rather than leaving you
to guess. At present:

- Campaign finance covers every federal candidate on file with the FEC for
  this cycle.
- Voting records cover sitting members of Congress. A challenger who has
  never held federal office has no roll call record to compare against, and
  we show no alignment scoring for them.
- Promises are collected for sitting members first, because they are the
  only candidates whose statements can be checked against votes.

Where we have nothing for a candidate, the app says we have nothing. An empty
section means we have not gathered it yet, never that the candidate said or
did nothing.

## Corrections

If something here is wrong, we want to fix it and say that we fixed it.

Open an issue at github.com/Somnora/Tally with the candidate, the item, and
what you believe is incorrect. Quotes and votes carry deep links to the
official record, so the fastest correction usually cites that record
directly.

Corrections to underlying data are made by re-running the pipeline against
the corrected source, not by hand editing a stored fact, so the provenance
trail stays intact. Evaluations are never edited in place: a corrected or
re-scored evaluation is stored as a new version, and the record of what was
previously shown is retained.
