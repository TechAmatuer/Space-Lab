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
const ago = t => { if (!t) return 'never'; const s = Date.now() / 1000 - t; return s < 90 ? 'just now' : s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago'; };
const jdUTC = jd => jd ? new Date((jd - 2440587.5) * 864e5).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
let D = null;

// ---- Extension point for web/extras.js (see EXTRAS.md). Extras can never break the main page.
const HOOKS = [], EXTRA_TABS = {};
// Optional visitor counter (GoatCounter, no cookies; only present when the owner turned it on). Counts tab views and votes.
function track(path, event, tries = 0) {
  try {
    const g = window.goatcounter;
    if (g && g.count) g.count({ path, title: path, event: !!event });
    else if (document.querySelector('script[data-goatcounter]') && tries < 20) setTimeout(() => track(path, event, tries + 1), 500);
  } catch (_) { /* counting must never break the page */ }
}
window.SpaceLab = {
  get data() { return D; },
  esc, num, track,
  // Cloud forecast for a place, only when a visitor asks for it: the free Open-Meteo service gets the place rounded to ~10 km.
  weather: (lat, lon) => fetch(`https://api.open-meteo.com/v1/forecast?latitude=${(+lat).toFixed(1)}&longitude=${(+lon).toFixed(1)}&hourly=cloud_cover&forecast_days=2&timeformat=unixtime&timezone=auto`)
    .then(r => (r.ok ? r.json() : Promise.reject(new Error('weather service: HTTP ' + r.status)))),
  // load a file from this same site (never another site), e.g. the ZIP code table for the dark-sky finder
  load: p => /^[A-Za-z0-9_./-]+(\?v=[0-9]+)?$/.test(p) && !p.includes('..') && !p.startsWith('/')
    ? fetch(p).then(r => (r.ok ? r.text() : Promise.reject(new Error('HTTP ' + r.status)))) : Promise.reject(new Error('only files from this site')),
  // the same, as raw bytes (the 3D universe's galaxy file)
  loadBytes: p => /^[A-Za-z0-9_./-]+(\?v=[0-9]+)?$/.test(p) && !p.includes('..') && !p.startsWith('/')
    ? fetch(p).then(r => (r.ok ? r.arrayBuffer() : Promise.reject(new Error('HTTP ' + r.status)))) : Promise.reject(new Error('only files from this site')),
  onRender(fn) { HOOKS.push(fn); },
  showTic: tic => showTic(tic),                      // the card of TESS candidate `tic`, shown even if it was on a later page                   // fn(tabName, viewElement, data) after every tab render
  addTab(id, title, render) {                          // render(viewElement, data)
    if (R[id] || EXTRA_TABS[id]) return;
    EXTRA_TABS[id] = render;
    const b = document.createElement('button');
    b.setAttribute('role', 'tab'); b.dataset.tab = id; b.setAttribute('aria-selected', 'false'); b.textContent = title;
    $('nav.tabs').insertBefore(b, $('nav.tabs button[data-tab=about]'));
    const s = document.createElement('section'); s.className = 'view'; s.id = 'v-' + id;
    $('main').appendChild(s);
    if (navReady) layoutNav();
    if (D && location.hash === '#' + id) setTab(id);
  },
};
function runExtras(tab) {
  const el = $('#v-' + tab);
  if (EXTRA_TABS[tab]) { try { EXTRA_TABS[tab](el, D); } catch (e) { console.error('extras tab', tab, e); el.innerHTML = '<div class="card empty">This extra is having a problem.</div>'; } }
  for (const fn of HOOKS) { try { fn(tab, el, D); } catch (e) { console.error('extras hook', e); } }
}

// Tabs in groups, so 12 tabs still read clearly. Extras' tabs are slotted in by id; unknown tabs go before About.
const NAV = [['Explore', ['today', 'sky', 'solar', 'atlas', 'universe', 'stargazing', 'brief', 'beyond']], ['Research', ['tess', 'asteroids', 'exowatch', 'results']],
             ['Join in', ['play', 'create', 'mylist']], ['', ['about']]];
let navReady = false;
function layoutNav() {
  navReady = true;
  const nav = $('nav.tabs'), all = $$('nav.tabs button'), byId = Object.fromEntries(all.map(b => [b.dataset.tab, b]));
  const known = new Set(NAV.flatMap(g => g[1])), extra = all.filter(b => !known.has(b.dataset.tab));
  $$('nav.tabs .navgroup').forEach(g => g.remove());
  for (const [label, ids] of NAV) {
    const bs = (label ? [] : extra).concat(ids.map(id => byId[id]).filter(Boolean));
    if (!bs.length) continue;
    if (label) { const g = document.createElement('span'); g.className = 'navgroup'; g.textContent = label; nav.appendChild(g); }
    bs.forEach(b => nav.appendChild(b));
  }
}

function modal(html) { $('#modalBody').innerHTML = html; $('#modal').classList.add('on'); return $('#modalBody'); }

// Fix #1 & #2: Shared error banner for viewer failures (LC + Orbit3D)
const VIEWER_ERR = `<div class="card banner"><h3>⚠️ Failed to load</h3><p>The requested data is missing or the file is corrupted. Reload the page and try again.</p><button class="b primary" onclick="location.reload()">Reload</button></div>`;

function today() {
  const t = D?.today || {}, b = t.brief || {};
  const jobs = (t.jobs || []).map(j => {
    const sched = j.schedule ? (String(j.schedule).startsWith('every') ? esc(j.schedule) : 'daily at ' + esc(j.schedule)) : 'on demand';
    const chip = j.status === 'ok' ? '<span class="chip ok">ok</span>' : j.status === 'running' ? '<span class="chip warn">running now</span>' : j.status ? `<span class="chip">${esc(j.status)}</span>` : '';
    return `<div class="card jobcard"><div class="row"><h3>${esc(j.label)}</h3><span class="spacer"></span>${chip}</div>
      <div class="small muted">${sched} · last run ${ago(j.started)}</div><div style="margin-top:6px">${esc(j.summary || '')}</div></div>`;
  }).join('');
  $('#v-today').innerHTML = `<div class="card hero"><h2 class="hero-t">A home computer hunting for new worlds</h2>
      <p>Space Lab searches open NASA data every day for planets around other stars, old pictures of risky asteroids, and the exact timing of known planets. You can check its finds, play with real data, and see what's in the sky tonight.</p>
      <div class="row"><button class="b primary" data-go="today-daily">🪐 Today's candidate</button><button class="b" data-go="sky">🌙 Tonight's sky</button><button class="b" data-go="play">🎮 Play & review</button><button class="b" data-go="about">How it works</button></div></div>
    <div id="home-slots"></div>
    <div class="card"><div class="row"><h3>☕ Morning brief</h3><span class="spacer"></span><span class="small muted">${ago(b.t)}${b.model ? ' · written by a local AI (' + esc(b.model) + ')' : ''}</span></div>
      <div class="brief" style="white-space:pre-wrap">${esc(b.text || 'The first brief is written after the overnight runs.')}</div></div>
    <h2>What the computer is doing</h2>
    <p class="small muted">${t.always_on ? 'This Mac runs full-time: when nothing is scheduled it keeps hunting through TESS stars and building the paper library.' : 'Jobs run on a schedule.'} Each card is the latest run of one pipeline.</p>
    <div class="grid g2">${jobs || '<div class="card empty">No runs yet.</div>'}</div>`;
}

// Variable stars found in the same light curves (only the ones the owner has looked at are listed)
function injCard(s, img) {
  if (!s || !s.n) return '';
  const lines = l => (l || []).map(x => `<li>${esc(x)}</li>`).join('');
  return `<h2>What this search can find</h2><div class="card"><p class="small">To know what the planet search misses, fake planets are hidden in real TESS light curves and the same search is run on them.
    So far <b>${Number(s.n).toLocaleString()}</b> fake planets, <b>${Number(s.found || 0).toLocaleString()}</b> found at the right period${s.alias ? ` (and ${s.alias} at half or double it)` : ''}.</p>
    ${(s.headline_quiet || []).length ? `<p class="small" style="margin-bottom:2px"><b>Around quiet stars</b> it finds:</p><ul class="small">${lines(s.headline_quiet)}</ul>` : ''}
    ${(s.headline_variable || []).length ? `<p class="small" style="margin-bottom:2px"><b>Around spotted, flaring stars</b> it finds:</p><ul class="small">${lines(s.headline_variable)}</ul>` : ''}
    ${s.plot ? `<img class="plot" src="${img(s.plot)}" alt="Share of fake planets found, by period and depth of the dip" loading="lazy">` : ''}
    <p class="small muted">${esc(s.note || '')}</p></div>`;
}
function varCard(v) {
  if (!v || !v.searched) return '';
  const rows = (v.stars || []).map(s => `<tr><td><a href="https://exofop.ipac.caltech.edu/tess/target.php?id=${s.tic}" target="_blank" rel="noopener">TIC ${s.tic}</a></td><td>${esc(s.type || '')}</td>
    <td class="mono">${num(s.period, 4)} d</td><td class="mono">${num(s.amp, 1)} ppt</td><td>${s.status === 'submitted' ? 'sent to VSX' : 'checked by a person'}${s.ztf === true ? ' · seen by ZTF too' : ''}</td></tr>`).join('');
  return `<h2>Variable stars in the same light curves</h2><div class="card"><p class="small">Every light curve is also searched for stars whose brightness changes in a repeating way: spotted stars turning, pairs of stars eclipsing each other, flares.
    So far <b>${Number(v.searched).toLocaleString()}</b> light curves searched, <b>${Number(v.clear || 0).toLocaleString()}</b> with a clear repeating signal, <b>${Number(v.known || 0).toLocaleString()}</b> of them already in catalogues of variable stars.
    ${v.candidates ? `${v.candidates} possible new variable star(s) wait for a person's look.` : ''}</p>
    ${rows ? `<table class="compact"><tr><th>star</th><th>type</th><th>period</th><th>amplitude</th><th></th></tr>${rows}</table>` : ''}
    ${(v.stars || []).filter(s => s.plot).slice(0, 4).map(s => `<img class="plot" src="${esc(s.plot)}" alt="TIC ${s.tic} folded light curve" loading="lazy">`).join('')}</div>`;
}

// The TESS tab shows the strongest candidates first, a page at a time: 120 full cards with plots are heavy on a phone.
const TESS_PAGE = 20;
let tessShown = TESS_PAGE, wantTic = null;
function showTic(tic) {                                 // make sure a candidate's card is on the page (deep links, "go to" links)
  const all = (D && D.tess && D.tess.cands) || [];
  const k = all.findIndex(c => String(c.tic) === String(tic));
  if (k >= tessShown) {
    tessShown = k + 1;
    if ($('#v-tess').classList.contains('on')) { tess(); runExtras('tess'); }
  }
  return document.getElementById('tic-' + tic);
}

function tess() {
  const t = D?.tess, m = t?.model || {};
  if (t && !t.stats) t.stats = {};
  if (!t) { $('#v-tess').innerHTML = VIEWER_ERR + '<div class="card"><h3>Looking for new planets in NASA TESS data</h3><p>The TESS data hasn\'t loaded yet. It should appear once the overnight pipeline runs.</p></div>'; return; }

  if (wantTic) { const k = t.cands.findIndex(c => 'tic-' + c.tic === wantTic); if (k >= tessShown) tessShown = k + 1; wantTic = null; }
  const rest = t.cands.length - tessShown;
  const more = rest > 0 ? `<div class="row" style="justify-content:center;margin:12px 0"><button class="b" data-tess-more>Show ${Math.min(40, rest)} more candidates (${rest} not shown yet)</button></div>` : '';
  const cards = t.cands.slice(0, tessShown).map(c => {
    const ai = c.ai, cen = c.centroid;
    const what = c.kind === 'periodic' ? `dips every <b class="mono">${num(c.period, 4)} days</b>` : 'one lone dip (period unknown)';
    return `<div class="card" id="tic-${c.tic}" data-tic="${c.tic}"><div class="row"><h3>TIC ${c.tic}</h3><span class="chip">${esc(c.status)}</span>${c.known ? `<span class="chip ok">known: ${esc(c.known)}</span>` : ''}<span class="spacer"></span><span class="small muted">found ${day(c.created)}</span></div>
      <div class="small" style="margin:4px 0 8px">${what} · depth <b class="mono">${Math.round(c.depth * 1e6)} ppm</b> · ${num(c.duration * 24, 1)} h · SNR ${num(c.snr, 1)} · ~<b>${num(c.rp, 1)}× Earth</b> if it is a planet</div>
      ${(Array.isArray(c.flags) ? c.flags : []).map(f => `<span class="chip warn">${esc(f)}</span>`).join('')}
      ${c.plot ? `<img class="plot" src="${esc(c.plot)}" alt="diagnostic plots" loading="lazy">` : ''}
      <p>${esc(c.explain || '')}</p>
      ${ai ? `<div class="note"><b>AI first look</b> (a local vision model read the plot): <b>${esc(ai.verdict)}</b>, ${pct(ai.confidence)}${(ai.reasons || []).length ? ' — ' + ai.reasons.map(esc).join('; ') : ''}</div>` : '<div class="note"><b>Awaiting AI analysis</b> — a local vision model will review the diagnostic plots and add its verdict here.</div>'}
      ${c.model_score !== null && c.model_score !== undefined ? `<div class="note"><b>Volunteer\'s own model</b>: ${pct(c.model_score)} planet-like</div>` : ''}
      ${c.fpp ? `<div class="note"><b>False-positive probability</b> (TRICERATOPS): FPP ${num(c.fpp.fpp, 3)}, nearby-star FPP ${num(c.fpp.nfpp, 4)} — ${esc(c.fpp.verdict)}</div>` : ''}
      ${cen ? `<div class="note"><b>Centroid test</b>: ${esc(cen.verdict)} (offset ${num(cen.offset_arcsec, 0)}″, ${num(cen.signif, 1)}σ)${cen.png ? `<br><img class="thumb plot" src="${esc(cen.png)}" alt="centroid" loading="lazy">` : ''}</div>` : '<div class="note"><b>Awaiting centroid test</b> — checking whether the dip signal comes from the target star or a nearby impostor.</div>'}
      <div class="row">${c.lc ? `<button class="b primary" data-lc="${esc(c.lc)}" data-p="${c.period || ''}" data-t0="${c.t0}" data-dur="${c.duration}" data-dep="${c.depth}">Interactive light curve</button>` : ''}
      <a class="b" href="https://exofop.ipac.caltech.edu/tess/target.php?id=${c.tic}" target="_blank" rel="noopener">ExoFOP ↗</a></div></div>`;
  }).join('');

  $('#v-tess').innerHTML = `<div class="card"><h3>Looking for new planets in NASA TESS data</h3>
    <p>Each night a home Mac downloads TESS light curves of nearby small stars, searches for repeating dips, and runs the standard checks that expose eclipsing binary stars. These are the signals that survived. Most will still turn out to be false alarms; that\'s how planet hunting works. Opinions welcome.</p>
    <div class="kv"><b>Stars checked</b><span>${t.stats.done || 0} (${t.stats.nodata || 0} had no TESS light curve, ${t.stats.error || 0} errors)</span><b>Waiting in line</b><span>${t.stats.queued || 0} cool dwarf stars</span><b>Skipped (already have planets/TOIs)</b><span>${t.stats.known || 0}</span></div>
    ${t.engine ? `<div class="small muted">Last run: ${esc(t.engine.workers)} stars at a time; period search on ${t.engine.engine === 'mlx-gpu' ? 'the GPU (MLX)' : 'the CPU (astropy)'}.</div>` : ''}
    <div class="small muted">Volunteer's own planet model: ${m.ready ? `trained on ${m.n_user} human decisions + ${m.n_auto} automatic labels (${pct(m.acc)} agreement); strongest clues: ${(m.top || []).map(x => esc(x[0])).join(', ')}` : `still learning (needs about ${m.need || 12} labelled signals)`}.</div></div>
    <div class="card"><details><summary><b>How to read the plots</b></summary>
      <p><b>Top left: brightness over time.</b> Each grey dot is the star's brightness (averaged over 30 minutes). The faint <span style="color:#d03b3b">red vertical lines</span> are drawn by the program, not by a person or the AI: they mark where the period search predicts each dip should be. A good candidate has a small dip in the dots under every red line, and the lines don't sit on data gaps or sudden jumps.</p>
      <p><b>Top right: the period search.</b> How strongly each possible repeat interval matches the data. The red line marks the winner; a lone sharp peak is good, a forest of equal peaks is not.</p>
      <p><b>Bottom left: all dips stacked.</b> Every dip lined up on top of each other. The red box is the simple transit model. A flat-bottomed U or box suits a planet; a sharp V often means two stars eclipsing.</p>
      <p><b>Bottom right: odd vs even dips.</b> If odd and even dips have different depths, it's probably an eclipsing binary at twice the period. The dashed line is the brightness halfway between dips and should be flat (a dip there means a secondary eclipse, i.e. a star).</p></details></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">A GPU-based Box Least Squares search finds repeating brightness dips in TESS light curves. Each candidate goes through false-positive checks (depth ratios, centroid test, star density) before being shown here. A local vision AI then reads the diagnostic plots to give a first verdict. Your personal ML model scores each one based on past decisions.</p></details></div>
    ${cards || '<div class="card empty">No signals under review right now.</div>'}${more}${varCard(t.variables)}${injCard(t.injection, p => esc(p))}`;
}

function asteroids() {
  const a = D?.asteroids;
  if (!a) { $('#v-asteroids').innerHTML = VIEWER_ERR + '<div class="card"><h3>Planetary defense watch</h3><p>The asteroid data hasn\'t loaded yet.</p></div>'; return; }

  const cad = (a.cad || []).slice(0, 12).map(r => `<tr><td class="mono">${esc(r.cd)}</td><td><b>${esc(r.des)}</b></td><td class="mono">${num(parseFloat(r.dist) / LD_AU, 1)} LD</td><td>${fmtSize(sizeFromH(parseFloat(r.h)))}</td></tr>`).join('');
  const nc = (a.neocp || []).slice(0, 12).map(r => `<tr><td class="mono">${esc(r.name)}</td><td class="mono">${esc(r.neoScore ?? '')}</td><td class="mono">${esc(r.phaScore ?? '')}</td><td class="mono">${esc(r.H ?? '')}</td></tr>`).join('');
  const cands = (a.cands || []).map(c => `<div class="card" data-pc="${c.id}"><div class="row"><h3>${esc(c.des)} · ${esc(c.night)}</h3><span class="chip">${esc(c.archive)}</span><span class="spacer"></span><span class="chip">${esc(c.status)}</span></div>
    <p class="small muted">${/^SuperCOSMOS/.test(c.archive || '') ? `A possible sighting on an old photographic plate, ${num(c.offset, 1)}″ from JPL's predicted position, measured against Gaia stars. Left: the plate; middle: nearby stars removed; right: the same sky on another date, where it should be gone. It waits for a person's check before anything is sent to the Minor Planet Center.${c.weak ? ' <b>Weak:</b> noise on this plate makes blobs nearly as strong, so this one is probably not real.' : ''}`
      : `A moving dot ${num(c.offset, 0)}″ from JPL's predicted position, in two images from the same night, missing from the deep stack. If confirmed, these old positions would extend the asteroid's observed orbit.`}</p>
    <div class="row" style="align-items:flex-start">${c.gif ? `<img class="blink" src="${esc(c.gif)}" alt="blink">` : ''}<div style="flex:1;min-width:260px">${c.png ? `<img class="plot" src="${esc(c.png)}" alt="cutouts">` : ''}</div></div>
    <button class="b" data-orbit="${esc(c.des)}">3D orbit</button></div>`).join('');
  const tg = (a.targets || []).map(t => `<tr><td><b>${esc(t.fullname || t.des)}</b></td><td class="mono">${num(t.h, 1)} (${fmtSize(sizeFromH(t.h))})</td><td class="mono">${num(t.arc, 0)} d</td><td class="mono">${t.nimg || 0}</td><td>${t.orbit && t.orbit.a ? `<button class="b" data-orbit="${esc(t.des)}">3D orbit</button>` : ''}</td></tr>`).join('');

  $('#v-asteroids').innerHTML = `<div class="card"><h3>Planetary defense watch</h3><p>${esc(a.text || '')}</p></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">This page tracks near-Earth asteroids and does <b>precovery</b> — searching old Pan-STARRS and ZTF images to find earlier sightings of known asteroids. Finding older positions extends the asteroid's "observed arc", which refines the orbit calculation and improves impact-risk estimates.</p></details></div>
    <div class="grid g2"><div class="card"><h3>Close approaches, next 30 days</h3><table class="compact"><tr><th>date</th><th>object</th><th>distance</th><th>size*</th></tr>${cad}</table><p class="small muted">LD = lunar distance. *From brightness.</p></div>
    <div class="card"><h3>New objects awaiting confirmation</h3><table class="compact"><tr><th>name</th><th>NEO score</th><th>hazard score</th><th>H</th></tr>${nc || '<tr><td colspan=4 class="muted">—</td></tr>'}</table></div></div>
    <div class="card"><h3>Highest impact-risk objects (NASA Sentry)</h3><table class="compact"><tr><th>object</th><th>impact chance</th><th>Palermo</th><th>Torino</th><th>years</th></tr>${(a.sentry || []).slice(0, 10).map(r => { const ip = parseFloat(r.ip); return `<tr><td><b>${esc(r.fullname || r.des)}</b></td><td class="mono">${ip ? '1 in ' + Math.round(1 / ip).toLocaleString() : '—'}</td><td class="mono">${esc(r.ps ?? '')}</td><td class="mono">${esc(r.ts_max ?? '')}</td><td class="mono">${esc(r.range || '')}</td></tr>`; }).join('') || '<tr><td colspan=5 class="muted">—</td></tr>'}</table><p class="small muted">Palermo below −2: no cause for concern. Torino 0: no hazard.</p></div>
    <div class="card"><h3>Newly announced near-Earth asteroids: fast precovery check</h3><table class="compact"><tr><th>object</th><th>H</th><th>arc</th><th>checked</th><th>result</th></tr>${(a.new_neos || []).map(t => `<tr><td><b>${esc(t.fullname || t.des)}</b></td><td class="mono">${num(t.h, 1)}</td><td class="mono">${num(t.arc, 1)} d</td><td>${t.searched ? ago(t.searched) : 'queued'}</td><td class="small">${esc(t.note || '')}</td></tr>`).join('') || '<tr><td colspan=5 class="muted">None in the last days.</td></tr>'}</table>
      <p class="small muted">New objects are checked in recent ZTF images first, then in older Pan-STARRS images once their orbits are good enough. NEO confirmation page last checked ${ago(a.neocp_t)}.</p></div>
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
  const st = (e.stale || []).slice(0, 20).map(t => `<tr><td><b>${esc(t.name)}</b>${t.priority ? ` <span class="chip">${esc(t.priority)}</span>` : ''}</td><td class="mono">${num(t.vmag, 1)}</td><td class="mono">${num(t.depth_ppt, 1)} ppt</td><td class="mono">±${num(t.sigma_min, 0)} min</td><td>${jdUTC(t.next_jd)}</td></tr>`).join('');

  $('#v-exowatch').innerHTML = `<div class="card"><h3>Timing exoplanet transits for NASA Exoplanet Watch</h3><p>Measured transit times keep planets' schedules accurate so big telescopes like JWST don't miss them. Results go to the AAVSO Exoplanet Database and into Exoplanet Watch.</p></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">When an exoplanet transits its star, it blocks a tiny fraction of light. By measuring the exact moment of each transit, citizen scientists help keep the planet's ephemeris accurate — so that JWST and other large telescopes point at the right time. This tab shows measurements from MicroObservatory observations, reduced with EXOTIC software.</p></details></div>
    ${res || '<div class="card empty">No transits measured yet.</div>'}
    <div class="card"><h3>You can help too</h3><ol class="steps"><li>Request a free telescope dataset on NASA's <a href="https://science.nasa.gov/citizen-science/exoplanet-watch/how-to-contribute/data-checkout/" target="_blank" rel="noopener">Exoplanet Watch Data Checkout</a>, ideally for a planet in the table below.</li><li>Reduce it with NASA's free EXOTIC software (or run your own Space Lab, it's open source).</li><li>Own a Seestar or Dwarf? Record a transit from the table below and share your frames.</li></ol></div>
    <h2>Planets whose predicted transit times have drifted most</h2><div class="card"><p class="small muted">One new measurement of these helps the most. Priorities and timings from ExoClock (ESA Ariel's monitoring project), updated ${ago(e.ephem_t)}.</p><table class="compact"><tr><th>planet</th><th>star V</th><th>depth</th><th>timing uncertainty now</th><th>next transit (your time)</th></tr>${st || '<tr><td colspan=5 class="muted">—</td></tr>'}</table></div>`;
}

function brief() {
  const b = D?.brief;
  if (!b) { $('#v-brief').innerHTML = VIEWER_ERR + '<div class="card"><h3>Space brief</h3><p>The research summary hasn\'t loaded yet.</p></div>'; return; }

  $('#v-brief').innerHTML = `<div class="card"><h3>Space brief ${esc(b.date || '')}</h3><p class="small muted">New astronomy papers from arXiv, explained in plain English by a local AI.</p></div>
    <div class="card"><details><summary>How this tab works</summary><p style="margin:0">A daily script scans arXiv for new astrophysics papers, ranks them by relevance using embeddings (bge-m3), and has a local AI summarize each paper in plain English with an explanation of why it matters for astronomy.</p></details></div>
    ${libraryCard(b.library || {})}
    ${(b.paper_news || []).length ? `<h2>Research in the news</h2><div class="card" id="paperNews"><p class="small muted">Stories from popular science outlets this week that link to the research paper behind them.</p><ul class="list">${b.paper_news.slice(0, 15).map(n => `<li><span><a href="${esc(n.url)}" target="_blank" rel="noopener"><b>${esc(n.title)}</b></a> <span class="small muted">${esc(n.source)}${n.date ? ' · ' + day(n.date) : ''}</span><br><span class="small">📄 the paper: ${(n.papers || []).map(p => `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title || p.id)}</a>${p.venue ? ` <span class="muted">(${esc(p.venue)}${p.year ? ', ' + p.year : ''})</span>` : ''}`).join(' · ')}</span></span></li>`).join('')}</ul></div>` : ''}
    <h2>New on arXiv</h2>
    ${(b.papers || []).map(p => `<div class="card"><h3><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></h3><div class="small muted">${esc(p.authors)}</div><p>${esc(p.summary || '')}</p>${p.why ? `<p><b>Why it matters:</b> ${esc(p.why)}</p>` : ''}</div>`).join('') || '<div class="card empty">No new papers today (arXiv posts Sunday–Thursday evenings).</div>'}
    ${(b.news || []).length ? `<h2>Elsewhere this week</h2><div class="card"><ul class="list">${b.news.slice(0, 25).map(n => `<li><span><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a><br><span class="small muted">${esc(n.source)}${n.date ? ' · ' + day(n.date) : ''}</span></span></li>`).join('')}</ul></div>` : ''}`;
}

