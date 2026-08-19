const DATA = window.__TALLY__;

/* Bulk tables arrive column-wise to keep the download small; rehydrate them
   into plain objects once, so every render function below reads normally. */
for(const name of ['candidates','finance','donors','spenders','conduits','votes','topics','record','collected']){
  const t = DATA[name];
  if(t && !Array.isArray(t)){
    DATA[name] = t.rows.map(r => {
      const o = {};
      t.cols.forEach((c,i) => { o[c] = r[i]; });
      return o;
    });
  }
}
const ROLE = {I:'Incumbent', C:'Challenger', O:'Open seat'};
const OFFICE = {house:'US House', senate:'US Senate'};
// What kind of document a quote came from. A reader is entitled to know
// whether they are reading a congressional office publishing under rules that
// restrict campaign content, or a candidate asking for their vote. Most
// harvested pages carry no title of their own, so without this the source line
// read 'campaign_site'.
const DOCKIND = {
  official_site:'Official congressional website', campaign_site:'Campaign website',
  press_release:'Press release', youtube_transcript:'Video transcript',
  wayback_snapshot:'Archived page', debate_transcript:'Debate transcript',
};
const docKind = t => DOCKIND[t] || 'Document';
const usd = n => (n==null||n==='') ? '--' : '$' + Number(n).toLocaleString('en-US');
const esc = s => String(s??'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const byId = (arr,k,v) => arr.filter(r => String(r[k])===String(v));
const financeFor = c => DATA.finance.find(f => f.candidacy_id===c.candidacy_id) || {};
const promisesFor = c => byId(DATA.promises,'politician_id',c.politician_id);
const evalFor = pid => DATA.evaluations.find(e => e.promise_id===pid);
const receiptsFor = eid => byId(DATA.evidence,'evaluation_id',eid);
const promiseVotesFor = pid => byId(DATA.promise_votes,'promise_id',pid);
// Bill text lives once in DATA.bills, not once per promise-vote row.
const billOf = v => (DATA.bills && DATA.bills[v.bill_key]) || {};
const recordFor = c => (DATA.record||[]).find(r => r.politician_id===c.politician_id);
const topicsFor = c => byId(DATA.topics||[],'politician_id',c.politician_id);
const votesFor  = c => byId(DATA.votes||[],'politician_id',c.politician_id);

// Districts that have had documents read and promises extracted. Everywhere
// else shows money only, and says so rather than looking empty.
const RESEARCHED = new Set(DATA.promises.map(p => p.politician_id));
const hasResearch = c => RESEARCHED.has(c.politician_id);

// Candidates whose documents we hold but have not run extraction over. This
// is a different fact from having nothing, and the map used to draw both the
// same shade: a district where nobody has spoken looked identical to one
// where nobody has listened. Only counts as pending while something is
// genuinely unread, so the label stays true after extraction runs.
const PENDING = new Set((DATA.collected||[]).filter(r => r.pending > 0)
  .map(r => r.politician_id));
const isPending = c => !RESEARCHED.has(c.politician_id) && PENDING.has(c.politician_id);

const STATES = [...new Set(DATA.candidates.map(c => c.state))].sort();
const seatKey = c => `${c.office}|${c.district ?? ''}`;
const seatLabel = c => c.office === 'senate'
  ? 'US Senate' : `District ${String(c.district ?? '').replace(/^0+/,'') || 'at large'}`;

let curState = STATES.includes('ME') ? 'ME' : STATES[0];
let curSeat = null;
let curCandidate = null;
let curFilter = 'all';
// 'nation' shows every state; 'state' zooms to one state's districts.
let mapLevel = 'nation';

function seatsIn(state){
  const seats = new Map();
  DATA.candidates.filter(c => c.state===state).forEach(c => {
    const k = seatKey(c);
    if(!seats.has(k)) seats.set(k, []);
    seats.get(k).push(c);
  });
  return [...seats.entries()].sort((a,b) => a[0].localeCompare(b[0], undefined, {numeric:true}));
}

function renderNav(){
  document.getElementById('stateSel').innerHTML =
    STATES.map(s => `<option value="${s}" ${s===curState?'selected':''}>${s}</option>`).join('');
  const seats = seatsIn(curState);
  if(curSeat===null || !seats.some(([k]) => k===curSeat)) curSeat = seats.length ? seats[0][0] : null;
  document.getElementById('tabs').innerHTML = seats.map(([k, cs]) => {
    const researched = cs.some(hasResearch);
    return `<button class="tab" role="tab" aria-selected="${k===curSeat}" data-seat="${esc(k)}">
      ${esc(seatLabel(cs[0]))}${researched?'<span class="dot" title="promises researched"></span>':''}
    </button>`;
  }).join('');
}

function renderRace(){
  const seats = seatsIn(curState);
  const entry = seats.find(([k]) => k===curSeat);
  const cs = entry ? entry[1] : [];
  const researched = cs.filter(hasResearch).length;
  document.getElementById('raceHead').innerHTML = cs.length ? `
    <div class="race-name">${esc(curState)} &middot; ${esc(seatLabel(cs[0]))}</div>
    <div class="race-meta">${esc(OFFICE[cs[0].office]||cs[0].office)} &middot; 2026 cycle &middot;
      ${cs.length} candidate${cs.length===1?'':'s'} with FEC filings &middot;
      ${researched ? `${researched} researched for promises` : 'promises not yet researched here'}</div>` : '';

  document.getElementById('grid').innerHTML = cs.map(c => {
    const f = financeFor(c), ps = promisesFor(c);
    const raised = Number(f.total_receipts||0), pac = Number(f.pac_contributions_official||0);
    const pacPct = raised ? Math.round(pac/raised*100) : 0;
    const donors = byId(DATA.donors,'candidacy_id',c.candidacy_id)
      .sort((a,b)=>a.donor_rank-b.donor_rank).slice(0,3);
    const nWithVotes = ps.filter(p => promiseVotesFor(p.promise_id).length).length;
    const meas = ps.filter(p => p.specificity==='measurable').length;
    return `<article class="card ${curCandidate===c.politician_id?'sel':''}">
      <div class="card-top">
        <div class="monogram" title="${esc(c.party||'')}">${esc((c.party||'?').slice(0,1))}</div>
        <div class="who">
          <h3 class="cname">${esc(c.display_name)}</h3>
          <div class="crole">${esc(ROLE[c.incumbent_challenger]||'Candidate')}${c.party?' &middot; '+esc(c.party):''}</div>
        </div>
      </div>
      <dl class="money">
        <div class="money-row"><dt>Total raised</dt><dd>${usd(f.total_receipts)}</dd></div>
        <div class="money-row"><dt>Cash on hand</dt><dd>${usd(f.cash_on_hand)}</dd></div>
        ${raised?moneyMix(f, raised):''}
      </dl>
      ${outsideMoney(f, c)}
      ${donors.length?`<div class="donors"><h4>Largest committee donors</h4>${donors.map(d=>
        `<div class="donor"><span>${esc(d.committee_name||'')}</span><span>${usd(d.total_amount)}</span></div>`
      ).join('')}${bundled(c)}<p class="donor-note">A political action committee may give a
        candidate $5,000 per election, so $10,000 across a primary and a
        general. Party committees and a candidate&rsquo;s own affiliated
        committees follow different rules and can appear here for more.</p>${coverage(f)}</div>`:''}
      ${votingRecord(c)}
      <div class="pcount">
        <h4>Promises on record</h4>
        ${ps.length ? `<div class="tally-row">
            <span class="chip"><b>${ps.length}</b> total</span>
            <span class="chip"><b>${meas}</b> measurable</span>
            <span class="chip"><b>${nWithVotes}</b> with related votes</span>
          </div>
          <button class="view-btn" data-p="${c.politician_id}">
            ${curCandidate===c.politician_id?'Showing promises below':'Show promises &amp; record'}
          </button>`
        : `<p class="none">Promises not researched yet. Money comes from FEC filings, which
             cover every candidate;${recordFor(c)?' the voting record above is this member&rsquo;s own.':''}
             Promises require reading a candidate&rsquo;s own words, and we have not
             reached this one yet.</p>`}
      </div>
    </article>`;
  }).join('') || `<p class="none">No candidates with FEC filings in this seat.</p>`;
}

function renderDetail(){
  const host = document.getElementById('detail');
  if(curCandidate===null){ host.innerHTML=''; return; }
  const c = DATA.candidates.find(x => x.politician_id===curCandidate);
  if(!c){ host.innerHTML=''; return; }
  let ps = promisesFor(c);
  const topics = [...new Set(ps.map(p=>p.topic))].sort();
  const scored = ps.filter(p=>evalFor(p.promise_id)).length;
  const withVotes = ps.filter(p=>!evalFor(p.promise_id) && promiseVotesFor(p.promise_id).length).length;
  if(curFilter!=='all') ps = ps.filter(p=>p.topic===curFilter);

  host.innerHTML = `
    <div class="detail-head"><h2>${esc(c.display_name)}</h2></div>
    <p class="detail-note">Every quote below was matched character-for-character against its
      source document before it was stored. ${(scored+withVotes)>0
        ? `${scored>0 ? `${scored} carr${scored===1?'ies':'y'} an assessment of how ${esc(c.display_name.split(' ').pop())}
             has voted on related bills. ` : ''}${withVotes>0
             ? `${withVotes} show the related votes without an assessment, for you to read directly. ` : ''}
           We publish no finding that a promise was broken: those are held for a person to check.`
        : `*None of these have related votes to show. Either ${esc(c.display_name)} has not held
           federal office, or we have not matched these subjects to roll calls in this Congress.
           That is a gap in our coverage, not a finding about the candidate.`}</p>
    <div class="filters">
      <button class="filt" aria-pressed="${curFilter==='all'}" data-f="all">All ${promisesFor(c).length}</button>
      ${topics.map(t=>`<button class="filt" aria-pressed="${curFilter===t}" data-f="${esc(t)}">${esc(t.replace(/_/g,' '))}</button>`).join('')}
    </div>
    ${ps.map(renderPromise).join('')}`;
}

function renderPromise(p){
  const ev = evalFor(p.promise_id);
  const before = (p.context_before||'').slice(-190);
  const after  = (p.context_after||'').slice(0,190);
  return `<article class="promise">
    <button class="p-head" aria-expanded="false">
      <p class="q">&ldquo;${esc(p.verbatim_quote)}&rdquo;</p>
      <span class="p-meta">
        <span class="topic">${esc((p.topic||'').replace(/_/g,' '))}</span>
        <span class="spec">${esc(p.specificity||'')}</span>
      </span>
    </button>
    <div class="p-body">
      <p class="ctx">&hellip;${esc(before)}<mark>${esc(p.verbatim_quote)}</mark>${esc(after)}&hellip;</p>
      <p class="src">Source: ${esc(p.document_title||docKind(p.doc_type))}
        &middot; <a href="${esc(p.document_url)}" target="_blank" rel="noopener">view original</a></p>
      ${ev ? renderVerdict(ev) : renderRelatedVotes(p)}
    </div>
  </article>`;
}

function renderRelatedVotes(p){
  const vs = promiseVotesFor(p.promise_id);
  if(!vs.length) return `<div class="empty" style="margin-top:16px">
    <b>*We have no votes to show beside this promise.</b> Either this candidate has never held
    federal office, or we have not matched this subject to any roll call in this Congress.
    That is a gap in what we have gathered, not a finding about the candidate.</div>`;
  const noSummary = vs.filter(v=>!billOf(v).has_summary).length;
  return `<div class="related">
    <h5 class="rel-h">How they voted on related bills &mdash; ${vs.length} roll call${vs.length===1?'':'s'}</h5>
    <p class="rel-note">We do not score these. A bill&rsquo;s title is written to persuade and often
      names the opposite of its effect, so read what the bill <b>does</b> before deciding what a
      vote meant. Every row links to the official record.</p>
    ${vs.map(v=>`<div class="rel">
      <span class="pos pos-${esc((v.position||'').toLowerCase())}">${esc(v.position||'')}</span>
      <span class="bill">
        <b>${esc(v.bill_key||'')}</b> ${esc(billOf(v).title||'')}
        ${v.is_omnibus?'<span class="omni" title="bundles many unrelated provisions">omnibus</span>':''}
        ${v.is_procedural?'<span class="omni" title="a vote on process, not policy">procedural</span>':''}
        ${billOf(v).has_summary
          ? `<span class="what">${esc(billOf(v).summary||'')}</span>`
          : `<span class="what what-missing">*Congress.gov published no summary for this bill.
             The title alone may not describe what it does.</span>`}
      </span>
      <a href="${esc(v.congress_gov_url)}" target="_blank" rel="noopener">record</a>
    </div>`).join('')}
    ${noSummary ? `<p class="rel-foot">*${noSummary} of these ${noSummary===1?'bill has':'bills have'}
      no published summary, so only the title is shown. Treat those with extra caution.</p>` : ''}
  </div>`;
}

function renderVerdict(ev){
  const rs = receiptsFor(ev.evaluation_id);
  const score = ev.consistency_score;
  const dirClass = d => d==='supports'?'dir-s':(d==='contradicts'?'dir-c':'dir-x');
  // Invariant: a score never appears without its reasoning, its citations and
  // a route to the methodology. The status word alone is a verdict; the gloss
  // and the link are what make it checkable.
  const gloss = ev.status==='completed'
    ? 'the record shows this carried out'
    : ev.status==='in_progress' ? 'voted on related bills, not settled'
    : '';
  return `<div class="verdict">
    <div class="v-head">
      <span class="v-status">${esc((ev.status||'').replace(/_/g,' '))}</span>
      ${gloss?`<span class="v-gloss">${esc(gloss)}</span>`:''}
      <span style="font-size:13px;color:var(--muted)">against the roll-call record</span>
      ${score!=null?`<span class="score"><span class="score-track"><span style="width:${score}%"></span></span>
        <span class="score-num">${score}/100</span></span>`:''}
    </div>
    <div class="v-body">
      <p class="reason">${esc(ev.llm_reasoning)}</p>
      <h5 class="rcpt-h">Receipts &mdash; ${rs.length} cited vote${rs.length===1?'':'s'}</h5>
      ${rs.map(r=>`<div class="rcpt">
        <span class="dir ${dirClass(r.direction)}">${esc(r.direction)}</span>
        <span class="bill"><b>${esc(r.bill_number||'')}</b> ${esc(r.bill_title||'')}
          ${r.bill_is_omnibus?'<span class="omni">omnibus</span>':''}</span>
        <span class="pos">${esc(r.position||'')}</span>
        <a href="${esc(r.congress_gov_url)}" target="_blank" rel="noopener">record</a>
      </div>`).join('')}
      <p class="v-prov">Generated by ${esc(ev.model_name||'a language model')}
        (${esc(ev.prompt_version||'')}), then every citation checked against the
        database before display.
        <a href="methodology.html" target="_blank" rel="noopener">How this is produced
        and what it cannot tell you</a>.</p>
    </div>
  </div>`;
}

/* Where the money came from, as proportions rather than one PAC percentage.
   Small-dollar versus itemized versus committee money is the distinction a
   reader is actually looking for, and all three are already in the filing. */
function moneyMix(f, raised){
  const parts = [
    {k:'small', label:'Small donors', v:Number(f.individual_unitemized||0)},
    {k:'indiv', label:'Itemized individuals', v:Number(f.individual_itemized_official||0)},
    {k:'pac',   label:'Committees &amp; PACs', v:Number(f.pac_contributions_official||0)},
  ].filter(p => p.v > 0);
  if(!parts.length) return '';
  const known = parts.reduce((s,p)=>s+p.v, 0) || 1;
  return `<div class="mix" role="img" aria-label="Funding sources">
      ${parts.map(p=>`<span class="seg ${p.k}" style="width:${(p.v/known*100).toFixed(1)}%"
        title="${p.label}: ${usd(p.v)}"></span>`).join('')}
    </div>
    <div class="mix-key">${parts.map(p=>
      `<span><i class="sw ${p.k}"></i>${p.label} ${Math.round(p.v/known*100)}%</span>`).join('')}</div>`;
}

/* Independent expenditures: money spent for or against a candidate by people
   the candidate does not control, and does not report. Worth its own line
   because it is invisible in a fundraising total, and worth naming because
   it is the money with no legal ceiling on it. A direct committee
   contribution stops at $10,000; this does not, so on most cards below it is
   the larger number by an order of magnitude. */
function spendersFor(c, stance){
  return byId(DATA.spenders,'candidacy_id',c.candidacy_id)
    .filter(s => s.stance===stance)
    .sort((a,b)=>a.spender_rank-b.spender_rank);
}

function spenderList(rows, label){
  if(!rows.length) return '';
  return `<div class="sp-group"><h5>${label}</h5>${rows.map(s=>
    `<a class="sp" href="https://www.fec.gov/data/committee/${esc(s.spender_cmte_id)}/"
        target="_blank" rel="noopener">
       <span>${esc(s.spender_name||'')}</span><span>${usd(s.total_amount)}</span>
     </a>`).join('')}</div>`;
}

function outsideMoney(f, c){
  const forC = Number(f.ie_support||0), against = Number(f.ie_oppose||0);
  if(!forC && !against) return '';
  const sup = spendersFor(c,'supporting'), opp = spendersFor(c,'opposing');
  return `<div class="outside"><h4>Outside spending</h4>
    <div class="out-row"><span>Supporting</span><b>${usd(forC)}</b></div>
    <div class="out-row"><span>Opposing</span><b>${usd(against)}</b></div>
    ${spenderList(sup,'Spent supporting')}
    ${spenderList(opp,'Spent opposing')}
    <p class="out-note">Spent independently by other groups, not by the campaign,
      and not subject to contribution limits. Largest spenders shown; each links
      to its FEC record.</p></div>`;
}

/* Bundling: individuals give the money, an organisation collects and
   delivers it together. Every underlying contribution is capped and legal,
   and the organisation directing them is not named anywhere in a donor list,
   because it never donated. This is the channel organised giving mostly
   uses, so leaving it out while naming $10,000 committee donors would
   understate exactly the relationship a reader came to check. */
function bundled(c){
  const rows = byId(DATA.conduits,'candidacy_id',c.candidacy_id)
    .sort((a,b)=>a.conduit_rank-b.conduit_rank);
  if(!rows.length) return '';
  return `<div class="bundle"><h4>Bundled through</h4>${rows.map(b=>
    `<a class="bd" href="https://www.fec.gov/data/committee/${esc(b.conduit_cmte_id)}/"
        target="_blank" rel="noopener">
       <span class="bd-n">${esc(b.conduit_name||'')}</span>
       <span class="bd-c">${Number(b.contribution_count).toLocaleString('en-US')}
         gift${Number(b.contribution_count)===1?'':'s'}</span>
       <span class="bd-a">${usd(b.total_amount)}</span>
     </a>`).join('')}
    <p class="out-note">Given by individuals, collected and passed on by these
      groups. Not a donation from the group, and not outside spending.</p></div>`;
}

/* How much of this campaign's individual contributions we actually hold.
   Shown because the donor list above it looks complete and is not: committee
   money is close to complete, itemized individual money is barely loaded,
   and individual money is the larger share of what campaigns raise. Saying
   so is the difference between a short list and a misleading one. */
function coverage(f){
  const owed = Number(f.individual_itemized_official||0);
  const held = Number(f.individual_itemized_loaded||0);
  if(owed <= 0) return '';
  const pct = Math.min(100, Math.round(held/owed*100));
  return `<div class="cover">
    <div class="cover-bar" role="img"
         aria-label="We hold ${pct} percent of itemized individual contributions">
      <span style="width:${pct}%"></span></div>
    <p class="cover-note">Individual contributions: we hold itemized records for
      ${usd(held)} of the ${usd(owed)} this campaign reports (${pct}%). The
      remainder is mostly contributions filed since our last bulk load. Where
      giving was bundled through a conduit we can see that it was, but cannot
      yet name the organisation that bundled it.
      <a href="methodology.html">How coverage is measured</a></p></div>`;
}


/* A sitting member's own record. This is what a card shows instead of an
   apology when nobody has researched their promises yet. */
function votingRecord(c){
  const r = recordFor(c);
  if(!r) return '';
  const topics = topicsFor(c).slice(0,4);
  const votes = votesFor(c);
  const span = [r.first_vote, r.last_vote].filter(Boolean).map(d=>String(d).slice(0,4));
  const years = span.length===2 && span[0]!==span[1] ? `${span[0]}&ndash;${span[1]}` : (span[0]||'');
  return `<div class="record">
    <h4>Voting record ${years?`<span class="yrs">${years}</span>`:''}</h4>
    <div class="tally-row">
      <span class="chip"><b>${Number(r.roll_calls).toLocaleString('en-US')}</b> roll calls</span>
      <span class="chip"><b>${Number(r.yea).toLocaleString('en-US')}</b> yea</span>
      <span class="chip"><b>${Number(r.nay).toLocaleString('en-US')}</b> nay</span>
    </div>
    ${topics.length?`<div class="topics">${topics.map(t=>
      `<span class="tp">${esc(t.policy_area)}<i>${t.votes}</i></span>`).join('')}</div>`:''}
    ${votes.length?`<div class="recent"><h5>Most recent votes</h5>${votes.map(v=>
      `<a class="rv" href="${esc(v.congress_gov_url)}" target="_blank" rel="noopener">
         <span class="pos">${esc(v.position)}</span>
         <span class="rv-bill"><b>${esc(v.bill_number||'')}</b> ${esc(v.bill_title||'')}</span>
         <span class="rv-date">${esc(String(v.voted_at).slice(0,10))}</span>
       </a>`).join('')}</div>`:''}
  </div>`;
}

function render(){ renderMap(); renderNav(); renderRace(); renderDetail(); }

function selectState(code){
  curState = code; curSeat = null; curCandidate = null; curFilter = 'all';
  mapLevel = 'state';
  render();
}

document.addEventListener('change', e => {
  if(e.target.id==='stateSel') selectState(e.target.value);
});

// Paths carry role="button" and tabindex, so they must answer the keyboard too.
document.addEventListener('keydown', e => {
  if(e.key !== 'Enter' && e.key !== ' ') return;
  const path = e.target.closest && e.target.closest('path[data-state], path[data-seat]');
  if(path){ e.preventDefault(); path.dispatchEvent(new MouseEvent('click', {bubbles:true})); }
});
document.addEventListener('click', e => {
  const mapState = e.target.closest('path[data-state]');
  if(mapState){ selectState(mapState.dataset.state); return; }
  const mapSeat = e.target.closest('path[data-seat]');
  if(mapSeat){
    // Frame the district before re-rendering: metro districts are otherwise
    // too small to read once selected. Uses the clicked element's own bbox.
    frameSeatPath(mapSeat);
    curSeat=mapSeat.dataset.seat; curCandidate=null; curFilter='all'; render();
    document.getElementById('raceHead').scrollIntoView({behavior:'smooth',block:'start'}); return; }
  if(e.target.closest('[data-back]')){ mapLevel='nation'; render(); return; }
  const tab = e.target.closest('.tab');
  if(tab){ curSeat=tab.dataset.seat; curCandidate=null; curFilter='all'; render(); return; }
  const view = e.target.closest('.view-btn');
  if(view){
    const id=Number(view.dataset.p);
    curCandidate = curCandidate===id ? null : id; curFilter='all'; render();
    if(curCandidate!==null) document.getElementById('detail').scrollIntoView({behavior:'smooth',block:'start'});
    return;
  }
  const filt = e.target.closest('.filt');
  if(filt){ curFilter=filt.dataset.f; renderDetail(); return; }
  const head = e.target.closest('.p-head');
  if(head){
    const art=head.closest('.promise'); const open=art.classList.toggle('open');
    head.setAttribute('aria-expanded', open); return;
  }
});
render();

/* -- address to district ---------------------------------------------------
 * The honest answer to "which district am I in". City labels orient a reader;
 * they cannot answer this, because districts split cities: five points across
 * Houston fall in five different districts. Only an address resolves it.
 *
 * WHY JSONP, WHICH IS OTHERWISE OBSOLETE. This is a static page on GitHub
 * Pages with no server of its own, and the Census geocoder returns no
 * Access-Control-Allow-Origin header, so a normal fetch is blocked by the
 * browser before we ever see a response. JSONP predates CORS and sidesteps
 * it: the reply arrives as a <script> whose body calls our callback.
 *
 * That means executing script from a remote origin, so the trust is
 * deliberate and narrow. The URL is built here from a hardcoded https origin
 * and never from anything a user typed beyond one encoded query parameter;
 * the origin is a US government service; the tag is removed and the callback
 * deleted whether it succeeds, fails or times out. We do not eval the
 * response ourselves.
 *
 * PRIVACY. The address goes from the reader's browser straight to the Census
 * Bureau. It never reaches us, we store nothing, and the form says so.
 */

const GEOCODER_ORIGIN = 'https://geocoding.geo.census.gov';
const GEOCODER_PATH = '/geocoder/geographies/onelineaddress';
const GEOCODER_TIMEOUT_MS = 15000;
const CD_LAYER = '119th Congressional Districts';

let geocodeSeq = 0;

function lookupAddress(address){
  return new Promise((resolve, reject) => {
    const cb = `__tallyGeo${++geocodeSeq}`;
    const script = document.createElement('script');
    let timer = 0;

    const cleanup = () => {
      clearTimeout(timer);
      delete window[cb];
      if(script.parentNode) script.parentNode.removeChild(script);
    };

    window[cb] = payload => { cleanup(); resolve(payload); };
    script.onerror = () => { cleanup(); reject(new Error('network')); };
    timer = setTimeout(() => { cleanup(); reject(new Error('timeout')); }, GEOCODER_TIMEOUT_MS);

    const params = new URLSearchParams({
      address, benchmark: 'Public_AR_Current', vintage: 'Current_Current',
      format: 'jsonp', callback: cb,
    });
    script.src = `${GEOCODER_ORIGIN}${GEOCODER_PATH}?${params.toString()}`;
    document.head.appendChild(script);
  });
}

/* Pull state + district out of the geocoder's reply, or null. Written to fail
 * closed: an unexpected shape returns null and the caller says so, rather
 * than guessing a district for someone's home address. */
function districtFromGeocode(payload){
  const matches = payload && payload.result && payload.result.addressMatches;
  if(!Array.isArray(matches) || !matches.length) return null;
  const match = matches[0];
  const layer = match.geographies && match.geographies[CD_LAYER];
  if(!Array.isArray(layer) || !layer.length) return null;
  const fips = String(layer[0].STATE || '');
  const cd = String(layer[0].CD119 || '');
  const state = FIPS[fips];
  if(!state || !cd) return null;
  return { state, district: cd, matched: String(match.matchedAddress || '') };
}

function finderSay(text, kind){
  const el = document.getElementById('finderStatus');
  if(!el) return;
  el.textContent = text;
  el.className = `finder-status${kind ? ' ' + kind : ''}`;
}

async function runFinder(address){
  const btn = document.getElementById('findBtn');
  if(btn) btn.disabled = true;
  finderSay('Looking up that address…');
  try {
    const found = districtFromGeocode(await lookupAddress(address));
    if(!found){
      finderSay('No match for that address. Try including the city and state, '
              + 'or a ZIP code.', 'warn');
      return;
    }
    const num = found.district === '00' ? 'at large'
              : found.district === '98' ? 'delegate, at large'
              : `district ${Number(found.district)}`;

    // Navigating to a place this snapshot has no data for renders an empty
    // shell and looks broken. DC is the live case: the geocoder answers with
    // a delegate district, and this product covers the 435 VOTING seats, so
    // DC is absent by design. Say that plainly and stay put.
    if(!STATES.includes(found.state)){
      finderSay(`${found.matched} is in ${found.state} ${num}. This site covers `
        + `the 435 voting House seats, and ${found.state} is not one of them, so `
        + `there is nothing to show for it.`, 'warn');
      return;
    }

    const seat = `house|${found.district}`;
    const known = seatsIn(found.state).some(([k]) => k === seat);
    curState = found.state; curCandidate = null; curFilter = 'all';
    mapLevel = 'state';
    // Only select the seat if this snapshot actually carries it, rather than
    // selecting a tab that does not exist.
    curSeat = known ? seat : null;
    render();
    finderSay(known
      ? `${found.matched} is in ${found.state} ${num}.`
      : `${found.matched} is in ${found.state} ${num}, which this snapshot does `
        + `not carry yet. Showing ${found.state}.`, known ? 'ok' : 'warn');
    const head = document.getElementById('raceHead');
    if(head) head.scrollIntoView({behavior:'smooth', block:'start'});
  } catch (err) {
    finderSay(err && err.message === 'timeout'
      ? 'The Census geocoder did not answer in time. Try again in a moment.'
      : 'Could not reach the Census geocoder. Check your connection and try again.',
      'warn');
  } finally {
    if(btn) btn.disabled = false;
  }
}

document.addEventListener('submit', e => {
  const form = e.target.closest && e.target.closest('#finder');
  if(!form) return;
  e.preventDefault();
  const input = document.getElementById('addr');
  const address = (input && input.value || '').trim();
  if(!address){ finderSay('Enter an address first.', 'warn'); return; }
  runFinder(address);
});
