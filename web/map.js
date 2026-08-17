/* Interactive map: the nation by state, then one state by district.
 *
 * Two levels on purpose. Congressional districts are drawn by equal
 * population, not equal area, so a single national map of all 436 makes the
 * districts where the most people live into unclickable specks: Manhattan
 * holds several inside a few square miles while one Wyoming district covers
 * the state. Drilling nation -> state -> district keeps every district a
 * real target and is also the order a reader actually thinks in.
 *
 * Geometry is Census cb_2024 cd119 at 3% simplification, projected to Albers
 * USA by mapshaper so Alaska and Hawaii sit in their conventional insets and
 * no projection maths is needed here. It arrives as TopoJSON because that is
 * 188 KB where the same shapes as GeoJSON are 1.2 MB; the cost is the ~30
 * lines of decoding below. Simplification is topology-preserving, so
 * neighbouring districts still share an edge instead of developing gaps.
 *
 * The map never stands alone: the seat tabs beside it do the same job for
 * anyone not using a mouse, and for the urban districts that stay small even
 * at state zoom.
 */

const GEO = window.__TALLY_GEO__;

const FIPS = {
  '01':'AL','02':'AK','04':'AZ','05':'AR','06':'CA','08':'CO','09':'CT','10':'DE',
  '11':'DC','12':'FL','13':'GA','15':'HI','16':'ID','17':'IL','18':'IN','19':'IA',
  '20':'KS','21':'KY','22':'LA','23':'ME','24':'MD','25':'MA','26':'MI','27':'MN',
  '28':'MS','29':'MO','30':'MT','31':'NE','32':'NV','33':'NH','34':'NJ','35':'NM',
  '36':'NY','37':'NC','38':'ND','39':'OH','40':'OK','41':'OR','42':'PA','44':'RI',
  '45':'SC','46':'SD','47':'TN','48':'TX','49':'UT','50':'VT','51':'VA','53':'WA',
  '54':'WV','55':'WI','56':'WY',
};

/* -- TopoJSON decoding ---------------------------------------------------- */

const ARCS = (() => {
  const [sx, sy] = GEO.transform.scale;
  const [tx, ty] = GEO.transform.translate;
  return GEO.arcs.map(arc => {
    let x = 0, y = 0;
    return arc.map(([dx, dy]) => {
      x += dx; y += dy;
      // Negate y: the projection puts north up, SVG puts y down.
      return [x * sx + tx, -(y * sy + ty)];
    });
  });
})();

const arcPoints = i => (i < 0 ? ARCS[~i].slice().reverse() : ARCS[i]);

function ringPoints(ring){
  let pts = [];
  ring.forEach((idx, n) => {
    const a = arcPoints(idx);
    pts = pts.concat(n === 0 ? a : a.slice(1));
  });
  return pts;
}

function pathFor(geom){
  const polys = geom.type === 'Polygon' ? [geom.arcs] : geom.arcs;
  let d = '';
  for(const poly of polys){
    for(const ring of poly){
      const pts = ringPoints(ring);
      if(pts.length < 3) continue;
      d += 'M' + pts.map(p => `${Math.round(p[0])},${Math.round(p[1])}`).join('L') + 'Z';
    }
  }
  return d;
}

function boundsOf(paths){
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for(const {geom} of paths){
    const polys = geom.type === 'Polygon' ? [geom.arcs] : geom.arcs;
    for(const poly of polys) for(const ring of poly) for(const p of ringPoints(ring)){
      if(p[0] < x0) x0 = p[0]; if(p[0] > x1) x1 = p[0];
      if(p[1] < y0) y0 = p[1]; if(p[1] > y1) y1 = p[1];
    }
  }
  return [x0, y0, x1 - x0, y1 - y0];
}

/* -- rendering ------------------------------------------------------------ */

const STATE_GEOMS = GEO.objects.states.geometries
  .filter(g => FIPS[g.properties.STATEFP])
  .map(g => ({ code: FIPS[g.properties.STATEFP], geom: g }));

