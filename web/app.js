'use strict';
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const LD_AU = 0.00256955529;
const S = { tab: 'today', state: null, tessFilter: 'new', starClicks: {}, logJob: 'server', focus: null };
if (new URLSearchParams(location.search).get('embed')) document.body.classList.add('embed');

async function api(path, body) {
  const opt = body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-SpaceLab': '1' }, body: JSON.stringify(body) } : {};
  const r = await fetch(path, opt);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ('HTTP ' + r.status));
  return j;
}
// ------------------------------------------------------------------ extras (web/extras.js, web/beyond.js)
// The public page and Meridian run the same extras through window.SpaceLab, so both show the same features.
// The extras read the public data (api/public-data = exactly what GitHub Pages gets) and never write anything here.
const HOOKS = [], EXTRA_TABS = {}, PUB_NAME = { arxiv: 'brief' };   // Meridian tab name -> public tab name
let PUB = null;
async function loadPublicData() {
  try { PUB = await api('api/public-data'); if (EXTRA_TABS[S.tab]) runExtras(S.tab); } catch (e) { console.warn('public data', e); }
}
function runExtras(tab) {
  const el = $('#v-' + tab);
  if (!el) return;
  if (EXTRA_TABS[tab]) { try { EXTRA_TABS[tab](el, PUB); } catch (e) { console.error('extras tab', tab, e); el.innerHTML = '<div class="card empty">This extra is having a problem.</div>'; } }
  for (const fn of HOOKS) { try { fn(PUB_NAME[tab] || tab, el, PUB); } catch (e) { console.error('extras hook', e); } }
}
window.SpaceLab = {
  get data() { return PUB; },
  esc: s => esc(s), num: (x, d) => num(x, d), local: true,
  onRender(fn) { HOOKS.push(fn); },
  addTab(id, title, render) {
    if (EXTRA_TABS[id] || $('#v-' + id)) return;
    EXTRA_TABS[id] = render;
    const b = document.createElement('button');
    b.setAttribute('role', 'tab'); b.dataset.tab = id; b.setAttribute('aria-selected', 'false'); b.textContent = title;
    $('nav.tabs').insertBefore(b, $('nav.tabs button[data-tab=setup]'));
    const s = document.createElement('section'); s.className = 'view'; s.id = 'v-' + id;
    $('main').appendChild(s);
    if (navReady) layoutNav();
  },
  // Meridian only: the local vision AI names the clouds in a sky photo (the public page has no AI)
  cloudCheck: dataUrl => api('api/cloud-check', { image: String(dataUrl).split(',').pop() }),
  // Meridian only: your results log (the public page shows the entries marked public)
  results: {
    list: () => api('api/results'),
    save: (id, r) => api('api/results/' + (id || 0), Object.assign({}, r, { action: 'save' })).then(x => { loadPublicData(); return x; }),
    remove: id => api('api/results/' + id, { action: 'delete' }).then(x => { loadPublicData(); return x; }),
  },
  track() {},          // visitor counting happens only on the public page
};
const NAV = [['Explore', ['today', 'sky', 'arxiv', 'beyond']], ['Research', ['tess', 'asteroids', 'exowatch', 'results']],
             ['Join in', ['play', 'create', 'mylist']], ['', ['setup']]];
let navReady = false;
function layoutNav() {   // same groups as the public page
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
function ago(t) {
  if (!t) return 'never';
  const s = Date.now() / 1000 - t;
  if (s < 90) return 'just now';
  if (s < 3600) return Math.round(s / 60) + ' min ago';
  if (s < 86400 * 1.5) return Math.round(s / 3600) + ' h ago';
  return Math.round(s / 86400) + ' days ago';
}
const when = t => t ? new Date(t * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—';
const num = (x, d = 2) => (x === null || x === undefined || x === '' || isNaN(+x)) ? '—' : (+x).toFixed(d);
const sizeFromH = h => (h === null || h === undefined || isNaN(+h)) ? null : 1329e3 / Math.sqrt(0.14) * Math.pow(10, -h / 5);
const fmtSize = m => m === null ? '?' : m >= 1000 ? (m / 1000).toFixed(1) + ' km' : Math.round(m) + ' m';
const fileUrl = rel => 'files/' + encodeURI(rel || '');
const statusChip = s => `<span class="chip ${s === 'ok' ? 'ok' : s === 'running' ? 'warn' : s ? 'bad' : ''}">${esc(s || 'not run yet')}</span>`;
const PNAME = { arxiv: 'Space brief', exowatch: 'Exoplanet Watch', tess: 'TESS hunter', asteroids: 'Asteroids', setup: 'Setup' };
const pct = x => (x === null || x === undefined) ? '—' : Math.round(x * 100) + '%';
const jdLocal = jd => new Date((jd - 2440587.5) * 86400000).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
const isoLocal = s => new Date(s).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
function modal(html) { $('#modalBody').innerHTML = html; $('#modal').classList.add('on'); return $('#modalBody'); }
function closeModal() { $('#modal').classList.remove('on'); $('#modalBody').innerHTML = ''; }
function md(text) {   // tiny, safe markdown for model answers: escape first, then **bold**, bullets, line breaks
  const lines = esc(text).replace(/\*\*(.+?)\*\*/g, '<b>$1</b>').split('\n');
  let out = '', inList = false;
  for (const ln of lines) {
    const hd = ln.match(/^#{1,4}\s+(.*)/);
    if (hd) { if (inList) { out += '</ul>'; inList = false; } out += `<h4 style="margin:10px 0 4px">${hd[1]}</h4>`; continue; }
    const m = ln.match(/^\s*[-*•]\s+(.*)/);
    if (m) { if (!inList) { out += '<ul>'; inList = true; } out += `<li>${m[1]}</li>`; continue; }
    if (inList) { out += '</ul>'; inList = false; }
    out += ln.trim() ? `<p style="margin:4px 0">${ln}</p>` : '';
  }
  return out + (inList ? '</ul>' : '');
}
const checkList = cs => `<ul class="checks">${cs.map(c => `<li>${c.level === 'ok' ? '✅' : c.level === 'warn' ? '⚠️' : '⛔'} ${esc(c.msg)}</li>`).join('')}</ul>`;

// ------------------------------------------------------------------ tabs
function setTab(tab, focus) {
  S.tab = tab; S.focus = focus || null;
  $$('nav.tabs button').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === tab));
  $$('section.view').forEach(v => v.classList.toggle('on', v.id === 'v-' + tab));
  if (location.hash.slice(1) !== tab) history.replaceState(null, '', '#' + tab + location.search);
  const tb = document.querySelector(`nav.tabs button[data-tab="${tab}"]`); if (tb && tb.scrollIntoView && innerWidth <= 1100) tb.scrollIntoView({ block: 'nearest', inline: 'center' });
  load(tab);
}
async function load(tab) {
  await loadCore(tab);
  runExtras(tab);
}
async function loadCore(tab) {
  try {
    if (EXTRA_TABS[tab]) return;
    if (tab === 'today') return renderToday(S.state || await refreshState());
    if (tab === 'arxiv') return renderArxiv(await api('api/arxiv' + (S.arxivDate ? '?date=' + S.arxivDate : '')));
    if (tab === 'exowatch') { const x = await api('api/exo'); S.lastExo = x.datasets; return renderExo(x); }
    if (tab === 'tess') return renderTess(await api('api/tess'));
    if (tab === 'asteroids') return renderNeo(await api('api/neo'));
    if (tab === 'setup') return renderSetup(await api('api/settings'));
  } catch (e) { $('#v-' + tab).innerHTML = `<div class="card bad-t">Could not load: ${esc(e.message)}</div>`; }
}
function afterRender() {
  if (!S.focus) return;
  const el = document.getElementById(S.focus);
  if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.style.outline = '2px solid var(--accent)'; setTimeout(() => el.style.outline = '', 2500); }
  S.focus = null;
}

// ------------------------------------------------------------------ state + today
async function refreshState() {
  try {
    const st = await api('api/state');
    S.state = st;
    const n = st.tasks.length;
    $('#nTasks').hidden = !n; $('#nTasks').textContent = n;
    const busy = !!st.running;
    $('#status').classList.toggle('busy', busy);
    $('#statusText').textContent = busy ? `running: ${st.jobs[st.running]}` + (st.queue.length ? ` · ${st.queue.length} queued` : '') : 'idle · next jobs tonight';
    if (S.tab === 'today') renderToday(st);
    return st;
  } catch (e) {
    $('#statusText').textContent = 'server not reachable';
    $('#status').classList.add('busy');
  }
}

function taskCard(t) {
  const r = t.ref || {};
  const btns = [];
  if (r.tab && r.tab !== 'today') btns.push(`<button class="b primary" data-open="${esc(r.tab)}" data-id="${esc(r.id ?? '')}">Open</button>`);
  if (r.url) btns.push(`<a class="b" href="${esc(r.url)}" target="_blank" rel="noopener">Website ↗</a>`);
  if (r.file) btns.push(`<a class="b" href="download?path=${encodeURIComponent(r.file)}">Download file</a>`);
  btns.push(`<button class="b good" data-task="${t.id}" data-act="done">Done</button>`);
  btns.push(`<button class="b" data-task="${t.id}" data-act="dismiss" title="Hide this task">✕</button>`);
  return `<div class="card task" data-p="${esc(t.project)}"><div class="bar"></div><div class="body">
    <div class="small muted">${esc(PNAME[t.project] || t.project)} · ${ago(t.created)}</div>
    <h3>${esc(t.title)}</h3><div class="muted">${esc(t.detail || '')}</div></div>
    <div class="actions">${btns.join('')}</div></div>`;
}

