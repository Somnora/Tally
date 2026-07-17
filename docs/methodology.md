# Methodology

This page explains where our data comes from, how quotes and evaluations are
checked before they appear in the app, and how often everything updates. It is
a first-class part of the product: if you cannot tell how we know something,
you should not have to trust it.

Status: skeleton. Each section will be expanded as the corresponding pipeline
stage ships. Nothing is displayed in the app before its verification path,
described below, is live.

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
| Candidate statements | Campaign websites, press releases, and public video, with archived snapshots from the Internet Archive Wayback Machine |
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

## How promises are verified

A promise only appears in the app when its quote passed an exact text match.

1. We collect source documents: transcripts of town halls and interviews,
   press releases, and campaign site snapshots.
2. A language model reads each document and proposes promise quotes with
   their positions in the text.
3. Our code then checks, character for character, that the quoted text
   actually appears in the source document. Quotes that match at a slightly
   different position are corrected. Quotes that do not appear anywhere in
   the document are rejected and never shown, and we log every rejection.
4. Promises are labeled by specificity: measurable, directional, or
   rhetorical. Rhetorical statements are shown for context but are never
   scored.

## How evaluations are validated

For promises that are specific enough to check, we compare the promise with
the incumbent's voting record and reported campaign finance data.

1. The model receives only verified promises and pre-summarized official
   records, each carrying a database identifier.
2. Any evaluation it returns must cite those identifiers as evidence.
3. Our code independently validates every citation: the cited record must
   exist, and it must actually relate to the claim in the stated way.
4. An evaluation with any unvalidated citation is excluded from the app by
   construction. Evaluations are never edited in place; a new model or
   prompt version produces a new evaluation, and the app shows which
   version produced what you see.
5. Model settings are pinned and deterministic, and every prompt version is
   tracked in our public repository.

## Update cadence

- Campaign finance data: weekly, following the FEC bulk data publication
  schedule.
- Votes and documents: weekly per candidate during the cycle.
- The app loads a published data snapshot with a version stamp; the "data
  as of" date is always visible in the app.

## Corrections

(To be expanded: how to report an error, and our correction policy.)