function libraryCard(lib) {
  const y = new Date().getFullYear();
  return `<div class="card"><h3>📚 Search 50 years of astronomy papers</h3>
    <p class="small muted">The volunteer's Mac keeps a library of ${lib.papers ? lib.papers.toLocaleString() + ' papers (' + lib.years[0] + '–' + lib.years[1] + ')' : 'astronomy papers (still building)'} that its local AI searches by meaning. This public page can't run that AI, so the search below uses NASA ADS, the free database astronomers themselves use, with the same filters.</p>
    <div class="row"><input type="text" id="pubQ" placeholder="e.g. hot Jupiter orbital decay" style="flex:1;min-width:240px"><button class="b primary" id="pubGo">Search</button></div>
    <div class="row small" style="margin-top:6px"><label>from <input type="number" id="pubY0" value="1976" style="width:72px"></label><label>to <input type="number" id="pubY1" value="${y}" style="width:72px"></label>
    <label><input type="checkbox" id="pubRef" checked> peer-reviewed only</label></div></div>`;
}

// What we got wrong (spacelab/corrections.py): mistakes found in Space Lab's own work, and how each was fixed
function correctionsCard(list) {
  if (!list.length) return '';
  const row = c => `<li><b>${esc(c.date)} · ${esc(c.area)}.</b> ${esc(c.wrong)}
    <div class="small"><b>What it affected:</b> ${esc(c.effect)} <b>How it was found:</b> ${esc(c.found)} <b>What changed:</b> ${esc(c.fix)}
    ${c.url && /^https:\/\/github\.com\//.test(c.url) ? `<a href="${esc(c.url)}" target="_blank" rel="noopener">the fix ↗</a>` : ''}</div></li>`;
  return `<div class="card" id="corrections"><h3>What we got wrong</h3>
    <p class="small">Mistakes found in Space Lab's own work, newest first: what was wrong, whether it reached this page or a science archive, how it was found and what changed. Nothing is removed from this list.</p>
    <ul class="corr">${list.map(row).join('')}</ul></div>`;
}