function renderToday(st) {
  if (!st) return;
  const b = st.brief;
  const brief = b ? `<div class="card"><div class="row"><h3>☕ Morning brief</h3><span class="spacer"></span><span class="small muted">${ago(b.t)}${b.model ? ' · written by ' + esc(b.model) : ''}</span></div><div class="brief">${esc(b.text)}</div></div>`
    : `<div class="card"><h3>Welcome to Space Lab 🚀</h3><p class="muted">The first runs start automatically. Your morning brief shows up here after the nightly jobs. Start with the Setup tab: add your name, email and AAVSO code.</p></div>`;
  const tasks = st.tasks.length ? st.tasks.map(taskCard).join('') : `<div class="card empty">Nothing needs you right now. ${st.done_today ? `You finished ${st.done_today} task${st.done_today > 1 ? 's' : ''} today. ` : ''}Your computer keeps working overnight.</div>`;
  const runs = Object.keys(st.jobs).map(job => {
    const r = st.runs[job] || {};
    const sv = st.schedule[job];
    const sched = sv ? (String(sv).startsWith('every') ? esc(sv) : `daily at ${esc(sv)}`) : 'on demand';
    const running = st.running === job, queued = st.queue.includes(job);
    return `<div class="card"><div class="row"><h3>${esc(st.jobs[job])}</h3><span class="spacer"></span>${running ? '<span class="chip warn">running now</span>' : queued ? '<span class="chip">queued</span>' : statusChip(r.status)}</div>
      <div class="small muted">${sched} · last run ${r.started ? when(r.started) : 'never'}</div>
      <div style="margin:6px 0 10px">${esc(r.summary || '')}</div>
      <div class="row"><button class="b" data-run="${job}" ${running || queued ? 'disabled' : ''}>Run now</button><button class="b" data-log="${job}">Log</button></div></div>`;
  }).join('');
  if (!$('#todayBody')) {
    $('#v-today').innerHTML = `<div class="card" id="askBox"><div class="row"><h3>💬 Ask Space Lab</h3><span class="spacer"></span><span class="small muted">answered by your local AI from your own results</span></div>
      <div class="row"><input type="text" id="askQ" placeholder="e.g. Which TESS signals look most promising? What's new on the asteroid watch?" style="flex:1;min-width:240px"><button class="b primary" id="askBtn">Ask</button></div>
      <div id="askOut" class="answer"></div></div><div id="todayBody"></div>`;
  }
  const dk = st.disk, co = st.contributions || {};
  const house = `<div class="grid g2"><div class="card"><h3>📊 Your contributions</h3><div class="kv"><b>Stars searched</b><span>${co.stars_searched ?? '—'}</span><b>Candidates you reviewed</b><span>${co.tess_reviewed ?? '—'} (${co.tess_planet_like ?? 0} planet-like)</span><b>Asteroid sightings checked</b><span>${co.asteroid_sightings_checked ?? '—'}</span><b>Exoplanet Watch light curves</b><span>${co.exowatch_reduced ?? '—'}</span><b>Results submitted</b><span>${co.uploads_done ?? '—'}</span></div></div>
    ${st.visitors ? `<div class="card"><h3>👀 Website visitors</h3><p><b>${st.visitors.visitors ?? '—'}</b> visitors in the last ${st.visitors.days} days${st.visitors.events ? `, ${st.visitors.events} votes and clicks` : ''}.</p><a class="b" href="${esc(st.visitors.url)}" target="_blank" rel="noopener">Open your visitor stats ↗</a></div>` : ''}
    <div class="card"><h3>💾 Storage</h3>${dk ? `<div class="kv"><b>Space Lab data</b><span>${dk.total_gb} GB</span><b>Free on this Mac</b><span>${dk.free_gb} GB</span><b>Largest parts</b><span>${(dk.largest || []).map(x => esc(x[0]) + ' ' + x[1] + ' GB').join(', ')}</span></div><p class="small muted">Temporary downloads older than 60 days are cleaned up and the database is backed up every night (7 copies kept in data/backups).</p>` : '<p class="small muted">Measured after the next Sky watch run.</p>'}</div></div>`;
  $('#todayBody').innerHTML = `${brief}<h2>Your tasks (${st.tasks.length})</h2>${tasks}<h2>What your computer does</h2><div class="grid g3">${runs}</div>${house}`;
}

// ------------------------------------------------------------------ arXiv
function renderArxiv(d) {
  S.arxivDate = d.date;
  const opts = d.dates.map(x => `<option ${x === d.date ? 'selected' : ''}>${esc(x)}</option>`).join('');
  const cards = d.papers.map(p => `<div class="card" id="item-arxiv-${esc(p.id)}" style="${p.is_read ? 'opacity:.7' : ''}">
    <div class="row"><span class="mono muted">#${p.rank}</span>${p.project && p.project !== 'none' ? `<span class="chip p-${esc(p.project)}">${esc(p.project.replace('-', ' '))}</span>` : ''}<span class="small muted">${esc(p.cats.split(',')[0])} · ${esc(p.published)}</span><span class="spacer"></span>
    <label class="small"><input type="checkbox" data-read="${esc(p.id)}" ${p.is_read ? 'checked' : ''}> read</label></div>
    <h3><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></h3>
    <div class="small muted" style="margin-bottom:6px">${esc(p.authors)}</div>
    <div>${esc(p.summary)}</div>${p.why ? `<div style="margin-top:6px"><b>Why it matters:</b> ${esc(p.why)}</div>` : ''}
    <details style="margin-top:8px"><summary class="small">Original abstract</summary><p class="small muted">${esc(p.abstract)}</p></details></div>`).join('');
  const lib = d.library || {};
  if (!$('#libBox')) {
    const cats = [['', 'all of astro-ph'], ['astro-ph.EP', 'planets & solar system'], ['astro-ph.SR', 'stars & Sun'], ['astro-ph.GA', 'galaxies'],
      ['astro-ph.CO', 'cosmology'], ['astro-ph.HE', 'high-energy'], ['astro-ph.IM', 'instruments & methods']];
    $('#v-arxiv').innerHTML = `<div id="arxivTop"></div>
      <div class="card" id="libBox"><div class="row"><h3>📚 Library: 50 years of astronomy papers</h3><span class="spacer"></span><span class="small muted" id="libStatus"></span></div>
        <div class="row"><input type="text" id="libQ" placeholder="e.g. hot Jupiter orbital decay; precovery of near-Earth asteroids; TRAPPIST-1 atmospheres" style="flex:1;min-width:260px"><button class="b primary" id="libGo">Search</button></div>
        <div class="row small" style="margin-top:6px"><label>from <input type="number" id="libY0" value="1976" style="width:72px"></label><label>to <input type="number" id="libY1" value="${new Date().getFullYear()}" style="width:72px"></label>
          <label>area <select id="libCat">${cats.map(c => `<option value="${c[0]}">${c[1]}</option>`).join('')}</select></label>
          <label>topic <select id="libTopic"><option value="">any</option>${(lib.topics || []).map(t => `<option>${esc(t)}</option>`).join('')}</select></label>
          <label><input type="checkbox" id="libAI" checked> AI reads the top 15 abstracts and ranks them (about a minute)</label></div>
        <div id="libOut"></div></div>
      <div id="arxivBody"></div>`;
  }
  $('#libStatus').textContent = lib.papers ? `${lib.papers.toLocaleString()} papers (${lib.years[0]}–${lib.years[1]}), ${lib.embedded.toLocaleString()} searchable by meaning${lib.complete ? '' : ' · still downloading'} · ${lib.disk_gb} GB` : 'building: the first download starts with the next library run';
  const news = (d.news && d.news.items) || [];
  const pn = (d.paper_news && d.paper_news.items) || [];
  $('#arxivTop').innerHTML = `<div class="row"><h2 style="margin:4px 0">Space brief</h2><span class="spacer"></span>${d.dates.length ? `<select id="arxivDate">${opts}</select>` : ''}</div>
    <p class="muted small">New astronomy papers from arXiv, ranked against your interests by your local embedding model, explained by your local AI. Edit your interests in Setup.</p>`;
  const tr = (d.tracked || []).slice(0, 10);
  $('#arxivBody').innerHTML = (tr.length ? `<h2>New papers about your targets</h2><div class="card"><ul class="list">${tr.map(h => `<li><span><a href="${esc(h.url)}" target="_blank" rel="noopener"><b>${esc(h.title)}</b></a><br><span class="small muted">mentions ${h.matches.map(esc).join(', ')} · ${ago(h.t)}</span></span></li>`).join('')}</ul></div>` : '') + (pn.length ? `<h2>Research in the news</h2><div class="card"><p class="small muted">Stories from popular science outlets this week that link to the research paper behind them.</p><ul class="list">${pn.slice(0, 15).map(paperNewsLi).join('')}</ul></div>` : '') +
    (cards || '<div class="card empty">No digest yet. It runs every morning (arXiv posts new papers Sunday–Thursday evenings).</div>') +
    (news.length ? `<h2>Elsewhere this week</h2><div class="card"><ul class="list">${news.slice(0, 25).map(n => `<li><span><a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}</a><br><span class="small muted">${esc(n.source)}${n.date ? ' · ' + new Date(n.date * 1000).toLocaleDateString() : ''}</span></span></li>`).join('')}</ul></div>` : '');
  afterRender();
}