const DISTRICT_GEOMS = GEO.objects.districts.geometries
  .filter(g => FIPS[g.properties.STATEFP])
  .map(g => ({
    code: FIPS[g.properties.STATEFP],
    district: g.properties.CD119FP,
    geom: g,
  }));

function svgFrame(paths, inner, extraClass){
  const [x, y, w, h] = boundsOf(paths);
  const pad = Math.max(w, h) * 0.02;
  mapBase = [Math.round(x - pad), Math.round(y - pad),
             Math.round(w + pad * 2), Math.round(h + pad * 2)];
  return `<svg class="map ${extraClass||''}" viewBox="${mapBase.join(' ')}" role="img" `
       + `aria-label="Clickable map">`
       + `<g class="mz" transform="${viewTransform()}">${inner}</g></svg>`;
}

/* -- zoom and pan ---------------------------------------------------------
 * Congressional districts are equal-population, so a metro cluster is a knot
 * of tiny shapes: Los Angeles packs a dozen districts into the area one rural
 * district covers alone. Fitting a whole state makes those unclickable, which
 * is the specific problem this solves.
 *
 * The view is a persistent {k, tx, ty} applied to a group inside the SVG,
 * rather than a rewritten viewBox, so the fitted frame stays the reference
 * for clamping and for framing a single district. It has to live outside the
 * DOM because renderMap replaces the map's innerHTML on every render.
 *
 * Strokes are non-scaling: without that, borders thicken with the zoom and
 * swallow exactly the small districts the zoom exists to reveal.
 */

const MAX_K = 60;
let mapView = { k: 1, tx: 0, ty: 0 };
let mapBase = [0, 0, 1, 1];     // the fitted viewBox of the current frame
let mapFrameKey = null;         // resets the view when the frame changes
let mapWired = false;

const viewTransform = () =>
  `translate(${mapView.tx} ${mapView.ty}) scale(${mapView.k})`;

function applyView(){
  const svg = document.querySelector('#map svg.map');
  if(!svg) return;
  const g = svg.querySelector('g.mz');
  if(g) g.setAttribute('transform', viewTransform());
  const zoomed = mapView.k > 1.01;
  svg.classList.toggle('zoomed', zoomed);
  const reset = document.querySelector('#map [data-zoom="reset"]');
  if(reset) reset.disabled = !zoomed;
}

function clampView(){
  const [x, y, w, h] = mapBase;
  if(mapView.k <= 1){ mapView = { k: 1, tx: 0, ty: 0 }; return; }
  if(mapView.k > MAX_K) mapView.k = MAX_K;
  // Keep the content covering the frame, so it can never be dragged away.
  const k = mapView.k;
  mapView.tx = Math.min(x * (1 - k), Math.max((x + w) * (1 - k), mapView.tx));
  mapView.ty = Math.min(y * (1 - k), Math.max((y + h) * (1 - k), mapView.ty));
}

function zoomAt(cx, cy, factor){
  const k0 = mapView.k;
  const k = Math.max(1, Math.min(MAX_K, k0 * factor));
  // Hold the point under the cursor still while the scale changes.
  mapView.tx = cx - (k / k0) * (cx - mapView.tx);
  mapView.ty = cy - (k / k0) * (cy - mapView.ty);
  mapView.k = k;
  clampView(); applyView();
}

function svgPoint(svg, clientX, clientY){
  const ctm = svg.getScreenCTM();
  if(!ctm || !svg.createSVGPoint) return null;
  const pt = svg.createSVGPoint();
  pt.x = clientX; pt.y = clientY;
  const p = pt.matrixTransform(ctm.inverse());
  return { x: p.x, y: p.y };
}

/* Frame one district. Called before the re-render that follows a click, so it
 * only sets the view; applyView runs once the new paths exist. */
