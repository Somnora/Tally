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

### Naming the money that has no limit on it

A political action committee may give a candidate $5,000 per election, which
is $10,000 across a primary and a general. There is no comparable ceiling on
spending independently for or against that same candidate. The practical
result is that a candidate's largest direct donors are often a row of
identical $10,000 entries, while the decisive money sits in independent
expenditure beside them. Party committees, and transfers from a candidate's
own affiliated committees, follow different rules and can appear in a donor
list for considerably more. Across the candidates we cover,
outside spending exceeds the single largest direct donor for a clear majority
of those who have any.

We therefore name the committees that spend independently, not just the
total. Each candidate shows the largest spenders for and against them, each
one linked to its own FEC record, ranked separately by side so that a lone
committee spending against a candidate is never pushed off the list by a
crowd of supporters. Naming them costs us nothing in accuracy: the spending
committee's identifier is part of the filing, and it covers all but a
fraction of a percent of the outside dollars we hold.

This is a mechanical rule applied to every candidate. We do not decide which
backers are worth mentioning, and we do not annotate any candidate with a
description of who supports them. The list is whoever filed the spending.

### How much of the money we can actually show you

Campaigns report their totals to the FEC in summary form, and separately file
the itemized records behind those totals. We hold all of the summaries and
only part of the itemization, so every candidate shows how much of their
individual contributions we hold as itemized records.

Contributions from committees are close to complete. Itemized contributions
from individuals now stand at roughly four fifths of what the FEC's summaries
report, and the remainder is mostly contributions filed since our last bulk
load. Each candidate shows their own figure rather than this average, because
the spread is wide.

Two things that section gets right only because they were once wrong.

The comparison covers one period. A campaign's official summary runs up to a
filing date, and the itemized file we load runs to whenever we last
downloaded it. Measuring everything we hold against a summary that stops in
March told twenty candidates' readers that we held more of their money than
the campaign had reported raising. We now count only contributions dated
within the period the campaign has actually reported on.

A candidate's own money is not a contribution to them. The FEC records money
a candidate gives their own campaign under its own transaction type and
reports it on its own line; we were adding it to individual contributions,
which made a self-funded campaign look like a campaign with supporters. That
was $27.5 million across 860 candidates in this cycle. It is now shown
separately and labelled as what it is, because on a site about who is backing
a candidate, the answer "they are" is a real answer and not a rounding
detail.

A small number of candidates still show slightly more held than reported,
usually by a few percent. That is contributions filed after the campaign's
last summary but dated inside it, and the card says so rather than rounding
the number down and claiming to be complete.

### Bundling, and why we name it separately

There are three ways money reaches a candidate, and a donor list shows only
one of them.

A committee can give directly, capped at $5,000 per election. A group can
spend independently for or against the candidate, uncapped, which we cover
above. And an organisation can bundle: it asks many individuals to give,
collects their contributions, and delivers them together. Every one of those
gifts is an individual's own and stays within the individual limit, so
nothing about it is irregular, but the organisation that assembled them
directs a total far larger than any single limit and appears nowhere in a
donor list, because it never donated.

The filings identify the collecting committee, so we name it. Each candidate
shows the organisations that bundled contributions to them, how much arrived
that way and across how many gifts, each linked to its FEC record. The count
matters as much as the total: an organisation routing $400,000 across nine
hundred contributions is doing something different from one routing it across
three.

This is stated as a channel, not an allegation. Bundling is lawful, disclosed
and ordinary, and it is treated identically for every candidate. We report it
because a reader asking who is behind a campaign is asking about exactly this,
and answering with only the capped committee donations would be a technically
accurate reply to a question nobody asked.

We measure that gap on individual contributions alone, and it is worth saying
why, because the first version of this measure was wrong. Combining committee
and individual money into one ratio reported that we held more money than
existed for 388 candidates. Two causes: our committee sums include
coordinated party expenditures and in-kind transfers, which the FEC's own
contribution total does not count, and itemized filings routinely post-date
the summary they belong to. A coverage figure above 100 percent discredits
the disclosure it is trying to make. Individual money suffers from neither
distortion, and it is the gap that actually matters, so that is what the
number reports.

This matters for one mechanism in particular. Organizations that bundle
earmarked contributions from many individuals exercise influence through
exactly the channel we have least of, and their role is correspondingly
under-represented in what we can show today. We would rather publish that
limitation next to every donor list than let a short list imply a complete
one.