// ------------------------------------------------------------------ Exoplanet Watch
function exoCard(d) {
  const P = d.planet && typeof d.planet === 'object' ? d.planet : null;
  const pname = P ? P.pl_name : (d.planet || 'unknown planet');
  const st = d.status;
  const chip = { done: 'ok', uploaded: 'ok', failed: 'bad', needs_stars: 'warn', needs_planet: 'warn', running: 'warn' }[st] || '';
  let body = '';
  if (st === 'done' || st === 'uploaded') {
    const r = d.result || {};
    const oc = r.oc;
    body = `${d.plot ? `<img class="plot" src="${fileUrl(d.plot)}" alt="light curve">` : ''}
      <div class="kv" style="margin:10px 0"><b>Mid-transit time</b><span class="mono">${num(r.tmid, 5)} ± ${num((r.tmid_err || 0) * 1440, 1)} min (BJD_TDB)</span>
      <b>vs prediction (O−C)</b><span class="mono">${oc ? `${num(oc.oc_min, 1)} min (prediction uncertainty ±${num(oc.pred_err_min, 1)} min)` : '—'}</span>
      <b>Transit depth</b><span class="mono">${num(r.depth_pct, 2)} %</span><b>Light-curve scatter</b><span class="mono">${num(r.scatter_pct, 2)} %</span>
      <b>Stars used</b><span>${d.stars ? `${d.stars.comps.length} comparison stars, ${d.stars.auto ? 'picked automatically' : 'picked by you'}` : '—'}</span></div>
      ${(r.notes || []).length ? `<div class="warn-t small">⚠ ${esc(r.notes.join('; '))}</div>` : ''}
      <p>${esc(d.explain || '')}</p>${improve(r)}
      <div class="row">${d.aavso_file ? `<button class="b primary" data-aavso="${d.id}">Upload helper</button><a class="b" href="download?path=${encodeURIComponent(d.aavso_file)}">Download AAVSO file</a>` : ''}
      ${S.exoShared ? `<button class="b" data-share="exo" data-id="${d.id}">${d.shared ? 'Shared ✓' : 'Share to Discord'}</button>` : ''}
      ${st === 'done' ? `<button class="b good" data-exo="${d.id}" data-act="uploaded">I uploaded it</button>` : '<span class="chip ok">uploaded</span>'}
      <button class="b" data-exo="${d.id}" data-act="rerun">Re-run</button><button class="b" data-exo="${d.id}" data-act="discard">Discard</button></div>
      <p class="small muted">Upload: log in at <a href="https://www.aavso.org/" target="_blank" rel="noopener">aavso.org</a>, open the Exoplanet section and upload the AAVSO file. Your result then appears on the Exoplanet Watch results page under your observer code.</p>`;
  } else if (st === 'needs_stars') {
    const clicks = S.starClicks[d.id] || [];
    body = `<p class="warn-t">${esc(d.error || '')}</p><p>Click the <b style="color:#ff5a36">target star</b> first (the planet's star, usually near the middle), then 2–5 <b style="color:#4db3d3">comparison stars</b> of similar brightness. Avoid the edges and very bright stars.</p>
      <div class="clickwrap" data-clickexo="${d.id}"><img src="${fileUrl(d.preview)}" alt="star field">${clicks.map((c, i) => `<span class="mk ${i === 0 ? 't' : ''}" style="left:${c.px}px;top:${c.py}px"></span>`).join('')}</div>
      <div class="row" style="margin-top:8px"><span class="small muted">${clicks.length ? `target + ${Math.max(0, clicks.length - 1)} comparison star(s)` : 'no stars chosen yet'}</span><span class="spacer"></span>
      <button class="b" data-clearstars="${d.id}">Reset</button><button class="b primary" data-savestars="${d.id}" ${clicks.length < 2 ? 'disabled' : ''}>Save and run EXOTIC</button></div>`;
  } else if (st === 'needs_planet') {
    body = `<p class="warn-t">${esc(d.error || '')}</p><div class="row"><input type="text" id="pl-${d.id}" placeholder="e.g. WASP-12 b"><button class="b primary" data-planet="${d.id}">Save</button></div>`;
  } else if (st === 'failed') {
    body = `<p class="bad-t small mono">${esc(d.error || '')}</p><div class="row"><button class="b" data-exo="${d.id}" data-act="rerun">Try again</button><button class="b" data-exo="${d.id}" data-act="discard">Discard</button><button class="b" data-log="exowatch">Log</button></div>`;
  } else {
    body = `<p class="muted">${st === 'running' ? 'EXOTIC is working on it (10–40 minutes).' : 'Waiting for the next Exoplanet Watch run.'}</p>`;
  }
  return `<div class="card" id="item-exowatch-${d.id}"><div class="row"><h3>${esc(pname)}</h3><span class="small muted">${esc(d.obs_date || '')} · ${d.nimg} images · ${esc(d.name)}</span><span class="spacer"></span><span class="chip ${chip}">${esc(st.replace('_', ' '))}</span></div>${body}</div>`;
}

function improve(r) {
  const oc = r && r.oc;
  if (!oc || !oc.pred_err_min || !r.tmid_err) return '';
  const m = r.tmid_err * 1440, p = oc.pred_err_min, n = 1 / Math.sqrt(1 / (p * p) + 1 / (m * m));
  return `<div class="note ok">Before your measurement, this transit's time was predicted to ±${num(p, 1)} min. Yours measured it to ±${num(m, 1)} min, so combined the timing is now known to about ±${num(n, 1)} min${m < p ? ' — a real improvement' : ''}.</div>`;
}
function renderExo(x) {
  S.exoShared = x.shared;
  const eph = x.ephem || {}, plan = x.plan || {};
  const pri = p => p ? `<span class="chip ${p === 'alert' || p === 'high' ? 'warn' : ''}">${esc(p)}</span>` : '';
  const stale = (eph.stale || []).slice(0, 15).map(t => `<tr><td><b>${esc(t.name)}</b> ${pri(t.priority)}</td><td class="mono">${num(t.vmag, 1)}</td><td class="mono">${num(t.depth_ppt, 1)} ppt</td><td class="mono"><b>±${num(t.sigma_min, 0)} min</b></td><td>${jdLocal(t.next_jd)}</td></tr>`).join('');
  const ev = (plan.events || []).slice(0, 20).map(e => `<tr><td>${isoLocal(e.start_utc)}</td><td>${isoLocal(e.mid_utc).split(', ').pop()}</td><td>${isoLocal(e.end_utc).split(', ').pop()}</td><td><b>${esc(e.name)}</b> ${pri(e.priority)}</td><td class="mono">${num(e.depth_ppt, 1)} ppt</td><td class="mono">${num(e.vmag, 1)}</td><td class="mono">±${num(e.sigma_min, 0)} min</td><td class="mono">${e.min_alt}°</td></tr>`).join('');
  const sc = x.telescope || {};
  const scope = `<h2>Your telescope</h2><div class="card">
      <p>Own a Seestar S50 or Dwarf 3? Record a transit from the list below, then drop the night's folder of frames (or a zip) into <span class="mono">${esc(x.telescope_inbox)}</span> <button class="b" data-copy="${esc(x.telescope_inbox)}">Copy path</button>. Space Lab converts colour frames to the green channel, runs EXOTIC with your location, and gives you the same AAVSO upload helper.</p>
      <p class="small muted">Seestar: turn on "Save each frame", 10–20 s exposures, start 1 hour before the transit and stop 1 hour after. Telescope: ${esc(sc.model || 'not set')} · location: ${sc.located ? 'set (private)' : '<span class="warn-t">not set — add it in Setup</span>'}</p>
      <h3>Transits you can observe from home (next 7 days)</h3>
      ${plan.missing_location ? '<p class="muted">Add your telescope and location in Setup, then run "Transit timing targets" on the Today tab.</p>' :
        `<table class="compact"><tr><th>start</th><th>middle</th><th>end</th><th>planet</th><th>depth</th><th>star V</th><th>timing now</th><th>lowest altitude</th></tr>${ev || '<tr><td colspan=8 class="muted">No fully observable transits this week for your telescope.</td></tr>'}</table>`}</div>`;
  $('#v-exowatch').innerHTML = `<h2>NASA Exoplanet Watch</h2>
    <div class="card"><h3>How it works</h3><ol class="steps">
      <li>Request a dataset on the <a href="${esc(x.checkout)}" target="_blank" rel="noopener">Exoplanet Watch Data Checkout</a> page (they email you a download link).</li>
      <li>Drop the zip into <span class="mono">${esc(x.inbox)}</span> <button class="b" data-copy="${esc(x.inbox)}">Copy path</button></li>
      <li>Space Lab notices it within a few minutes, finds the stars, runs NASA's EXOTIC and puts an upload task on your Today tab.</li></ol>
      <div class="small ${x.aavso_code ? 'muted' : 'warn-t'}">AAVSO observer code: ${x.aavso_code ? esc(x.aavso_code) : 'not set yet (Setup tab) — results still work, but credit needs it'}</div></div>
    <h2>Your datasets</h2>${x.datasets.length ? x.datasets.map(exoCard).join('') : '<div class="card empty">No datasets yet.</div>'}
    <h2>Timing targets: planets whose transit times have drifted most</h2><div class="card">
      <p class="small muted">Every planet's predicted transit time gets less certain each orbit. These are the planets MicroObservatory can measure (bright enough, deep enough) whose predictions are now the most uncertain, so one new measurement helps the most. Ask for these when you check out data. Timing and priorities from ExoClock (the ESA Ariel mission's monitoring project, kept up to date); the NASA Exoplanet Archive fills in planets ExoClock doesn't follow. ${eph.t ? 'Updated ' + ago(eph.t) + '.' : ''}</p>
      <table class="compact"><tr><th>planet</th><th>star V</th><th>depth</th><th>timing uncertainty now</th><th>next transit (your time)</th></tr>${stale || '<tr><td colspan=5 class="muted">Runs daily (Transit timing targets).</td></tr>'}</table></div>
    ${scope}`;
  afterRender();
}