function frameSeatPath(el){
  if(!el || !el.getBBox) return;
  let b;
  try { b = el.getBBox(); } catch { return; }
  if(!b || !b.width || !b.height) return;
  const [x, y, w, h] = mapBase;
  const k = Math.max(1, Math.min(MAX_K, 0.6 * Math.min(w / b.width, h / b.height)));
  mapView.k = k;
  mapView.tx = (x + w / 2) - k * (b.x + b.width / 2);
  mapView.ty = (y + h / 2) - k * (b.y + b.height / 2);
  clampView();
}

function pinchState(svg, pointers){
  const [a, b] = [...pointers.values()];
  if(!a || !b) return null;
  const mid = svgPoint(svg, (a.x + b.x) / 2, (a.y + b.y) / 2);
  if(!mid) return null;
  return { dist: Math.hypot(a.x - b.x, a.y - b.y), cx: mid.x, cy: mid.y };
}

/* Listeners bind once to #map, which survives every re-render. */
function wireMap(){
  const host = document.getElementById('map');
  if(mapWired || !host) return;
  mapWired = true;

  host.addEventListener('wheel', e => {
    const svg = e.target.closest && e.target.closest('svg.map');
    if(!svg) return;
    e.preventDefault();
    const p = svgPoint(svg, e.clientX, e.clientY);
    if(p) zoomAt(p.x, p.y, e.deltaY < 0 ? 1.18 : 1 / 1.18);
  }, { passive: false });

  const pointers = new Map();
  let travelled = 0, last = null, pinch = null;

  host.addEventListener('pointerdown', e => {
    const svg = e.target.closest && e.target.closest('svg.map');
    if(!svg) return;
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
    if(pointers.size === 1){ travelled = 0; last = svgPoint(svg, e.clientX, e.clientY); }
    if(pointers.size === 2) pinch = pinchState(svg, pointers);
  });

  host.addEventListener('pointermove', e => {
    if(!pointers.has(e.pointerId)) return;
    const svg = document.querySelector('#map svg.map');
    if(!svg) return;
    const prev = pointers.get(e.pointerId);
    travelled += Math.abs(e.clientX - prev.x) + Math.abs(e.clientY - prev.y);
    pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });

    if(pointers.size >= 2){
      const now = pinchState(svg, pointers);
      if(now && pinch && pinch.dist > 0){ zoomAt(pinch.cx, pinch.cy, now.dist / pinch.dist); }
      pinch = now;
      return;
    }
    if(mapView.k > 1 && last){
      const p = svgPoint(svg, e.clientX, e.clientY);
      if(!p) return;
      mapView.tx += p.x - last.x;
      mapView.ty += p.y - last.y;
      last = p;
      clampView(); applyView();
    }
  });

  const release = e => {
    pointers.delete(e.pointerId);
    if(pointers.size < 2) pinch = null;
    if(pointers.size === 0) last = null;
  };
  host.addEventListener('pointerup', release);
  host.addEventListener('pointercancel', release);
  host.addEventListener('pointerleave', release);

  // A drag that ends over a district must not also select it.
  host.addEventListener('click', e => {
    if(travelled > 6){ e.stopPropagation(); e.preventDefault(); travelled = 0; }
  }, true);

  host.addEventListener('click', e => {
    const btn = e.target.closest && e.target.closest('[data-zoom]');
    if(!btn) return;
    e.stopPropagation();
    const [x, y, w, h] = mapBase;
    const cx = x + w / 2, cy = y + h / 2;
    if(btn.dataset.zoom === 'in') zoomAt(cx, cy, 1.5);
    else if(btn.dataset.zoom === 'out') zoomAt(cx, cy, 1 / 1.5);
    else { mapView = { k: 1, tx: 0, ty: 0 }; applyView(); }
  });

  host.addEventListener('keydown', e => {
    const [x, y, w, h] = mapBase;
    const cx = x + w / 2, cy = y + h / 2;
    const step = 0.2 * w / mapView.k;
    if(e.key === '+' || e.key === '=') zoomAt(cx, cy, 1.4);
    else if(e.key === '-' || e.key === '_') zoomAt(cx, cy, 1 / 1.4);
    else if(e.key === '0'){ mapView = { k: 1, tx: 0, ty: 0 }; applyView(); }
    else if(e.key === 'ArrowLeft')  mapView.tx += step;
    else if(e.key === 'ArrowRight') mapView.tx -= step;
    else if(e.key === 'ArrowUp')    mapView.ty += step;
    else if(e.key === 'ArrowDown')  mapView.ty -= step;
    else return;
    e.preventDefault();
    clampView(); applyView();
  });
}

