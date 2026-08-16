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
  return `<svg class="map ${extraClass||''}" viewBox="${Math.round(x-pad)} ${Math.round(y-pad)} `
       + `${Math.round(w+pad*2)} ${Math.round(h+pad*2)}" role="img" `
       + `aria-label="Clickable map">${inner}</svg>`;
}

function renderMap(){
  const host = document.getElementById('map');
  if(!host) return;

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
        <span class="map-hint">Click a state to see its districts</span>
      </div>`;
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
      <span class="map-hint">${curState} &middot; ${ds.length} district${ds.length===1?'':'s'}
        &middot; Senate seats are statewide, use the tabs</span>
    </div>`;
}