// ------------------------------------------------------------------ TESS
function tessCard(c) {
  const flags = (c.flags || []).map(f => `<span class="chip ${/two stars|too big/.test(f) ? 'bad' : 'warn'}">${esc(f)}</span>`).join('');
  const periodic = c.kind === 'periodic';
  const metrics = periodic
    ? `every <b class="mono">${num(c.period, 4)} d</b> · depth <b class="mono">${Math.round(c.depth * 1e6)} ppm</b> · ${num(c.duration * 24, 1)} h · SNR ${num(c.snr, 1)} · SDE ${num(c.sde, 1)} · ${c.ntr} dips · ~<b>${num(c.rp, 1)}× Earth</b>`
    : `one dip · depth <b class="mono">${Math.round(c.depth * 1e6)} ppm</b> · ${num(c.duration * 24, 1)} h · SNR ${num(c.snr, 1)} · ~<b>${num(c.rp, 1)}× Earth</b>`;
  const v = c.status;
  return `<div class="card" id="item-tess-${c.id}" data-tic="${c.tic}"><div class="row"><h3>TIC ${c.tic}</h3><span class="chip">${periodic ? 'repeating dips' : 'lone dip'}</span>${c.known ? `<span class="chip ok">known: ${esc(c.known)}</span>` : ''}<span class="spacer"></span><span class="chip ${v === 'real' ? 'ok' : v === 'new' ? 'warn' : ''}">${esc(v)}</span></div>
    <div class="small" style="margin:4px 0 8px">${metrics}</div><div style="margin-bottom:8px">${flags}</div>
    <img class="plot" src="${fileUrl(c.plot)}" alt="plots" loading="lazy">
    <p>${esc(c.explain || '')}</p>
    ${tessExtras(c)}
    <div class="row"><button class="b good" data-tv="${c.id}" data-v="real">Looks like a planet</button><button class="b bad" data-tv="${c.id}" data-v="fake">Not a planet</button><button class="b" data-tv="${c.id}" data-v="unsure">Unsure</button>
    ${c.lc ? `<button class="b primary" data-lc="${esc(c.lc)}" data-p="${c.period || ''}" data-t0="${c.t0}" data-dur="${c.duration}" data-dep="${c.depth}">Interactive view</button>` : ''}
    ${S.tessShared ? `<button class="b" data-share="tess" data-id="${c.id}">${c.shared ? 'Shared ✓' : 'Share to Discord'}</button>` : ''}<span class="spacer"></span>
    <a href="https://exofop.ipac.caltech.edu/tess/target.php?id=${c.tic}" target="_blank" rel="noopener">ExoFOP page ↗</a></div>
    <details class="small"><summary>What to look for</summary><ul><li>Top left: grey dots are the star's brightness; the red lines are drawn by the period search (not the AI) where it predicts each dip. They should sit on real dips, not on gaps or jumps.</li><li>Top right: the period search; one sharp peak at the red line is good.</li><li>Bottom left: a clean U or box shape. A V shape often means two stars.</li><li>Bottom right: odd and even dips should be equally deep, and the dashed halfway line flat.</li></ul></details></div>`;
}
function tessExtras(c) {
  const out = [];
  const ai = c.ai;
  if (ai) out.push(`<div class="note ${/planet/.test(ai.verdict) ? 'ok' : /binary|noise/.test(ai.verdict) ? 'bad' : 'warn'}"><b>AI first look</b> <span class="small muted">(${esc(ai.model || 'local model')} looked at the plot)</span>: <b>${esc(ai.verdict)}</b>, confidence ${pct(ai.confidence)}${(ai.reasons || []).length ? ' — ' + ai.reasons.map(esc).join('; ') : ''}</div>`);
  if (c.model_score !== null && c.model_score !== undefined) out.push(`<div class="note"><b>Your model</b>: ${pct(c.model_score)} planet-like <span class="small muted">(learned from your past decisions)</span></div>`);
  const fp = c.fpp;
  if (fp) out.push(`<div class="note ${fp.nfpp > 0.1 ? 'bad' : fp.fpp < 0.5 && fp.nfpp < 0.001 ? 'ok' : 'warn'}"><b>False-positive probability</b> (TRICERATOPS): FPP ${num(fp.fpp, 3)}, nearby-star FPP ${num(fp.nfpp, 4)} — ${esc(fp.verdict)}</div>`);
  const ct = c.ctoi;
  if (ct) out.push(`<details class="note ok"><summary><b>Ready to report as a Community TOI?</b> You marked it planet-like and no check argues against it.</summary>
    <p class="small">Check ExoFOP first (is it already a TOI or a known binary?), then fill in the ExoFOP CTOI form with these values:</p>
    <div class="kv mono small"><b>TIC</b><span>${ct.tic}</span><b>Period (days)</b><span>${ct.period_days}</span><b>Epoch (BJD_TDB)</b><span>${ct.epoch_bjd_tdb}</span><b>Depth (ppm)</b><span>${ct.depth_ppm}</span><b>Duration (hours)</b><span>${ct.duration_hours}</span></div>
    <p class="small">Notes: ${esc(ct.notes || '')}</p><a class="b primary" href="${esc(ct.url)}" target="_blank" rel="noopener">Open the ExoFOP CTOI form ↗</a> <span class="small muted">(needs a free ExoFOP login; you submit it yourself)</span>
    <button class="b" data-logresult="ctoi:${ct.tic}" data-kind="ctoi" data-target="TIC ${ct.tic}" data-title="Planet candidate TIC ${ct.tic} reported as a Community TOI">I submitted it: add to Results</button></details>`);
  const cen = c.centroid;
  if (cen) out.push(`<div class="note ${cen.verdict === 'off target' ? 'bad' : cen.verdict === 'on target' ? 'ok' : 'warn'}"><b>Centroid test</b>: ${esc(cen.verdict)} — the dip's light comes from ${num(cen.offset_arcsec, 0)}″ from the star (${num(cen.signif, 1)}σ; signal strength ${num(cen.snr, 0)})${cen.png ? `<br><img class="thumb plot" src="${fileUrl(cen.png)}" alt="centroid test" loading="lazy">` : ''}</div>`);
  return out.join('');
}
function renderTess(x) {
  S.tessShared = x.shared;
  const st = x.stats;
  const filt = S.tessFilter;
  const list = x.cands.filter(c => filt === 'all' ? true : filt === 'new' ? c.status === 'new' : filt === 'reviewed' ? ['real', 'fake', 'unsure'].includes(c.status) : c.status === 'auto-rejected');
  const btn = (k, l) => `<button class="b ${filt === k ? 'primary' : ''}" data-tf="${k}">${l}</button>`;
  $('#v-tess').innerHTML = `<h2>TESS planet hunter</h2>
    <div class="card"><div class="kv"><b>Stars checked</b><span>${st.done || 0} (${st.nodata || 0} had no TESS light curve, ${st.error || 0} errors)</span>
      <b>Waiting in line</b><span>${st.queued || 0} cool dwarf stars</span><b>Skipped (already have planets/TOIs)</b><span>${st.known || 0}</span></div>
      <p class="small muted">Each night your Mac downloads TESS light curves (2-minute data, or full-frame images for stars without it), searches for repeating dips, and runs the standard checks that catch eclipsing binary stars. Survivors get a pixel-level centroid test and an AI first look. Expect mostly false alarms; that's normal.</p>
      ${x.engine ? `<div class="small muted">Last night: ${x.engine.workers} stars at a time; period search on ${x.engine.engine === 'mlx-gpu' ? 'the GPU (MLX)' : 'the CPU (astropy)'}${x.engine.gpu_s ? ` — speed test GPU ${x.engine.gpu_s}s vs CPU ${x.engine.astropy_s}s per star` : ''}.</div>` : ''}
      <div class="small muted">Your planet model: ${x.model && x.model.ready ? `trained on ${x.model.n_user} of your decisions + ${x.model.n_auto} automatic labels (agrees with its training labels ${pct(x.model.acc)}); strongest clues: ${(x.model.top || []).map(t => esc(t[0])).join(', ')}` : `still learning — it needs about ${(x.model && x.model.need) || 12} labelled signals with both planets and non-planets (your clicks count 3×)`}.</div></div>
    <div class="row" style="margin:6px 0 12px">${btn('new', 'To review')}${btn('reviewed', 'Reviewed')}${btn('rejected', 'Auto-rejected')}${btn('all', 'All')}<span class="spacer"></span><a class="b" href="api/export/eclipsing-binaries.csv" title="Signals that look like two stars eclipsing each other: new variable stars you can submit to the AAVSO VSX catalog">Download eclipsing-binary list (CSV)</a></div>
    ${list.length ? list.map(tessCard).join('') : '<div class="card empty">Nothing here yet.</div>'}`;
  afterRender();
}

