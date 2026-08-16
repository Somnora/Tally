const DATA = window.__TALLY__;
const ROLE = {I:'Incumbent', C:'Challenger', O:'Open seat'};
const usd = n => n==null ? '--' : '$' + Math.round(Number(n)).toLocaleString('en-US');
const esc = s => String(s??'').replace(/[&<>"]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));

const districts = [...new Set(DATA.candidates.map(c => c.district))].sort();
let curDistrict = districts[0];
let curCandidate = null;
let curFilter = 'all';

const byId = (arr,k,v) => arr.filter(r => String(r[k])===String(v));

function financeFor(c){ return DATA.finance.find(f => f.candidacy_id===c.candidacy_id) || {}; }
function promisesFor(c){ return byId(DATA.promises,'politician_id',c.politician_id); }
function evalFor(pid){ return DATA.evaluations.find(e => e.promise_id===pid); }
function receiptsFor(eid){ return byId(DATA.evidence,'evaluation_id',eid); }

function renderTabs(){
  document.getElementById('tabs').innerHTML = districts.map(d =>
    `<button class="tab" role="tab" aria-selected="${d===curDistrict}" data-d="${d}">Maine ${d}</button>`
  ).join('');
}

function renderRace(){
  const cs = DATA.candidates.filter(c => c.district===curDistrict);
  const anyInc = cs.some(c => c.incumbent_challenger==='I');
  document.getElementById('raceHead').innerHTML =
    `<div class="race-name">Maine&rsquo;s ${curDistrict==='01'?'1st':'2nd'} congressional district</div>
     <div class="race-meta">US House &middot; 2026 cycle &middot; ${cs.length} candidates tracked &middot; ${anyInc?'incumbent running':'open seat'}</div>`;

  document.getElementById('grid').innerHTML = cs.map(c => {
    const f = financeFor(c), ps = promisesFor(c);
    const raised = Number(f.total_receipts||0), pac = Number(f.pac_contributions_official||0);
    const pacPct = raised ? Math.round(pac/raised*100) : 0;
    const donors = byId(DATA.donors,'candidacy_id',c.candidacy_id).slice(0,3);
    const nEval = ps.filter(p => evalFor(p.promise_id)).length;
    const meas = ps.filter(p => p.specificity==='measurable').length;
    return `<article class="card ${curCandidate===c.politician_id?'sel':''}">
      <div class="card-top">
        <div class="monogram" title="${esc(c.party)}">${esc((c.party||'?').slice(0,1))}</div>
        <div class="who">
          <h3 class="cname">${esc(c.display_name)}</h3>
          <div class="crole">${ROLE[c.incumbent_challenger]||'Candidate'} &middot; ${esc(c.party||'')}</div>
        </div>
      </div>
      <dl class="money">
        <div class="money-row"><dt>Total raised</dt><dd>${usd(f.total_receipts)}</dd></div>
        <div class="money-row"><dt>Cash on hand</dt><dd>${usd(f.cash_on_hand)}</dd></div>
        <div class="bar"><span style="width:${pacPct}%"></span></div>
        <div class="bar-cap"><span>${pacPct}% from committees &amp; PACs</span><span>${usd(pac)}</span></div>
      </dl>
      ${donors.length?`<div class="donors"><h4>Largest committee donors</h4>${donors.map(d=>
        `<div class="donor"><span>${esc(d.committee_name||'')}</span><span>${usd(d.total_amount)}</span></div>`).join('')}</div>`:''}
      <div class="pcount">
        <h4>Promises on record</h4>
        <div class="tally-row">
          <span class="chip"><b>${ps.length}</b> total</span>
          <span class="chip"><b>${meas}</b> measurable</span>
          <span class="chip"><b>${nEval}</b> checked against votes</span>
        </div>
        <button class="view-btn" data-p="${c.politician_id}">
          ${curCandidate===c.politician_id?'Showing promises below':'Show promises &amp; record'}
        </button>
      </div>
    </article>`;
  }).join('');
}

function renderDetail(){
  const host = document.getElementById('detail');
  if(curCandidate===null){ host.innerHTML=''; return; }
  const c = DATA.candidates.find(x => x.politician_id===curCandidate);
  let ps = promisesFor(c);
  const topics = [...new Set(ps.map(p=>p.topic))].sort();
  if(curFilter!=='all') ps = ps.filter(p=>p.topic===curFilter);
  const checked = promisesFor(c).filter(p=>evalFor(p.promise_id)).length;

  host.innerHTML = `
    <div class="detail-head"><h2>${esc(c.display_name)}</h2></div>
    <p class="detail-note">Every quote below was matched character-for-character against its source
      document before it was stored. ${checked>0
        ? `${checked} of these have been checked against ${esc(c.display_name.split(' ').pop())}&rsquo;s roll-call record.`
        : `None have been checked against a voting record yet &mdash; ${esc(c.display_name)} has not served in Congress, so there are no votes to compare.`}</p>
    <div class="filters">
      <button class="filt" aria-pressed="${curFilter==='all'}" data-f="all">All ${promisesFor(c).length}</button>
      ${topics.map(t=>`<button class="filt" aria-pressed="${curFilter===t}" data-f="${esc(t)}">${esc(t.replace(/_/g,' '))}</button>`).join('')}
    </div>
    ${ps.map(p => renderPromise(p)).join('')}`;
}

function renderPromise(p){
  const ev = evalFor(p.promise_id);
  const before = (p.context_before||'').slice(-190);
  const after  = (p.context_after||'').slice(0,190);
  return `<article class="promise" data-id="${p.promise_id}">
    <button class="p-head" aria-expanded="false">
      <p class="q">&ldquo;${esc(p.verbatim_quote)}&rdquo;</p>
      <span class="p-meta">
        <span class="topic">${esc((p.topic||'').replace(/_/g,' '))}</span>
        <span class="spec">${esc(p.specificity||'')}</span>
      </span>
    </button>
    <div class="p-body">
      <p class="ctx">&hellip;${esc(before)}<mark>${esc(p.verbatim_quote)}</mark>${esc(after)}&hellip;</p>
      <p class="src">Source: ${esc(p.document_title||p.doc_type||'document')}
        &middot; <a href="${esc(p.document_url)}" target="_blank" rel="noopener">view original</a></p>
      ${ev ? renderVerdict(ev) : `<div class="empty" style="margin-top:16px">
        <b>Not yet checked against a voting record.</b> An alignment verdict is only
        published when it can cite specific roll-call votes. Nothing is shown here
        rather than a guess.</div>`}
    </div>
  </article>`;
}

function renderVerdict(ev){
  const rs = receiptsFor(ev.evaluation_id);
  const score = ev.consistency_score;
  const dirClass = d => d==='supports'?'dir-s':(d==='contradicts'?'dir-c':'dir-x');
  return `<div class="verdict">
    <div class="v-head">
      <span class="v-status">${esc((ev.status||'').replace(/_/g,' '))}</span>
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
    </div>
  </div>`;
}

function render(){ renderTabs(); renderRace(); renderDetail(); }

document.addEventListener('click', e => {
  const tab = e.target.closest('.tab');
  if(tab){ curDistrict=tab.dataset.d; curCandidate=null; curFilter='all'; render(); return; }
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
