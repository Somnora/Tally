# Extraction Pipeline v2 Failure Taxonomy & Exclusion Rules

## Executive Summary

An evaluation of **118 extracted candidate promises** in `data/review/extract_v2_promises.jsonl` identified **47 genuine extraction errors** (verdicts of `opinion`, `not_a_promise`, or `fragment`), confirming a ~60.2% extraction precision rate for prompt version `extract_v2`. 

The primary driver of precision degradation is the extraction of **spoken interview transcripts (`youtube_transcript`)**, where conversational hedges, personal opinions, bio statements, and normative preferences were misclassified as binding campaign promises.

To raise extraction precision in version `extract_v3`, this taxonomy categorizes all 47 extraction leaks into **9 distinct failure patterns**, evaluates them against the existing v2 exclusion list (*past actions*, *present-progressive*, *procedural*, *demands on others*, *pleasantries*), and proposes imperative exclusion rules to close prompt gaps.

---

## Failure Pattern Taxonomy

### 1. Hedged Opinions & Personal Value Stances

- **Definition**: Statements where the candidate expresses a personal belief, ideological preference, policy evaluation, or hedged opinion rather than committing to a specific future governing action.
- **Coverage Status**: **NEW GAP (v3 Priority)** — Existing exclusions do not filter out epistemic opinion hedges or belief statements.
- **Count**: **26 promises**
- **Linguistic Trigger Phrases**: `"I think"`, `"Chellie believes"`, `"I believe"`, `"I don't think"`, `"I certainly think"`, `"I do not believe"`, `"I do think"`, `"I think I do support"`, `"seem to make sense to me"`, `"I would call it"`.
- **Verbatim Examples**:
  - **ID 240**: *"Chellie believes these consumer protections are non-negotiable and should be the foundation of our health care system going forward."*
  - **ID 234**: *"I think that's exactly the way it should be."*
  - **ID 219**: *"members of Congress, I don't think should be allowed to trade stocks while in office, and I think whether that's a something that goes into a blindly managed fund or a public sell-off, I think we do need some some real reform in Congress."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract statements where policy support or evaluation is framed as a personal belief, opinion, or stance using epistemic markers like "I think", "I believe", "[Candidate] believes", or "I don't think" unless accompanied by an explicit pledge of future action.

---

### 2. Normative Desiderata & Policy Rationale Statements

- **Definition**: General claims asserting systemic needs, policy importance, or ideal societal conditions without expressing an explicit candidate pledge or concrete action plan.
- **Coverage Status**: **NEW GAP (v3 Priority)** — Impersonal rationale claims are not captured by existing rules.
- **Count**: **3 promises**
- **Linguistic Trigger Phrases**: `"is critical"`, `"it's past time to"`, `"should not be dictated by"`, `"I really appreciate your idea"`.
- **Verbatim Examples**:
  - **ID 244**: *"Increasing access to treatment is critical to reaching people with substance use disorder, many of whom don’t have the means to afford private programs."*
  - **ID 178**: *"It's past time to move towards a Medicare-for-all system that puts people’s health and financial well-being over private profits for health insurance companies and executives."*
  - **ID 274**: *"And that's another huge issue that I'm concerned about is as all those costs rise... I really appreciate your idea of how can we make sure uh that there are those opportunities there..."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract impersonal rationale claims or normative statements asserting what is "critical", "past time", or "how things should be" unless the candidate explicitly commits themselves to enacting or executing that specific policy.

---

### 3. Aspirational Hopes & Exploratory Intentions

- **Definition**: Declarations of tentative desire, hope, or exploratory interest rather than definitive, unconditional governing commitments.
- **Coverage Status**: **NEW GAP (v3 Priority)** — Tentative hopes and exploratory plans fall outside present-progressive or procedural exclusions.
- **Count**: **3 promises**
- **Linguistic Trigger Phrases**: `"we're hoping to"`, `"I'm hoping to"`, `"We're looking at"`, `"hoping to do"`.
- **Verbatim Examples**:
  - **ID 199**: *"I'm hoping to in '26 get even more involved in it and help any way we can."*
  - **ID 201**: *"We're looking at putting together a program that we call the MITT program, MITT, which is a master illegal trafficking tracker, which will provide intel..."*
  - **ID 202**: *"we're hoping to get that launched early in '26."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract tentative desires, hopes, or exploratory planning signaled by phrases like "hoping to", "we're hoping", or "we're looking at" as candidate promises.