// ------------------------------------------------------------------ Asteroids
function renderNeo(x) {
  const w = x.watch || {};
  const cad = (w.cad || []).slice(0, 12).map(r => {
    const h = parseFloat(r.h);
    return `<tr><td class="mono">${esc(r.cd)}</td><td><b>${esc(r.des)}</b></td><td class="mono">${num(parseFloat(r.dist) / LD_AU, 1)} LD</td><td>${fmtSize(sizeFromH(h))}</td><td class="mono">${num(r.v_rel, 1)} km/s</td></tr>`;
  }).join('');
  const sen = (w.sentry || []).slice(0, 10).map(r => {
    const ip = parseFloat(r.ip);
    return `<tr><td><b>${esc(r.fullname || r.des)}</b></td><td class="mono">${ip ? '1 in ' + Math.round(1 / ip).toLocaleString() : '—'}</td><td class="mono">${esc(r.ps ?? r.ps_cum ?? '')}</td><td class="mono">${esc(r.ts_max ?? '')}</td><td class="mono">${esc(r.range || '')}</td><td>${r.diameter ? esc(r.diameter) + ' km' : fmtSize(sizeFromH(parseFloat(r.h)))}</td></tr>`;
  }).join('');
  const sc = (w.scout || []).slice().sort((a, b) => (+b.neoScore || 0) - (+a.neoScore || 0)).slice(0, 10).map(r =>
    `<tr><td class="mono">${esc(r.objectName)}</td><td class="mono">${esc(r.neoScore)}</td><td class="mono">${esc(r.H)}</td><td class="mono">${esc(r.arc)} d</td><td class="mono">${esc(r.nObs)}</td></tr>`).join('');
  const cands = x.cands.map(c => {
    const obs = (c.obs || []).map(o => `<tr><td class="mono">${esc(o.obsTime)}</td><td class="mono">${num(o.ra, 5)}</td><td class="mono">${num(o.dec, 5)}</td><td>${esc(o.filter)}</td><td class="mono">${num(o.snr, 1)}</td></tr>`).join('');
    const real = c.status === 'real';
    return `<div class="card" id="item-asteroids-${c.id}" data-pc="${c.id}"><div class="row"><h3>${esc(c.des)} · ${esc(c.night)}</h3><span class="chip">${esc(c.archive)}</span><span class="spacer"></span><span class="chip ${real ? 'ok' : c.status === 'new' ? 'warn' : ''}">${esc(c.status)}</span></div>
      <p class="small muted">The dot sits ${num(c.offset, 0)}″ from where JPL's orbit predicts (inside the 3-sigma search area). A real asteroid shows up in both night images, moves slightly between them, and is missing from the deep stack.</p>
      <div class="row" style="align-items:flex-start"><img class="blink" src="${fileUrl(c.gif)}" alt="blink"><div style="flex:1;min-width:280px"><img class="plot" src="${fileUrl(c.png)}" alt="cutouts"></div></div>
      <table style="margin-top:8px"><tr><th>time (UTC)</th><th>RA°</th><th>Dec°</th><th>filter</th><th>SNR</th></tr>${obs}</table>
      <div class="row" style="margin-top:10px"><button class="b good" data-pv="${c.id}" data-v="real">Yes, same moving dot</button><button class="b bad" data-pv="${c.id}" data-v="fake">No / artifact</button><button class="b" data-pv="${c.id}" data-v="unsure">Unsure</button><span class="spacer"></span>
      ${real ? `<button class="b" data-check="${c.id}">Check the report</button><a class="b primary" href="api/pc/${c.id}/ades">Download draft MPC report</a><button class="b" data-logresult="mpc:${c.id}" data-kind="mpc" data-target="${esc(c.des)}" data-title="Precovery positions of ${esc(c.des)} from ${esc(c.night)} (${esc(c.archive)})">I submitted it: add to Results</button>` : ''}
      <button class="b" data-orbit="${esc(c.des)}">3D orbit</button>${x.shared ? `<button class="b" data-share="pc" data-id="${c.id}">${c.shared ? 'Shared ✓' : 'Share to Discord'}</button>` : ''}</div>
      ${real ? `<p class="small ${x.sarc_verified ? 'muted' : 'warn-t'}">${x.sarc_verified ? 'You are SARC-verified: check the draft, then submit it on the MPC ADES PSV submission page.' : 'Before sending: the MPC requires a one-time verification by its archival committee (SARC). See the task on your Today tab.'}</p>` : ''}</div>`;
  }).join('');
  const nc = (x.neocp || []).slice().sort((a, b) => (+b.neoScore || 0) - (+a.neoScore || 0)).slice(0, 15).map(r =>
    `<tr><td class="mono">${esc(r.name)}</td><td class="mono">${esc(r.neoScore ?? '')}</td><td class="mono">${esc(r.phaScore ?? '')}</td><td class="mono">${esc(r.H ?? '')}</td><td class="mono">${esc(r.nObs ?? '')}</td><td class="mono">${esc(r.arc ?? '')}</td><td>${ago(r.first)}</td></tr>`).join('');
  const fresh = (x.new_neos || []).map(t => `<tr><td><b>${esc(t.fullname || t.des)}</b></td><td class="mono">${num(t.h, 1)}</td><td class="mono">${num(t.arc, 1)} d</td><td>${t.searched ? ago(t.searched) : 'queued'}</td><td class="small">${esc(t.note || '')}</td><td><button class="b" data-orbit="${esc(t.des)}">3D orbit</button></td></tr>`).join('');
  const tg = x.targets.map(t => `<tr><td><b>${esc(t.fullname || t.des)}</b> <button class="b" data-orbit="${esc(t.des)}" style="padding:1px 6px;font-size:11px">3D</button></td><td class="mono">${num(t.h, 1)} (${fmtSize(sizeFromH(t.h))})</td><td class="mono">${num(t.arc, 0)} d</td><td class="mono">${num(t.ps, 2)}</td><td>${t.searched ? ago(t.searched) : 'not yet'}</td><td class="mono">${t.nimg || 0}</td><td class="small">${esc(t.note || '')}${t.other && Object.keys(t.other).length ? `<br><span class="muted">also in: ${Object.entries(t.other).map(([k, v]) => `${esc(k)} (${v.n})`).join(', ')}</span>` : ''}</td></tr>`).join('');
  $('#v-asteroids').innerHTML = `<h2>Planetary defense watch</h2>
    <div class="card"><div class="row"><h3>Today</h3><span class="spacer"></span><span class="small muted">updated ${ago(w.t)}</span></div><p>${esc((x.text && x.text.text) || 'Runs nightly; first update soon.')}</p></div>
    <div class="grid g2"><div class="card"><h3>Close approaches, next 30 days</h3><table><tr><th>date (TDB)</th><th>object</th><th>distance</th><th>size*</th><th>speed</th></tr>${cad || '<tr><td colspan=5 class="muted">—</td></tr>'}</table><p class="small muted">LD = lunar distance (384,400 km). *Size estimated from brightness.</p></div>
    <div class="card"><h3>New objects awaiting confirmation (Scout)</h3><table><tr><th>temp. name</th><th>NEO score</th><th>H</th><th>arc</th><th>obs</th></tr>${sc || '<tr><td colspan=5 class="muted">—</td></tr>'}</table></div></div>
    <div class="card"><h3>Highest impact-risk objects (Sentry)</h3><table><tr><th>object</th><th>impact chance</th><th>Palermo</th><th>Torino</th><th>years</th><th>size</th></tr>${sen || '<tr><td colspan=6 class="muted">—</td></tr>'}</table><p class="small muted">Palermo below −2: no cause for concern. Torino 0: no hazard.</p></div>
    <h2>NEO confirmation watch</h2>
    <div class="card"><div class="row"><h3>On the MPC confirmation page now</h3><span class="spacer"></span><span class="small muted">checked ${ago(x.neocp_t)} · every 2 hours</span></div>
      <table class="compact"><tr><th>temp. name</th><th>NEO score</th><th>hazard score</th><th>H</th><th>obs</th><th>arc (d)</th><th>first seen</th></tr>${nc || '<tr><td colspan=7 class="muted">Runs every 2 hours.</td></tr>'}</table>
      <p class="small muted">Scores from JPL Scout (0–100). You get a private Discord alert when a new object could be hazardous (Scout hazard score 50+ or an impact rating).</p></div>
    <div class="card"><h3>Newly announced near-Earth asteroids: fast precovery check</h3><table class="compact"><tr><th>object</th><th>H</th><th>arc</th><th>checked</th><th>result</th><th></th></tr>${fresh || '<tr><td colspan=6 class="muted">None in the last days yet.</td></tr>'}</table>
      <p class="small muted">Old Pan-STARRS images (2010–2014) can only be searched once an orbit is good enough to say where the asteroid was back then; brand-new objects often aren't there yet, so Space Lab re-checks them as their orbits improve.</p></div>
    <h2>Precovery hunter: sightings to verify</h2>
    ${x.sarc_verified ? '' : `<div class="card banner"><b>One-time step before sending any report:</b> the Minor Planet Center requires archival submitters to be verified by its SARC committee. Space Lab still searches and lets you verify sightings in the meantime.</div>`}
    ${cands || '<div class="card empty">No possible sightings yet. Most searches find nothing; that is normal.</div>'}
    <h2>Asteroids being searched</h2><div class="card"><table><tr><th>object</th><th>H (size)</th><th>observed arc</th><th>Palermo</th><th>last search</th><th>images</th><th>result</th></tr>${tg || '<tr><td colspan=7 class="muted">Targets are picked on the first night.</td></tr>'}</table></div>`;
  afterRender();
}