function about() {
  const name = D?.name || 'a Space Lab volunteer';
  $('#v-about').innerHTML = `<div class="card"><h3>What is this?</h3>
    <p>Space Lab is a personal citizen-science setup run by ${esc(name)} on a home Mac. Overnight it works through open data from NASA and other observatories:</p>
    <ul><li><b>Planet hunting</b> in NASA TESS light curves, with the standard false-positive checks, a pixel-level centroid test, and a first look from a local vision AI.</li>
    <li><b>Asteroid precovery</b>: searching old Pan-STARRS images for asteroids whose orbits are still short, so their orbits (and impact-risk estimates) can be refined.</li>
    <li><b>Exoplanet Watch</b>: measuring exoplanet transit times for NASA's citizen-science project.</li>
    <li><b>Space brief</b>: new research papers summarized, stories from popular science outlets matched to the papers behind them, and a 50-year paper library.</li></ul>
    <p>This page mirrors the volunteer's private dashboard. Two things stay on the Mac because they need its local AI: asking questions about the results, and searching the library by meaning (the public library search uses NASA ADS instead). Want all of it? Space Lab is free and open source${D?.site?.repo ? `: <a href="https://github.com/${esc(D.site.repo)}" target="_blank" rel="noopener">github.com/${esc(D.site.repo)}</a>` : ''}.</p>
    <p>All AI runs locally. Every result is checked by a person before anything is reported to the AAVSO or the Minor Planet Center. Feedback from scientists is very welcome.</p>
    <p><b>Privacy.</b> No cookies and no accounts. Visits, tabs opened and candidate-of-the-day votes are counted anonymously with GoatCounter. A ZIP code or location you enter stays in your own browser. Only if you tap "Cloud forecast" is the place, rounded to about 10 km, sent to the free Open-Meteo weather service. The Space atlas loads its maps and photos from NASA, ESA, the CDS data centre and jsDelivr, and only after you open it. Reminders are plain calendar files.</p>
    <p class="small muted">Data: NASA TESS/MAST, NASA Exoplanet Archive, ExoClock, ExoFOP, TESS EB catalog, JPL Small-Body Database, Sentry, Scout and Horizons, CADC SSOIS, Pan-STARRS1 (STScI), ZTF (IRSA/Caltech), Gaia (ESA), arXiv, OpenAlex, NASA ADS, and the news outlets linked in the Space brief.</p></div>${correctionsCard((D && D.site && D.site.corrections) || [])}`;
}