---

### 4. Past Actions & Historical Counterfactuals

- **Definition**: Statements describing completed prior legislative votes, past decisions, or hypothetical past choices rather than forward-looking commitments.
- **Coverage Status**: **COVERED** — Matches the existing "past actions" exclusion rule, but prompt enforcement requires tightening for counterfactuals.
- **Count**: **2 promises**
- **Linguistic Trigger Phrases**: `"I did that because"`, `"I would have actually renewed"`, `"I voted for"`.
- **Verbatim Examples**:
  - **ID 262**: *"I DID THAT BECAUSE I DIDN'T FEEL THAT MEMBERS OF CONGRESS, IF THEY ARE NOT ABLE TO RESOLVE THE SITUATION... WE SHOULD NOT RECEIVE OUR PAY EITHER..."*
  - **ID 227**: *"I would have actually renewed them as a bridge."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract past legislative actions, prior votes, or hypothetical past choices ("I would have") as future commitments.

---

### 5. Campaign Bio & Candidate Running Aspirations

- **Definition**: Declarations of candidate credentials, professional background, or general motivations for seeking office rather than specific post-election governing commitments.
- **Coverage Status**: **NEW GAP (v3 Priority)** — Biographical statements and campaign running motivations are unaddressed in v2 prompt rules.
- **Count**: **3 promises**
- **Linguistic Trigger Phrases**: `"Matt is running to"`, `"I want to serve in Congress to"`, `"I am an Airborne Ranger... looking to continue"`.
- **Verbatim Examples**:
  - **ID 157**: *"Matt is running to take on the powerful interests that have rigged our economy against working class Mainers — because it's time Washington worked for us, not Wall Street."*
  - **ID 210**: *"I want to serve in Congress to bend the arc of our democracy back to the aspirational goal of building a better future for our families."*
  - **ID 154**: *"I am an Airborne Ranger Green Beret, who is looking to continue that service as your representative to the United States Congress."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract candidate biographical background, military/professional credentials, or general candidate motivations for seeking office ("I am running to", "I want to serve to") as policy promises.

---

### 6. Constituent Casework, Event Logistics & Support Offers

- **Definition**: Offers of constituent office assistance, helpline invitations, or local event planning details rather than public policy promises.
- **Coverage Status**: **COVERED** — Overlaps with existing "procedural" and "pleasantries" exclusion rules.
- **Count**: **2 promises**
- **Linguistic Trigger Phrases**: `"call our office and we will do our best"`, `"hoping to do a big event"`.
- **Verbatim Examples**:
  - **ID 264**: *"IF ANY OF YOU HAVE CONCERNS, CALL OUR OFFICE AND WE WILL DO OUR BEST TO LOOK AT PEOPLE TO GIVE HIM THE DUE PROCESS, PUSHBACK WITH ICE, AND THE BORDER PATROL..."*
  - **ID 200**: *"I'm hoping to do a big event sometime early in the year to get as many people that are involved in law enforcement, immigration enforcement, bring them into Maine..."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract constituent casework assistance offers ("call our office"), helpline invitations, or campaign event logistics as governing policy promises.

---

### 7. Ongoing Present Activity & Status Reports