Contributions below the itemization threshold are excluded from both sides of
this measure, because no itemized record of them exists at the FEC either.
Counting them against ourselves would describe a gap that nobody could close.

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
   dropped 10.6 percent of verified quotes across everything we hold.
5. Promises are labeled by specificity: measurable, directional, or
   rhetorical. Rhetorical statements are shown for context but are never
   scored.

We separate what a member says in their official capacity from what a
campaign says. Official congressional websites are published by a government
office under rules that restrict campaign content; a campaign website is the
candidate asking for your vote. Both are that person stating what they intend
to do, but only one is a campaign promise, and we label them differently
rather than merging them.

### Where a campaign website comes from

We do not search for a candidate's website. We read the address the campaign
gave the Federal Election Commission.

Every committee a candidate authorizes files a Form 1, and that form asks for
the committee's web address. It is the campaign's own statement, made to a
federal regulator, about where its campaign lives. We ask the FEC for each
candidate's authorized committees and take the address from there, matched on
the committee identifier rather than on anyone's name.

The reason is not convenience. The other ways to find a challenger's website
are to search their name or to buy a list from someone who did. A name search
has to decide which Brown is Sherrod Brown and which is Shontel Brown, and a
wrong decision publishes one candidate's words on another candidate's page.
Reading an address the campaign filed itself removes that decision.

Four things we will not do here.

We do not repair a mistyped address. One 2026 filing reads
VONDRASFORCONGRESS,ORG, where the comma is plainly meant to be a period, and
we still record that we have no site rather than invent one the campaign did
not file.

We do not read committees the candidate did not authorize. A political action
committee may file a website too, and publishing that as the candidate's own
would put a spender's words on a candidate's page.

We do not treat a candidate's Facebook page or donation form as a campaign
site. We count them and say so, because there is nothing on them a promise
can be read from, but we do not pretend they are the same thing as a site
with stated positions.

We do not hide it when a page was read over a connection we could not
verify. Some campaigns run misconfigured certificates, which a browser
repairs silently and stricter software does not. Refusing those pages would
have reported live campaigns as silent, and it would have fallen hardest on
campaigns with the cheapest hosting. So we read them, and every page read
that way is stored with a mark saying the certificate was not verified: what
the host served is still evidence, but we cannot promise the host was who it
claimed to be.

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

### What we will not tell you, and why

We publish no finding that a member BROKE a promise. Those findings exist in
our database and every one of them is withheld pending human review, by a
rule enforced in the database itself rather than by anyone remembering.

The reason is specific. An earlier version of this scoring was shown a bill's
title and not what the bill did, and it reasoned backwards from there. A bill
called the "Homeowner Energy Freedom Act" repeals home energy efficiency
rebates; a member voted against it, thereby protecting the rebates he had
promised to protect, and we recorded that he had broken his promise. We found
it in review, withdrew every score we had published, and disabled the
scoring in code so it could not be regenerated by accident.

The rebuild is not a better prompt. The model is no longer asked whether a
vote supports or contradicts a promise, because that is the judgment it was
getting wrong. It is asked only what passing the bill would do, and the
direction of the vote is then arithmetic. Voting down a repeal now counts as
protecting the thing being repealed, by construction rather than by
instruction. Separately, a vote on a contested question that a promise never
raised cannot be scored at all: a promise about working families is not a
position on abortion, guns or immigration, and treating it as one would be a
political judgment we do not make on anyone's behalf.

We then screened the remaining broken-promise findings ourselves, flagging
any that cited a vote AGAINST a bill that would have repealed or narrowed
something, since that is the shape the old error took. About half were
flagged. That is a rough automated screen and not a considered review of each
one, which is precisely the point: we do not yet know which of them are
sound, so none are published, and the findings you do see are limited to
"acting on it" and "completed".

## Update cadence

The app loads a published data snapshot with a version stamp, and the "data
as of" date is always visible in the app. That date, not this section, is the
authoritative answer to how current what you are reading is.

Our cadence is weekly for the parts that can be automated safely, and manual
for the parts that cannot.

Automated, weekly: campaign finance from the FEC, roll-call votes from
Congress.gov, a fresh read of members' official websites, and the rebuild and
republish of the site. This is the data that goes stale, and none of it
requires a judgment call.

Deliberately manual: reading new documents for promises, and assessing
promises against voting records. Both need a rented GPU, so automating them
would mean spending money unattended, and the second produces the one kind of
finding we will not publish without a person reading it. Those runs happen
when someone decides to do them, and the site always shows the date of the
data you are looking at.

## Finding your district, and what happens to your address