function adsSearch() {
  const q = $('#pubQ').value.trim(); if (!q) return;
  const y0 = +$('#pubY0').value || 1976, y1 = +$('#pubY1').value || new Date().getFullYear();
  const full = `${q} year:${y0}-${y1} collection:astronomy${$('#pubRef').checked ? ' property:refereed' : ''}`;
  window.open('https://ui.adsabs.harvard.edu/search/q=' + encodeURIComponent(full) + '&sort=' + encodeURIComponent('score desc'), '_blank', 'noopener');
}

const R = { today, brief, exowatch, tess, asteroids, about };
function setTab(tab) {
  $$('nav.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tab));
  $$('section.view').forEach(v => v.classList.toggle('on', v.id === 'v-' + tab));
  history.replaceState(null, '', '#' + tab);
  track('/' + tab);
  const tb = document.querySelector(`nav.tabs button[data-tab="${tab}"]`); if (tb && tb.scrollIntoView && innerWidth <= 1100) tb.scrollIntoView({ block: 'nearest', inline: 'center' });
  if (R[tab]) R[tab]();
  runExtras(tab);
}

window.addEventListener('hashchange', () => {             // a link to another tab typed, pasted or clicked while the page is open
  const id = (location.hash || '').slice(1);
  const on = document.querySelector('nav.tabs button[aria-selected="true"]');
  if (D && id && (R[id] || EXTRA_TABS[id]) && !(on && on.dataset.tab === id)) setTab(id);
});

document.addEventListener('click', async e => {
  const t = e.target.closest('button, img.plot, #zoom');
  if (!t) return;
  if (t.matches('[data-tess-more]')) { tessShown += 40; tess(); runExtras('tess'); return; }
  if (t.matches('nav.tabs button')) return setTab(t.dataset.tab);
  if (t.dataset.go) { const el = document.getElementById(t.dataset.go); if (el && el.scrollIntoView) return el.scrollIntoView({ behavior: 'smooth', block: 'start' }); return setTab(t.dataset.go); }
  if (t.id === 'pubGo') return adsSearch();
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

document.addEventListener('keydown', e => { if (e.key === 'Escape') $('#modal').classList.remove('on'); if (e.key === 'Enter' && e.target.id === 'pubQ') adsSearch(); });

// Fix #4: When data.json fails, show error in ALL tabs + reload button + explanation
function showNetworkError(msg) {
  const errCard = `<div class="card banner"><h3>⚠️ Could not load Space Lab data</h3><p>${esc(msg)} The data.json file may be missing, or the overnight pipeline hasn't run yet.</p><button class="b primary" onclick="location.reload()">Reload page</button></p><details><summary>Why does this happen?</summary><p style="margin:0">Space Lab runs an overnight script that downloads NASA/MAST data, processes it, and generates data.json. If the script fails or hasn't been set up yet, the page won't have data to display.</p></details></div>`;
  $('#updated').textContent = 'error';
  Object.entries(R).forEach(([tab, fn]) => {
    const el = $(`#v-${tab}`);
    if (el) el.innerHTML = errCard;
  });
}

// start once every extras script has run (they add tabs), so the tab layout and the first tab see all of them
const ready = new Promise(res => document.readyState === 'loading' ? document.addEventListener('DOMContentLoaded', res) : res());
Promise.all([fetch('data.json', { cache: 'no-store' }).then(r => r.json()), ready]).then(([d]) => d)
  .then(d => {
    D = d;
    $('#updated').textContent = `updated ${new Date(d.updated * 1000).toLocaleString()}`;
    layoutNav();
    const start = (location.hash || '#today').slice(1);
    const deep = /^tic-\d+$/.test(start) ? start : null;
    wantTic = deep;
    setTab(deep ? 'tess' : R[start] || EXTRA_TABS[start] ? start : 'today');
    if (deep) setTimeout(() => { const el = document.getElementById(deep); if (el) { el.scrollIntoView({ block: 'start' }); el.classList.add('flash'); } }, 300);
  })
  .catch(err => showNetworkError(err?.message || 'Network error'));