- **Definition**: Descriptions of current congressional efforts, ongoing negotiations, or existing leadership roles rather than new future commitments.
- **Coverage Status**: **COVERED** — Covered under existing "present-progressive ('we're working on')" exclusion rule.
- **Count**: **2 promises**
- **Linguistic Trigger Phrases**: `"Chellie is leading the fight"`, `"part of our negotiation is"`.
- **Verbatim Examples**:
  - **ID 249**: *"Chellie is leading the fight in Congress to face this crisis head-on with bold, decisive action."*
  - **ID 275**: *"Now, these cuts could be rolled back by Congress. And so, part of our negotiation is uh dealing with the subsidies under the Affordable Care Act..."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract descriptions of ongoing legislative negotiations, active committee work, or current leadership roles ("is leading", "part of our negotiation is") as future promises.

---

### 8. Third-Party Speech, Opponent Attacks & Media Overlays

- **Definition**: Statements spoken by political opponents, debate moderators, news broadcast overlays, or reporter summaries rather than direct pledges by the target candidate.
- **Coverage Status**: **COVERED** — Overlaps with existing "demands on others" rule, though broadcast captions require explicit prompt guidance.
- **Count**: **3 promises**
- **Linguistic Trigger Phrases**: `"RUSSELL SAYS HE'LL"`, `"We need a senator who... who she voted to confirm"`, `"We're going to beat [Candidate]"`.
- **Verbatim Examples**:
  - **ID 252**: *"RUSSELL SAYS HE'LL SUPPORT THE POLICIES OF PRESIDENT TRUMP TO TACKLE THE AFFORDABILITY CRISIS"*
  - **ID 258**: *"We need, we need a senator who has the courage to say no to an HHS secretary like RFK Junior, who she voted to confirm."*
  - **ID 259**: *"We're going to beat Susan Collins in November, and I am running because I'm the best candidate to win, to unite a fractured party..."*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract quotes spoken by political opponents, debate moderators, or news broadcast graphics ("SAYS HE WILL") that describe or attack a candidate.

---

### 9. Truncated Text & Syntactic Fragments

- **Definition**: Text extractions that end abruptly mid-word, are cut off mid-sentence, or consist of thin disfluent repetitions lacking complete policy meaning.
- **Coverage Status**: **NEW GAP (v3 Priority)** — Grammatical completeness is unaddressed in existing rules.
- **Count**: **3 promises**
- **Linguistic Trigger Phrases**: Mid-word cutoffs (`"semi-autom"`, `"gaming the m"`), or disfluent repetitions (`"we'll we'll fight back"`).
- **Verbatim Examples**:
  - **ID 250**: *"That’s why Chellie favors common sense gun safety measures to keep weapons of war out of the hands of dangerous people. That includes a ban on bumpstocks, which essentially transform semi-autom"*
  - **ID 221**: *"I don't think they need to be gaming the m"*
  - **ID 268**: *"we'll we'll fight back"*
- **Proposed Imperative Exclusion Rule**:
  > DO NOT extract incomplete sentence fragments, quotes cut off mid-word, or thin disfluent phrasing that fails to form a complete grammatical proposition.

---

## Gap Summary Table for Prompt Version v3

| Failure Pattern Name | Promise Count | Existing Exclusion Status | Priority for v3 Prompt |
| :--- | :---: | :--- | :--- |
| **1. Hedged Opinions & Personal Value Stances** | **26** | **NEW GAP** | **High** (55.3% of all leaks) |
| **2. Normative Desiderata & Rationale Statements** | **3** | **NEW GAP** | Medium |
| **3. Aspirational Hopes & Exploratory Intentions** | **3** | **NEW GAP** | Medium |
| **4. Past Actions & Historical Counterfactuals** | **2** | COVERED | Low (Rule Maintenance) |
| **5. Campaign Bio & Running Aspirations** | **3** | **NEW GAP** | Medium |
| **6. Constituent Casework & Event Logistics** | **2** | COVERED | Low (Rule Maintenance) |
| **7. Ongoing Present Activity & Status Reports** | **2** | COVERED | Low (Rule Maintenance) |
| **8. Third-Party Speech & Media Overlays** | **3** | COVERED | Low (Rule Maintenance) |
| **9. Truncated Text & Syntactic Fragments** | **3** | **NEW GAP** | Medium |
| **Total Genuine Leaks** | **47** | **5 New Gaps (38 rows)** | |
