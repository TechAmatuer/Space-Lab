'use strict';
/* Read-only public copy of Space Lab. Everything comes from data.json; nothing here can change anything. */
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const num = (x, d = 2) => (x === null || x === undefined || x === '' || isNaN(+x)) ? '—' : (+x).toFixed(d);
const pct = x => (x === null || x === undefined) ? '—' : Math.round(x * 100) + '%';
const LD_AU = 0.00256955529;
const sizeFromH = h => (h === null || h === undefined || isNaN(+h)) ? null : 1329e3 / Math.sqrt(0.14) * Math.pow(10, -h / 5);
const fmtSize = m => m === null ? '?' : m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m';
const day = t => t ? new Date(t * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
let D = null;

function modal(html) { $('#modalBody').innerHTML = html; $('#modal').classList.add('on'); return $('#modalBody'); }

// Fix #1 & #2: Shared error banner for viewer failures (LC + Orbit3D)
const VIEWER_ERR = `<div class="card banner"><h3>⚠️ Failed to load</h3><p>The requested data is missing or the file is corrupted. Reload the page and try again.</p><button class="b primary" onclick="location.reload()">Reload</button></div>`;

function tess() {
  const t = D?.tess, m = t?.model || {};
  if (t && !t.stats) t.stats = {};
  if (!t) { $('#v-tess').innerHTML = VIEWER_ERR + '<div class="card"><h3>Looking for new planets in NASA TESS data</h3><p>The TESS data hasn\'t loaded yet. It should appear once the overnight pipeline runs.</p></div>'; return; }

  const cards = t.cands.map(c => {
    const ai = c.ai, cen = c.centroid;
    const what = c.kind === 'periodic' ? `dips every <b class="mono">${num(c.period, 4)} days</b>` : 'one lone dip (period unknown)';
    return `<div class="card" id="tic-${c.tic}"><div class="row"><h3>TIC ${c.tic}</h3><span class="chip">${esc(c.status)}</span>${c.known ? `<span class="chip ok">known: ${esc(c.known)}</span>` : ''}<span class="spacer"></span><span class="small muted">found ${day(c.created)}</span></div>
      <div class="small" style="margin:4px 0 8px">${what} · depth <b class="mono">${Math.round(c.depth * 1e6)} ppm</b> · ${num(c.duration * 24, 1)} h · SNR ${num(c.snr, 1)} · ~<b>${num(c.rp, 1)}× Earth</b> if it is a planet</div>
      ${(Array.isArray(c.flags) ? c.flags : []).map(f => `<span class="chip warn">${esc(f)}</span>`).join('')}
      ${c.plot ? `<img class="plot" src="${esc(c.plot)}" alt="diagnostic plots" loading="lazy">` : ''}
      <p>${esc(c.explain || '')}</p>
      ${ai ? `<div class="note"><b>AI first look</b> (a local vision model read the plot): <b>${esc(ai.verdict)}</b>, ${pct(ai.confidence)}${(ai.reasons || []).length ? ' — ' + ai.reasons.map(esc).join('; ') : ''}</div>` : '<div class="note"><b>Awaiting AI analysis</b> — a local vision model will review the diagnostic plots and add its verdict here.</div>'}
      ${c.model_score !== null && c.model_score !== undefined ? `<div class="note"><b>Volunteer\'s own model</b>: ${pct(c.model_score)} planet-like</div>` : ''}
      ${cen ? `<div class="note"><b>Centroid test</b>: ${esc(cen.verdict)} (offset ${num(cen.offset_arcsec, 0)}″, ${num(cen.signif, 1)}σ)${cen.png ? `<br><img class="thumb plot" src="${esc(cen.png)}" alt="centroid" loading="lazy">` : ''}</div>` : '<div class="note"><b>Awaiting centroid test</b> — checking whether the dip signal comes from the target star or a nearby impostor.</div>'}
      <div class="row">${c.lc ? `<button class="b primary" data-lc="${esc(c.lc)}" data-p="${c.period || ''}" data-t0="${c.t0}" data-dur="${c.duration}" data-dep="${c.depth}">Interactive light curve</button>` : ''}
      <a class="b" href="https://exofop.ipac.caltech.edu/tess/target.php?id=${c.tic}" target="_blank" rel="noopener">ExoFOP ↗</a></div></div>`;
  }).join('');

  $('#v-tess').innerHTML = `<div class="card"><h3>Looking for new planets in NASA TESS data</h3>
    <p>Each night a home Mac downloads TESS light curves of nearby small stars, searches for repeating dips, and runs the standard checks that expose eclipsing binary stars. These are the signals that survived. Most will still turn out to be false alarms; that\'s how planet hunting works. Opinions welcome.</p>
    <div class="small muted">${t.stats.done || 0} stars checked so far · ${t.stats.queued || 0} waiting${m.ready ? ` · the volunteer\'s own model has learned from ${m.n_user} of their decisions` : ''}</div></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">A GPU-based Box Least Squares search finds repeating brightness dips in TESS light curves. Each candidate goes through false-positive checks (depth ratios, centroid test, star density) before being shown here. A local vision AI then reads the diagnostic plots to give a first verdict. Your personal ML model scores each one based on past decisions.</p></details></div>
    ${cards || '<div class="card empty">No signals under review right now.</div>'}`;
}

function asteroids() {
  const a = D?.asteroids;
  if (!a) { $('#v-asteroids').innerHTML = VIEWER_ERR + '<div class="card"><h3>Planetary defense watch</h3><p>The asteroid data hasn\'t loaded yet.</p></div>'; return; }

  const cad = (a.cad || []).slice(0, 12).map(r => `<tr><td class="mono">${esc(r.cd)}</td><td><b>${esc(r.des)}</b></td><td class="mono">${num(parseFloat(r.dist) / LD_AU, 1)} LD</td><td>${fmtSize(sizeFromH(parseFloat(r.h)))}</td></tr>`).join('');
  const nc = (a.neocp || []).slice(0, 12).map(r => `<tr><td class="mono">${esc(r.name)}</td><td class="mono">${esc(r.neoScore ?? '')}</td><td class="mono">${esc(r.phaScore ?? '')}</td><td class="mono">${esc(r.H ?? '')}</td></tr>`).join('');
  const cands = (a.cands || []).map(c => `<div class="card"><div class="row"><h3>${esc(c.des)} · ${esc(c.night)}</h3><span class="chip">${esc(c.archive)}</span><span class="spacer"></span><span class="chip">${esc(c.status)}</span></div>
    <p class="small muted">A moving dot ${num(c.offset, 0)}″ from JPL's predicted position, in two images from the same night, missing from the deep stack. If confirmed, these old positions would extend the asteroid's observed orbit.</p>
    <div class="row" style="align-items:flex-start">${c.gif ? `<img class="blink" src="${esc(c.gif)}" alt="blink">` : ''}<div style="flex:1;min-width:260px">${c.png ? `<img class="plot" src="${esc(c.png)}" alt="cutouts">` : ''}</div></div>
    <button class="b" data-orbit="${esc(c.des)}">3D orbit</button></div>`).join('');
  const tg = (a.targets || []).map(t => `<tr><td><b>${esc(t.fullname || t.des)}</b></td><td class="mono">${num(t.h, 1)} (${fmtSize(sizeFromH(t.h))})</td><td class="mono">${num(t.arc, 0)} d</td><td class="mono">${t.nimg || 0}</td><td>${t.orbit && t.orbit.a ? `<button class="b" data-orbit="${esc(t.des)}">3D orbit</button>` : ''}</td></tr>`).join('');

  $('#v-asteroids').innerHTML = `<div class="card"><h3>Planetary defense watch</h3><p>${esc(a.text || '')}</p></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">This page tracks near-Earth asteroids and does <b>precovery</b> — searching old Pan-STARRS images to find earlier sightings of known asteroids. Finding older positions extends the asteroid's "observed arc", which refines the orbit calculation and improves impact-risk estimates.</p></details></div>
    <div class="grid g2"><div class="card"><h3>Close approaches, next 30 days</h3><table class="compact"><tr><th>date</th><th>object</th><th>distance</th><th>size*</th></tr>${cad}</table><p class="small muted">LD = lunar distance. *From brightness.</p></div>
    <div class="card"><h3>New objects awaiting confirmation</h3><table class="compact"><tr><th>name</th><th>NEO score</th><th>hazard score</th><th>H</th></tr>${nc || '<tr><td colspan=4 class="muted">—</td></tr>'}</table></div></div>
    <h2>Precovery: possible sightings in old images</h2>${cands || '<div class="card empty">No precovery matches this run. This is normal — most searches find nothing, as these asteroids are very faint and buried in archival noise.</div>'}
    <h2>Asteroids searched</h2><div class="card"><table class="compact"><tr><th>object</th><th>H (size)</th><th>arc</th><th>images</th><th></th></tr>${tg}</table></div>`;
}

function exowatch() {
  const e = D?.exowatch;
  if (!e) { $('#v-exowatch').innerHTML = '<div class="card"><h3>Timing exoplanet transits</h3><p>No Exoplanet Watch data available yet. Wait for the overnight pipeline to run.</p></div>'; return; }

  const res = (e.results || []).map(d => `<div class="card"><div class="row"><h3>${esc(d.planet || '?')}</h3><span class="small muted">${esc(d.date)} · ${esc(d.telescope)}</span><span class="spacer"></span>${d.uploaded ? '<span class="chip ok">sent to AAVSO</span>' : ''}</div>
    ${d.plot ? `<img class="plot" src="${esc(d.plot)}" alt="light curve" loading="lazy">` : ''}
    <div class="kv"><b>Mid-transit time</b><span class="mono">${num(d.tmid, 5)} ± ${num((d.tmid_err || 0) * 1440, 1)} min</span><b>vs prediction</b><span class="mono">${d.oc ? num(d.oc.oc_min, 1) + ' min' : '—'}</span><b>Depth</b><span class="mono">${num(d.depth_pct, 2)} %</span></div>
    <p>${esc(d.explain || '')}</p></div>`).join('');
  const st = (e.stale || []).slice(0, 15).map(t => `<tr><td><b>${esc(t.name)}</b></td><td class="mono">${num(t.vmag, 1)}</td><td class="mono">${num(t.depth_ppt, 1)} ppt</td><td class="mono">±${num(t.sigma_min, 0)} min</td></tr>`).join('');

  $('#v-exowatch').innerHTML = `<div class="card"><h3>Timing exoplanet transits for NASA Exoplanet Watch</h3><p>Measured transit times keep planets' schedules accurate so big telescopes like JWST don't miss them. Results go to the AAVSO Exoplanet Database and into Exoplanet Watch.</p></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">When an exoplanet transits its star, it blocks a tiny fraction of light. By measuring the exact moment of each transit, citizen scientists help keep the planet's ephemeris accurate — so that JWST and other large telescopes point at the right time. This tab shows measurements from MicroObservatory observations, reduced with EXOTIC software.</p></details></div>
    ${res || '<div class="card empty">No transits measured yet.</div>'}
    <h2>Planets whose predicted transit times have drifted most</h2><div class="card"><table class="compact"><tr><th>planet</th><th>star V</th><th>depth</th><th>timing uncertainty now</th></tr>${st || '<tr><td colspan=4 class="muted">—</td></tr>'}</table></div>`;
}

function brief() {
  const b = D?.brief;
  if (!b) { $('#v-brief').innerHTML = VIEWER_ERR + '<div class="card"><h3>Space brief</h3><p>The research summary hasn\'t loaded yet.</p></div>'; return; }

  $('#v-brief').innerHTML = `<div class="card"><h3>Space brief ${esc(b.date || '')}</h3><p class="small muted">New astronomy papers from arXiv, explained in plain English by a local AI.</p></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">A daily script scans arXiv for new astrophysics papers, ranks them by relevance using embeddings (bge-m3), and has a local AI summarize each paper in plain English with an explanation of why it matters for astronomy.</p></details></div>
    ${(b.papers || []).map(p => `<div class="card"><h3><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></h3><div class="small muted">${esc(p.authors)}</div><p>${esc(p.summary || '')}</p>${p.why ? `<p><b>Why it matters:</b> ${esc(p.why)}</p>` : ''}</div>`).join('')}`;
}

function about() {
  const name = D?.name || 'a Space Lab volunteer';
  $('#v-about').innerHTML = `<div class="card"><h3>What is this?</h3>
    <p>Space Lab is a personal citizen-science setup run by ${esc(name)} on a home Mac. Overnight it works through open data from NASA and other observatories:</p>
    <ul><li><b>Planet hunting</b> in NASA TESS light curves, with the standard false-positive checks, a pixel-level centroid test, and a first look from a local vision AI.</li>
    <li><b>Asteroid precovery</b>: searching old Pan-STARRS images for asteroids whose orbits are still short, so their orbits (and impact-risk estimates) can be refined.</li>
    <li><b>Exoplanet Watch</b>: measuring exoplanet transit times for NASA's citizen-science project.</li>
    <li><b>Space brief</b>: new research papers, summarized.</li></ul>
    <p>All AI runs locally. Every result is checked by a person before anything is reported to the AAVSO or the Minor Planet Center. Feedback from scientists is very welcome.</p>
    <p class="small muted">Data: NASA TESS/MAST, NASA Exoplanet Archive, ExoFOP, JPL Small-Body Database, Sentry, Scout and Horizons, CADC SSOIS, Pan-STARRS1 (STScI), Gaia (ESA), arXiv.</p></div>`;
}

const R = { tess, asteroids, exowatch, brief, about };
function setTab(tab) {
  $$('nav.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tab));
  $$('section.view').forEach(v => v.classList.toggle('on', v.id === 'v-' + tab));
  history.replaceState(null, '', '#' + tab);
  R[tab]();
}

document.addEventListener('click', async e => {
  const t = e.target.closest('button, img.plot, #zoom');
  if (!t) return;
  if (t.matches('nav.tabs button')) return setTab(t.dataset.tab);
  if (t.id === 'zoom') { t.style.display = 'none'; return; }
  if (t.id === 'modalClose') { $('#modal').classList.remove('on'); return; }
  if (t.matches('img.plot')) { $('#zoom img').src = t.src; $('#zoom').style.display = 'flex'; return; }

  const d = t.dataset;
  // Fix #1: LC viewer — wrap fetch in try/catch, show errors instead of blank "Loading..."
  if (d.lc) {
    try {
      const body = modal('<div class="card"><p class="muted">Loading light curve…</p><p class="small muted">Fetching data from the server. This may take a moment.</p></div>');
      const resp = await fetch(d.lc);
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      body.innerHTML = `<h3>TIC ${esc(data.tic)}</h3><div id="lcHost"></div>`;
      LCViewer.open($('#lcHost'), data, { period: d.p ? +d.p : 0, t0: +d.t0, duration: +d.dur, depth: +d.dep });
    } catch (err) {
      modal(`<div class="card banner"><h3>⚠️ Failed to load light curve</h3><p>${esc(err.message)} — The data file for TIC ${esc(d.tic)} may be missing or corrupted.</p><button class="b primary" onclick="location.reload()">Reload page</button></div>`);
    }
  }

  // Fix #2: Orbit viewer — added try/catch + better error message
  if (d.orbit) {
    try {
      const tg = (D?.asteroids?.targets || []).find(x => x.des === d.orbit);
      if (!tg || !tg.orbit || typeof tg.orbit.a !== 'number') {
        modal(`<div class="card"><h3>Orbit unavailable</h3><p>No orbit data is included for <b>${esc(d.orbit)}</b> in this copy. The full orbital elements would come from JPL's Small-Body Database.</p></div>`);
        return;
      }
      const body = modal(`<h3>${esc(tg.orbit.name || d.orbit)}</h3><div id="orbHost"></div>`);
      Orbit3D.open($('#orbHost'), tg.orbit);
    } catch (err) {
      modal(`<div class="card banner"><h3>⚠️ Failed to render orbit</h3><p>${esc(err.message)} — The 3D orbit viewer encountered an error.</p><button class="b primary" onclick="location.reload()">Reload page</button></div>`);
    }
  }
});

document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').classList.remove('on'); });

// Fix #4: When data.json fails, show error in ALL tabs + reload button + explanation
function showNetworkError(msg) {
  const errCard = `<div class="card banner"><h3>⚠️ Could not load Space Lab data</h3><p>${esc(msg)} The data.json file may be missing, or the overnight pipeline hasn't run yet.</p><button class="b primary" onclick="location.reload()">Reload page</button></p><details><summary>Why does this happen?</summary><p style="margin:0">Space Lab runs an overnight script that downloads NASA/MAST data, processes it, and generates data.json. If the script fails or hasn't been set up yet, the page won't have data to display.</p></details></div>`;
  $('#updated').textContent = 'error';
  Object.entries(R).forEach(([tab, fn]) => {
    const el = $(`#v-${tab}`);
    if (el) el.innerHTML = errCard;
  });
}

fetch('data.json', { cache: 'no-store' })
  .then(r => r.json())
  .then(d => {
    D = d;
    $('#updated').textContent = `updated ${new Date(d.updated * 1000).toLocaleString()}`;
    const start = (location.hash || '#tess').slice(1);
    setTab(R[start] ? start : 'tess');
  })
  .catch(err => showNetworkError(err?.message || 'Network error'));