const ZOOM_CONTROLS = `<span class="map-zoom">
      <button type="button" data-zoom="out" title="Zoom out" aria-label="Zoom out">&minus;</button>
      <button type="button" data-zoom="in" title="Zoom in" aria-label="Zoom in">+</button>
      <button type="button" data-zoom="reset" title="Reset zoom" aria-label="Reset zoom" disabled>Reset</button>
    </span>`;

function renderMap(){
  const host = document.getElementById('map');
  if(!host) return;

  // Changing frame means the old pan/zoom is meaningless; start fitted.
  const frameKey = mapLevel === 'nation' ? 'nation' : 'state:' + curState;
  if(frameKey !== mapFrameKey){
    mapFrameKey = frameKey;
    mapView = { k: 1, tx: 0, ty: 0 };
  }

  if(mapLevel === 'nation'){
    const inner = STATE_GEOMS.map(s => {
      const cs = DATA.candidates.filter(c => c.state === s.code);
      const researched = cs.some(hasResearch);
      const cls = researched ? 'st researched' : (cs.length ? 'st funded' : 'st bare');
      const label = researched ? ' (promises researched)' : (cs.length ? ' (finance only)' : '');
      return `<path class="${cls} ${s.code===curState?'on':''}" d="${pathFor(s.geom)}"
        data-state="${s.code}" tabindex="0" role="button"
        aria-label="${s.code}${label}"><title>${s.code}${label}</title></path>`;
    }).join('');
    host.innerHTML = svgFrame(STATE_GEOMS, inner) + `
      <div class="map-legend">
        <span><i class="sw researched"></i>promises researched</span>
        <span><i class="sw funded"></i>finance only</span>
        ${ZOOM_CONTROLS}
        <span class="map-hint">Click a state to see its districts</span>
      </div>`;
    wireMap(); applyView();
    return;
  }

  const ds = DISTRICT_GEOMS.filter(d => d.code === curState);
  if(!ds.length){ host.innerHTML = ''; return; }
  const inner = ds.map(d => {
    const cs = DATA.candidates.filter(c =>
      c.state === curState && c.office === 'house' && c.district === d.district);
    const researched = cs.some(hasResearch);
    const key = `house|${d.district}`;
    const cls = researched ? 'st researched' : (cs.length ? 'st funded' : 'st bare');
    const num = d.district === '00' ? 'at large' : String(Number(d.district));
    return `<path class="${cls} ${key===curSeat?'on':''}" d="${pathFor(d.geom)}"
      data-seat="${key}" tabindex="0" role="button"
      aria-label="${curState} district ${num}"><title>${curState} ${num}</title></path>`;
  }).join('');
  host.innerHTML = svgFrame(ds, inner, 'state-level') + `
    <div class="map-legend">
      <button class="map-back" data-back="1">&larr; All states</button>
      <span><i class="sw researched"></i>promises researched</span>
      <span><i class="sw funded"></i>finance only, no promises yet</span>
      ${ZOOM_CONTROLS}
      <span class="map-hint">${curState} &middot; ${ds.length} district${ds.length===1?'':'s'}
        &middot; scroll or pinch to zoom, drag to pan &middot; Senate seats are
        statewide, use the tabs</span>
    </div>`;
  wireMap(); applyView();
}