The map shows city and town names so you can orient yourself. Those labels
cannot tell you your district, and we are careful not to imply they can:
congressional districts routinely split cities. Five points across Houston
fall in five different districts, so a single label tied to one district would
be wrong for most of the people living there.

The address box is the reliable answer, and here is exactly what it does.

- Your address goes from your browser **directly to the U.S. Census Bureau's
  public geocoder**. It does not pass through us, we never receive it, and we
  store nothing. There is no account, no cookie, and no log of it on our side.
- The Census geocoder returns the congressional district for that address, and
  the page then shows you that district. The lookup happens entirely between
  your browser and a government service.
- If the geocoder cannot match what you typed, the page says so rather than
  guessing. We would rather answer nothing than answer wrongly about where
  somebody lives.
- Washington DC is a specific case worth stating: the geocoder answers with
  its delegate district, but this site covers the 435 voting House seats, so
  DC has no page here. The lookup says that instead of showing you an empty
  one.

The one technical caveat, stated because it is a real tradeoff: to work on a
site with no server of its own, the lookup loads the Census reply as a script
rather than a normal data request. That is a deliberate trust in a US
government domain over HTTPS, and it is the only external service the page
talks to.

## When a member runs for a different seat

The FEC issues a candidate a new identifier for each office they seek, and
nothing in the published data joins the two. A sitting House member running
for the Senate therefore appears twice, and their voting record sits under
the identifier they are no longer campaigning under. Fourteen candidates were
in that position here, and their Senate pages showed sitting members of
Congress with no record and no promises, as though we had never heard of
them.

We join those identities, and the standard for doing so is deliberately
high, because the failure mode is one member's votes appearing on another
member's page. Two independent things must agree.

The FEC's own candidate master has to list both identifiers with a
character-for-character identical filed name, the same state, and the same
party, across two different offices, with exactly one marked as an incumbent.
That is a single source in a single format, self-reported by the candidate.
We do not match names across two different systems, which is guesswork
dressed as evidence, and we do not match on surnames: Ohio has had a Sherrod
Brown and a Shontel Brown at once.

Then our own database has to agree independently, because the FEC's
incumbency marker is self-declared and stale filings carry it. The
incumbent-side identifier must resolve to a member we hold a real roll-call
record for, and the other side must have none.

Anything that fails either test is left alone rather than guessed at. On the
current data that reduced twenty-nine name matches to fourteen links, and a
hundred and five ambiguous groups were never candidates for it. Every link we
make is stored with the evidence for it and the superseded identity is kept,
so any one of them can be checked or undone.

## Coverage, and what is missing

Coverage is uneven, and the app says so per candidate rather than leaving you
to guess. At present:

- Campaign finance covers every federal candidate on file with the FEC for
  this cycle. Reported totals and independent expenditure are complete.
  Itemized individual contribution records behind those totals are mostly
  loaded, unevenly by candidate, and each candidate states how much of
  theirs we hold.
- Voting records cover sitting members of Congress. A challenger who has
  never held federal office has no roll call record to compare against, and
  we show no alignment scoring for them.
- Promise sources now cover candidates, not only sitting members. Of the
  4,079 candidates on file for this cycle, 2,581 declared a campaign web
  address to the FEC, and we have read pages from 1,708 of them. Counting
  every kind of document, we hold material for 1,798 candidates, against 461
  before this pass.
- Reading a page is not the same as publishing a promise, and the app shows
  the difference. Promises appear only after a document has been through
  extraction and both verification checks above, and that step has not yet
  run for the campaign pages just collected. Until it does, those candidates
  show their money and no promises, and the page says so rather than
  implying they have made none.
- Of the addresses that were declared but produced no pages: 576 no longer
  resolve or refuse the connection, 282 serve their text only to a browser
  and not to software like ours, and 18 campaigns ask crawlers not to read
  them, which we honour. Those are counted separately because they mean
  different things, and none of them means the candidate has been quiet.
- Scoring a promise against a voting record still applies only to sitting
  members. A challenger who has never held federal office has no roll call
  to check anything against, so we show their promises without alignment
  scoring rather than inventing a comparison.

Where we have nothing for a candidate, the app says we have nothing. An empty
section means we have not gathered it yet, never that the candidate said or
did nothing.

The map draws that distinction too. A district shaded "collected, not yet
analysed" is one where we hold the candidates' own pages and have not yet run
extraction over them, which is a different statement from having nothing to
show. It reverts to the plainer shading once the material has been read,
whether or not reading it produced a single publishable promise.

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