// ------------------------------------------------------------------ Setup
function renderSetup(cfg) {
  const st = (S.state && S.state.selftest) || null;
  const sched = Object.entries(cfg.schedule).map(([k, v]) => `<label class="f" style="display:inline-block;margin-right:16px"><span>${esc((S.state && S.state.jobs[k]) || k)}</span><input type="${String(v).startsWith('every') ? 'text' : 'time'}" data-sched="${esc(k)}" value="${esc(v)}" ${String(v).startsWith('every') ? 'style="width:90px"' : ''}></label>`).join('');
  const tests = st ? `<table><tr><th>service</th><th>status</th><th>detail</th></tr>${st.net.map(c => `<tr><td>${esc(c.name)}</td><td>${c.ok ? '<span class="ok-t">OK</span>' : '<span class="bad-t">unreachable</span>'}</td><td class="small mono">${esc(c.msg)}</td></tr>`).join('')}
      <tr><td>Local AI writer</td><td>${st.llm.writer_model ? '<span class="ok-t">OK</span>' : '<span class="bad-t">none answered</span>'}</td><td class="small mono">${esc(st.llm.writer_model || '')} ${st.llm.models ? '· installed: ' + esc(st.llm.models.join(', ')) : ''}</td></tr>
      <tr><td>Local embeddings (bge-m3)</td><td>${st.llm.embed ? '<span class="ok-t">OK</span>' : '<span class="bad-t">missing</span>'}</td><td></td></tr>
      <tr><td>Old-image search (SSOIS)</td><td>${String(st.ssois).startsWith('ok') ? '<span class="ok-t">OK</span>' : '<span class="bad-t">check</span>'}</td><td class="small mono">${esc(st.ssois || '')}</td></tr>
      ${Object.entries(st.python).map(([k, v]) => `<tr><td>${esc(k)}</td><td>${String(v).startsWith('missing') ? '<span class="bad-t">missing</span>' : '<span class="ok-t">OK</span>'}</td><td class="small mono">${esc(v)}</td></tr>`).join('')}</table>
      <p class="small muted">Tested ${ago(st.t)}</p>` : '<p class="muted">Not run yet.</p>';
  const jobs = ['server'].concat(Object.keys((S.state && S.state.jobs) || {}));
  $('#v-setup').innerHTML = `<h2>Your details</h2><div class="card">
      <label class="f"><span>Your name (for credit on AAVSO and MPC reports)</span><input type="text" id="sName" value="${esc(cfg.observer_name)}"></label>
      <label class="f"><span>Email (goes only into reports you send yourself)</span><input type="email" id="sEmail" value="${esc(cfg.contact_email)}"></label>
      <label class="f"><span>AAVSO observer code (free account at aavso.org)</span><input type="text" id="sAavso" value="${esc(cfg.aavso_code)}"></label>
      <label class="f"><input type="checkbox" id="sSarc" ${cfg.sarc_verified ? 'checked' : ''}> The Minor Planet Center's SARC committee has verified me for archival (precovery) reports</label>
      <button class="b primary" id="saveProfile">Save</button> <span id="saved1" class="small ok-t"></span></div>
    <h2>Discord alerts (optional)</h2><div class="card">
      <p class="small muted">Get a message in your own Discord channel when something needs you (a candidate to review, a dataset ready to upload) and your morning brief. In Discord: Server Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL, then paste it here.</p>
      <label class="f"><span>Webhook URL</span><input type="text" id="sHook" value="${esc(cfg.discord_webhook || '')}" placeholder="https://discord.com/api/webhooks/…" style="width:100%;max-width:640px"></label>
      <div class="row"><label class="small"><input type="checkbox" id="aTasks" ${(cfg.discord_alerts || {}).tasks !== false ? 'checked' : ''}> new things to review</label>
      <label class="small"><input type="checkbox" id="aBrief" ${(cfg.discord_alerts || {}).brief !== false ? 'checked' : ''}> morning brief</label>
      <label class="small"><input type="checkbox" id="aErr" ${(cfg.discord_alerts || {}).errors !== false ? 'checked' : ''}> problems</label></div>
      <div class="row" style="margin-top:8px"><button class="b primary" id="saveHook">Save</button><button class="b" id="testHook">Send test message</button><span id="saved4" class="small"></span></div></div>
    <h2>Your telescope (private)</h2><div class="card">
      <p class="small muted">Used only on this Mac, to plan transits you can see and to tell EXOTIC where you observed from. Never shared or published.</p>
      <div class="row"><label class="f"><span>Telescope</span><select id="scModel">${['', 'Seestar S50', 'Dwarf 3'].map(m => `<option value="${m}" ${(cfg.telescope || {}).model === m ? 'selected' : ''}>${m || 'none yet'}</option>`).join('')}</select></label>
      <label class="f"><span>Latitude (°, north +)</span><input type="text" id="scLat" value="${esc((cfg.telescope || {}).lat || '')}" placeholder="40.00" style="width:110px"></label>
      <label class="f"><span>Longitude (°, east +; US is −)</span><input type="text" id="scLon" value="${esc((cfg.telescope || {}).lon || '')}" placeholder="-105.00" style="width:110px"></label>
      <label class="f"><span>Elevation (m)</span><input type="number" id="scElev" value="${esc((cfg.telescope || {}).elev ?? 50)}" style="width:90px"></label></div>
      <p class="small muted">Two decimals (about 1 km) are plenty.</p>
      <button class="b primary" id="saveScope">Save</button> <span id="saved5" class="small ok-t"></span></div>
    <h2>Sharing with friends and scientists</h2><div class="card">
      <p class="small muted">Shares science results only. Never shared: your name/email/AAVSO code from above, webhooks, tokens, your telescope location, tasks, logs or file paths. Everything is scanned before upload; if anything private shows up, nothing is published.</p>
      <label class="f"><span>Public name (shown on the page and posts; leave blank for "a Space Lab volunteer")</span><input type="text" id="shName" value="${esc((cfg.share || {}).public_name || '')}" placeholder="e.g. Alex R." style="max-width:320px"></label>
      <label class="f"><span>Public science channel webhook (a different Discord channel from your private alerts)</span><input type="text" id="shHook" value="${esc((cfg.share || {}).discord_webhook || '')}" placeholder="https://discord.com/api/webhooks/…" style="width:100%;max-width:640px"></label>
      <label class="small"><input type="checkbox" id="shSummary" ${(cfg.share || {}).post_summary !== false ? 'checked' : ''}> post a short nightly summary there</label>
      <h3 style="margin-top:14px">Public web page (free GitHub Pages)</h3>
      <ol class="small muted steps"><li>Create a free account at github.com and a new <b>public</b> repository, e.g. <span class="mono">spacelab</span>.</li>
      <li>GitHub → Settings → Developer settings → Fine-grained tokens → Generate: only that repository; permissions <b>Contents: Read and write</b> and <b>Pages: Read and write</b>.</li>
      <li>Paste both below, Save, then Publish now. <b>No token needed</b> if this Mac can already push to GitHub with git (Space Lab uses your Mac's GitHub login first).</li></ol>
      <div class="row"><label class="f"><span>Repository (yourname/spacelab)</span><input type="text" id="shRepo" value="${esc((cfg.share || {}).github_repo || '')}" style="width:220px"></label>
      <label class="f"><span>Token</span><input type="password" id="shToken" value="${esc((cfg.share || {}).github_token || '')}" style="width:260px"></label></div>
      <label class="small"><input type="checkbox" id="shPublish" ${(cfg.share || {}).publish !== false ? 'checked' : ''}> keep the page updated (several times a day; only changes are uploaded)</label>
      <h3 style="margin-top:14px">Your own web address (optional)</h3>
      <p class="small muted">Buy a domain (about $12 a year), point it at GitHub Pages (GitHub → your repository → Settings → Pages → Custom domain), then enter it here so link previews and the feed use it. Blank = the free github.io address.</p>
      <label class="f"><span>Website address</span><input type="text" id="shSite" value="${esc((cfg.share || {}).site_url || '')}" placeholder="https://www.example.com/" style="width:320px"></label>
      <h3 style="margin-top:14px">Visitor counter (optional, no cookies)</h3>
      <ol class="small muted steps"><li>Sign up free at goatcounter.com and pick a code, e.g. <span class="mono">spacelab</span> (your stats live at spacelab.goatcounter.com).</li>
      <li>Enter the code below. The public page then counts visits, tabs opened and the candidate-of-the-day votes, with no cookies and no personal data.</li>
      <li>Optional: in GoatCounter → your name → API, create a key with "Read statistics" and paste it here. This Mac then reads the vote totals for the candidate of the day and your weekly visitors. The key is never published.</li></ol>
      <div class="row"><label class="f"><span>GoatCounter code</span><input type="text" id="shGc" value="${esc((cfg.share || {}).goatcounter || '')}" placeholder="spacelab" style="width:180px"></label>
      <label class="f"><span>GoatCounter API key</span><input type="password" id="shGcKey" value="${esc((cfg.share || {}).goatcounter_token || '')}" style="width:260px"></label></div>
      <div class="row" style="margin-top:8px"><button class="b primary" id="saveShare">Save</button><button class="b" data-run="publish">Publish now</button><span id="saved6" class="small"></span></div>
      <div id="pubStatus" class="small muted" style="margin-top:6px"></div></div>
    <h2>Schedule</h2><div class="card">${sched}<div class="row"><label class="f"><span>TESS stars per night</span><input type="number" id="sTessN" min="5" max="1000" value="${esc(cfg.tess.max_targets)}"></label>
      <label class="f"><span>TESS stars at a time (0 = automatic)</span><input type="number" id="sTessW" min="0" max="12" value="${esc(cfg.tess.workers || 0)}"></label>
      <label class="f"><span>Asteroids per night</span><input type="number" id="sPcN" min="1" max="30" value="${esc(cfg.precovery.max_targets)}"></label></div>
      <label class="f" style="margin-top:8px"><input type="checkbox" id="sAlways" ${cfg.always_on ? 'checked' : ''}> <b>Full-time mode</b>: keep this Mac awake and keep hunting (TESS stars and the paper library run back-to-back whenever nothing else is scheduled). Keep the Mac plugged in; closing a laptop lid still puts it to sleep.</label>
      <button class="b primary" id="saveSched">Save</button> <span id="saved2" class="small ok-t"></span>
      <p class="small muted">Jobs run one at a time, at night, so they never compete with you or with Meridian for memory.</p></div>
    <h2>Space brief interests</h2><div class="card"><label class="f"><span>One interest per line. Papers are ranked by how close they are in meaning (local bge-m3 embeddings).</span><textarea id="sInterests">${esc(cfg.arxiv.interests.join('\n'))}</textarea></label>
      <button class="b primary" id="saveInterests">Save</button> <span id="saved3" class="small ok-t"></span></div>
    <h2>Self-test</h2><div class="card"><div class="row"><span class="muted small">Checks every science website, your local models and the EXOTIC install.</span><span class="spacer"></span><button class="b" data-run="selftest">Run self-test</button></div>${tests}</div>
    <h2>Logs</h2><div class="card"><div class="row"><select id="logJob">${jobs.map(k => `<option ${k === S.logJob ? 'selected' : ''} value="${k}">${esc(k)}</option>`).join('')}</select><button class="b" id="refreshLog">Refresh</button><span class="spacer"></span><button class="b bad" id="stopJob">Stop running job</button></div><pre class="log" id="logText">loading…</pre></div>`;
  loadLog();
  api('api/publish').then(p => {
    const el = $('#pubStatus'); if (!el) return;
    const last = p.last;
    el.innerHTML = last ? `Last published ${ago(last.t)}: <a href="${esc(last.url)}" target="_blank" rel="noopener">${esc(last.url)}</a>${last.note ? `<br><span class="warn-t">${esc(last.note)}</span>` : ''}` :
      (p.run ? `Last attempt ${ago(p.run.started)}: ${esc(p.run.summary || '')}` : 'Not published yet.');
  }).catch(() => {});
}
async function loadLog() {
  const el = $('#logText');
  if (!el) return;
  const d = await api('api/logs?job=' + encodeURIComponent(S.logJob));
  el.textContent = d.text || '(empty)';
  el.scrollTop = el.scrollHeight;
}

// ------------------------------------------------------------------ events
document.addEventListener('click', async e => {
  const t = e.target.closest('button, input[type=checkbox], img.plot, #zoom, .clickwrap img');
  if (!t) return;
  try {
    if (t.matches('nav.tabs button')) return setTab(t.dataset.tab);
    if (t.id === 'zoom') { t.style.display = 'none'; return; }
    if (t.id === 'modalClose') return closeModal();
    if (t.matches('img.plot')) { $('#zoom img').src = t.src; $('#zoom').style.display = 'flex'; return; }
    if (t.matches('.clickwrap img')) {
      const id = t.parentElement.dataset.clickexo;
      const r = t.getBoundingClientRect();
      const px = e.clientX - r.left, py = e.clientY - r.top;
      const x = Math.round(px * t.naturalWidth / r.width), y = Math.round(py * t.naturalHeight / r.height);
      (S.starClicks[id] = S.starClicks[id] || []).push({ x, y, px, py });
      return load('exowatch');
    }
    const d = t.dataset;
    if (d.open !== undefined) return setTab(d.open, d.id ? `item-${d.open}-${d.id}` : null);
    if (d.logresult) {
      await api('api/results/0', { action: 'save', key: d.logresult, kind: d.kind, target: d.target, title: d.title, status: 'submitted' });
      t.textContent = 'In Results ✓'; t.disabled = true; loadPublicData(); return;
    }
    if (d.task) { await api('api/task/' + d.task, { action: d.act }); return refreshState(); }
    if (d.run) { await api('api/run/' + d.run, {}); t.disabled = true; return refreshState(); }
    if (d.log) { S.logJob = d.log; return setTab('setup'); }
    if (d.read) { await api('api/arxiv/' + encodeURIComponent(d.read), { read: t.checked }); return; }
    if (d.tf) { S.tessFilter = d.tf; return load('tess'); }
    if (d.tv) { await api('api/tess/' + d.tv, { verdict: d.v }); refreshState(); return load('tess'); }
    if (d.pv) { await api('api/pc/' + d.pv, { verdict: d.v }); refreshState(); return load('asteroids'); }
    if (d.exo) {
      if (d.act === 'discard' && !confirm('Discard this dataset from the list? (files stay on disk)')) return;
      await api('api/exo/' + d.exo, { action: d.act }); closeModal(); refreshState(); return load('exowatch');
    }
    if (d.clearstars) { S.starClicks[d.clearstars] = []; return load('exowatch'); }
    if (d.savestars) {
      const c = S.starClicks[d.savestars] || [];
      await api('api/exo/' + d.savestars, { action: 'stars', target: [c[0].x, c[0].y], comps: c.slice(1).map(p => [p.x, p.y]) });
      S.starClicks[d.savestars] = []; refreshState(); return load('exowatch');
    }
    if (d.planet) { await api('api/exo/' + d.planet, { action: 'planet', name: $('#pl-' + d.planet).value }); return load('exowatch'); }
    if (d.copy) { await navigator.clipboard.writeText(d.copy); t.textContent = 'Copied'; return; }
    if (d.lc) {
      const body = modal('<p class="muted">Loading light curve…</p>');
      const data = await (await fetch(fileUrl(d.lc))).json();
      body.innerHTML = `<h3>TIC ${esc(data.tic)}</h3><div id="lcHost"></div>`;
      return LCViewer.open($('#lcHost'), data, { period: d.p ? +d.p : 0, t0: +d.t0, duration: +d.dur, depth: +d.dep });
    }
    if (d.orbit) {
      const body = modal('<p class="muted">Getting the orbit from JPL…</p>');
      const o = await api('api/orbit?des=' + encodeURIComponent(d.orbit));
      if (!o.a) { body.innerHTML = '<p class="bad-t">No orbit available.</p>'; return; }
      body.innerHTML = `<h3>${esc(o.name)}</h3><div id="orbHost"></div>`;
      return Orbit3D.open($('#orbHost'), o);
    }
    if (d.check) {
      const body = modal('<p class="muted">Checking the report (and asking the MPC about duplicates)…</p>');
      const r = await api(`api/pc/${d.check}/check`);
      body.innerHTML = `<h3>Report checks</h3>${checkList(r.checks)}<p class="small muted">Fix anything marked ⛔ before sending. ⚠️ items are worth a second look.</p>`;
      return;
    }
    if (d.aavso) {
      const body = modal('<p class="muted">Checking the AAVSO file…</p>');
      const r = await api(`api/exo/${d.aavso}/aavso`);
      const ds = (S.lastExo || []).find(x => String(x.id) === String(d.aavso)) || {};
      body.innerHTML = `<h3>Upload to the AAVSO Exoplanet Database</h3>${checkList(r.checks)}
        ${r.checks.some(c => /OBSCODE/.test(c.msg) && c.level !== 'ok') ? `<button class="b" data-fixaavso="${d.aavso}">Add my observer code to the file</button>` : ''}
        <ol class="steps">${r.steps.map(s => `<li>${esc(s)}</li>`).join('')}</ol>
        <div class="row">${ds.aavso_file ? `<a class="b primary" href="download?path=${encodeURIComponent(ds.aavso_file)}">1. Download AAVSO file</a>` : ''}${r.preview ? `<a class="b" href="files/${encodeURI(r.preview)}" download>2. Download star-field image</a>` : ''}
        <a class="b" href="${esc(r.submit)}" target="_blank" rel="noopener">3. Open AAVSO submit page ↗</a><button class="b good" data-exo="${d.aavso}" data-act="uploaded">4. I uploaded it</button></div>`;
      return;
    }
    if (d.fixaavso) { const r = await api('api/exo/' + d.fixaavso, { action: 'fix-aavso' }); t.textContent = r.changed ? 'Added ✓' : 'Nothing to change'; return; }
    if (d.share) {
      if (!confirm('Post this to your public Discord science channel? Only the science result is shared.')) return;
      await api('api/share', { kind: d.share, id: d.id }); t.textContent = 'Shared ✓'; return;
    }
    if (t.id === 'saveProfile') {
      await api('api/settings', { observer_name: $('#sName').value.trim(), contact_email: $('#sEmail').value.trim(), aavso_code: $('#sAavso').value.trim(), sarc_verified: $('#sSarc').checked });
      $('#saved1').textContent = 'Saved'; return refreshState();
    }
    if (t.id === 'saveSched') {
      const schedule = {}; $$('[data-sched]').forEach(i => schedule[i.dataset.sched] = i.value);
      await api('api/settings', { schedule, always_on: $('#sAlways').checked, tess: { max_targets: +$('#sTessN').value, workers: +$('#sTessW').value }, precovery: { max_targets: +$('#sPcN').value } });
      $('#saved2').textContent = 'Saved'; return;
    }
    if (t.id === 'saveInterests') {
      const interests = $('#sInterests').value.split('\n').map(s => s.trim()).filter(Boolean);
      await api('api/settings', { arxiv: { interests } }); $('#saved3').textContent = 'Saved (used from tomorrow)'; return;
    }
    if (t.id === 'saveHook') {
      await api('api/settings', { discord_webhook: $('#sHook').value.trim(), discord_alerts: { tasks: $('#aTasks').checked, brief: $('#aBrief').checked, errors: $('#aErr').checked } });
      $('#saved4').className = 'small ok-t'; $('#saved4').textContent = 'Saved'; return;
    }
    if (t.id === 'testHook') {
      $('#saved4').className = 'small muted'; $('#saved4').textContent = 'sending…';
      try { await api('api/notify-test', { url: $('#sHook').value.trim() }); $('#saved4').className = 'small ok-t'; $('#saved4').textContent = 'Sent: check your Discord channel'; }
      catch (err) { $('#saved4').className = 'small bad-t'; $('#saved4').textContent = err.message; }
      return;
    }
    if (t.id === 'askBtn') return ask();
    if (t.id === 'libGo') return libSearch();
    if (t.id === 'saveScope') {
      await api('api/settings', { telescope: { model: $('#scModel').value, lat: $('#scLat').value.trim(), lon: $('#scLon').value.trim(), elev: +$('#scElev').value || 0 } });
      $('#saved5').textContent = 'Saved. Run "Transit timing targets" on Today to plan transits.'; return;
    }
    if (t.id === 'saveShare') {
      try {
        await api('api/settings', { share: { public_name: $('#shName').value.trim(), discord_webhook: $('#shHook').value.trim(), post_summary: $('#shSummary').checked,
          github_repo: $('#shRepo').value.trim(), github_token: $('#shToken').value.trim(), publish: $('#shPublish').checked,
          site_url: $('#shSite').value.trim(), goatcounter: $('#shGc').value.trim(), goatcounter_token: $('#shGcKey').value.trim() } });
        $('#saved6').className = 'small ok-t'; $('#saved6').textContent = 'Saved';
      } catch (err) { $('#saved6').className = 'small bad-t'; $('#saved6').textContent = err.message; }
      return;
    }
    if (t.id === 'refreshLog') return loadLog();
    if (t.id === 'stopJob') { if (confirm('Stop the job that is running now?')) await api('api/stop/x', {}); return refreshState(); }
  } catch (err) { alert(err.message); }
});
function paperNewsLi(n) {
  return `<li><span><a href="${esc(n.url)}" target="_blank" rel="noopener"><b>${esc(n.title)}</b></a> <span class="small muted">${esc(n.source)}${n.date ? ' · ' + new Date(n.date * 1000).toLocaleDateString() : ''}</span><br>
    <span class="small">📄 the paper: ${n.papers.map(p => `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title || p.id)}</a>${p.venue ? ` <span class="muted">(${esc(p.venue)}${p.year ? ', ' + p.year : ''})</span>` : ''}`).join(' · ')}</span></span></li>`;
}
async function libSearch() {
  const q = $('#libQ').value.trim(); if (!q) return;
  const ai = $('#libAI').checked, out = $('#libOut');
  out.innerHTML = `<p class="muted">${ai ? 'Searching, then your local AI reads the top abstracts…' : 'Searching…'}</p>`; $('#libGo').disabled = true;
  try {
    const r = await api('api/library', { q, y0: +$('#libY0').value || null, y1: +$('#libY1').value || null, cat: $('#libCat').value, topic: $('#libTopic').value, review: ai });
    out.innerHTML = r.results.length ? r.results.map(p => `<div style="border-top:1px solid var(--line);padding:8px 0">
      <div class="row"><b><a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title)}</a></b><span class="spacer"></span>${p.ai ? `<span class="chip ${p.ai.score >= 7 ? 'ok' : p.ai.score >= 4 ? 'warn' : ''}">AI ${num(p.ai.score, 0)}/10</span>` : ''}<span class="small muted">${p.year}</span></div>
      <div class="small muted">${esc(p.authors)} ${(p.tags || []).map(t => `<span class="chip">${esc(t)}</span>`).join('')}</div>
      ${p.ai && p.ai.summary ? `<div style="margin-top:4px">${esc(p.ai.summary)}</div>` : ''}
      <details><summary class="small">Abstract</summary><p class="small muted">${esc(p.abstract)}</p></details></div>`).join('') : '<p class="muted">Nothing found yet (the library may still be downloading).</p>';
  } catch (err) { out.innerHTML = `<p class="bad-t">${esc(err.message)}</p>`; }
  $('#libGo').disabled = false;
}
async function ask() {
  const q = $('#askQ').value.trim(); if (!q) return;
  const out = $('#askOut'); out.innerHTML = '<p class="muted">Thinking (your local AI reads your results; 10–60 s)…</p>'; $('#askBtn').disabled = true;
  try { const r = await api('api/ask', { q }); out.innerHTML = r.answer ? md(r.answer) + `<div class="small muted">answered by ${esc(r.model)} in ${r.seconds}s</div>` : `<p class="bad-t">${esc(r.error)}</p>`; }
  catch (err) { out.innerHTML = `<p class="bad-t">${esc(err.message)}</p>`; }
  $('#askBtn').disabled = false;
}
document.addEventListener('keydown', e => { if (e.key === 'Enter' && e.target.id === 'askQ') ask(); if (e.key === 'Enter' && e.target.id === 'libQ') libSearch(); if (e.key === 'Escape') closeModal(); });
document.addEventListener('change', e => {
  if (e.target.id === 'arxivDate') { S.arxivDate = e.target.value; load('arxiv'); }
  if (e.target.id === 'logJob') { S.logJob = e.target.value; loadLog(); }
});

// ------------------------------------------------------------------ start
const start = (location.hash || '#today').slice(1);
function start_() {
  Promise.all([refreshState(), loadPublicData()]).then(() => layoutNav() || setTab(['today', 'arxiv', 'exowatch', 'tess', 'asteroids', 'setup'].includes(start) || EXTRA_TABS[start] ? start : 'today'));
}
// wait until every extras script has loaded (they add tabs), then lay out the tabs and open the first one
if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start_); else start_();
setInterval(() => { if (document.visibilityState === 'visible') loadPublicData(); }, 300000);
setInterval(refreshState, 20000);
setInterval(() => { if (document.visibilityState === 'visible' && ['tess', 'asteroids', 'exowatch'].includes(S.tab) && !Object.values(S.starClicks).some(a => a.length)) load(S.tab); }, 90000);
