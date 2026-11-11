'use strict';
// Variable-star observatory tab (local page only: it shows the observatory's own settings, plan and measurements).
// Server side: spacelab/vsobs.py (tonight's targets), vsphot.py (photometry, AAVSO files), vsqc.py (frame checks, comparison
// stars, clouds, overheads), vscal.py (transformation coefficients), vsnews.py (campaigns, outbursts), VARIABLE_STARS.md.
// Nothing here sends anything to the AAVSO: the observer downloads each file and uploads it at WebObs, and sends CHET reports.
(function () {
  const S = window.SpaceLab;
  if (!S || !S.local) return;
  const GUIDE = 'https://github.com/TechAmatuer/Space-Lab/blob/main/VARIABLE_STARS.md';
  const ut = t => t ? new Date(t * 1000).toISOString().slice(11, 16) + ' UT' : '—';
  const jdUt = jd => ut((jd - 2440587.5) * 86400);
  const jdDay = jd => new Date((jd - 2440587.5) * 86400000).toISOString().slice(0, 10);
  const flags = f => (f || []).map(x => `<span class="chip warn">${esc(x)}</span>`).join(' ');
  const notes = n => (n || []).map(x => `<span class="chip">${esc(x)}</span>`).join(' ');
  const cad = d => d == null ? '—' : d < 1 ? `every ${num(d * 24, 0)} h` : d === 1 ? 'nightly' : `every ${num(d, d < 10 ? 1 : 0)} d`;

  function readyCard(x) {
    const all = x.ready.every(r => r[1]);
    const runs = x.runs || {};
    const run = (k, label) => `<button class="b" data-vsrun="${k}">${label}</button> <span class="small muted">${runs[k] ? esc(runs[k].summary || runs[k].status || '') + ' · ' + ago(runs[k].started) : 'not run yet'}</span>`;
    return `<div class="card"><p>For an automated observatory that reports variable-star photometry to the AAVSO. Every afternoon it picks tonight's
      targets (AAVSO alerts and campaigns first, then the stars most overdue for data, stars that just went into outburst here) and writes them as a
      N.I.N.A. Target Scheduler file. Every hour it checks and measures the new images against the AAVSO's comparison stars and tells you about outbursts
      and fades. When a night is over it makes one AAVSO report file, already checked, with a short list of what to look at, for you to upload.
      Nothing is sent to the AAVSO from here.</p>
      <ul class="checks">${x.ready.map(r => `<li>${r[1] ? '✅' : '⬜'} ${esc(r[0])}</li>`).join('')}</ul>
      ${all ? '' : `<p class="small">Set it up once: the <a href="${GUIDE}" target="_blank" rel="noopener">setup guide</a> walks through it, and the settings are at the bottom of this page.</p>`}
      <div class="row">${run('vsplan', 'Plan tonight now')}</div><div class="row">${run('vsphot', 'Measure new images now')}</div></div>`;
  }

  function eventsCard(evs, boosts) {
    if (!evs.length) return '';
    return `<h2>Outbursts and fades (${evs.length})</h2><div class="card"><p class="small muted">Each new measurement is compared with the star's recent AAVSO data (or this observatory's own). Stars here get priority in the next nights' plans; nothing has been reported for you.</p>
      <table class="compact"><tr><th>night</th><th>star</th><th>what</th><th>priority</th></tr>${evs.map(e => {
        const b = boosts[e.star];
        return `<tr><td>${esc(e.night || '')}</td><td><b>${esc(e.star)}</b><br><span class="small muted">${esc(e.type || '')}</span></td><td class="small">${esc(e.words)}</td>
          <td class="small">${b && b.until > Date.now() / 1000 ? 'raised until ' + new Date(b.until * 1000).toISOString().slice(0, 10) + (b.series_min ? `, ${b.series_min}-min time series` : '') : '—'}</td></tr>`;
      }).join('')}</table></div>`;
  }

  function campaignsCard(cs) {
    if (!cs.length) return '';
    return `<details class="card"><summary><b>AAVSO campaigns (${cs.length})</b> <span class="small muted">read from the AAVSO campaign pages; their stars, filters and cadence go into tonight's plan until the end date</span></summary>
      <div style="overflow-x:auto"><table class="compact"><tr><th>#</th><th>campaign</th><th>stars</th><th>filters</th><th>how often</th><th>until</th><th>read by</th></tr>${cs.map(c => `<tr>
        <td><a href="${esc(c.url)}" target="_blank" rel="noopener">${c.id}</a></td><td>${esc(c.title)}${c.urgent ? ' <span class="chip bad">urgent</span>' : ''}${c.note ? `<br><span class="small muted">${esc(c.note)}</span>` : ''}</td>
        <td class="small">${(c.targets || []).map(esc).join(', ')}</td><td>${(c.filters || []).map(esc).join(' ') || 'any'}</td>
        <td>${cad(c.cadence_days)}${c.time_series ? `<br><span class="chip">time series${c.series_minutes ? ' ' + num(c.series_minutes, 0) + ' min' : ''}</span>` : ''}</td>
        <td>${esc(c.end || 'ongoing')}</td><td class="small muted">${esc(c.by || '')}</td></tr>`).join('')}</table></div></details>`;
  }

  function planCard(p, sections) {
    if (!p) return '<div class="card empty">No plan yet. It is made every afternoon once the observatory is set up.</div>';
    const rows = (p.targets || []).map(t => `<tr><td>${ut(t.start)}</td><td><b>${esc(t.name)}</b>${t.standard ? ' <span class="chip ok">standard field</span>' : ''}<br><span class="small muted">${esc(t.type || '')}${t.period ? ', P = ' + num(t.period, 4) + ' d' : ''}</span>
          ${(t.with || []).length ? `<br><span class="small">+ ${t.with.map(esc).join(', ')} (same field)</span>` : ''}${t.campaign ? `<br><a class="small" href="${esc(t.campaign_url || '#')}" target="_blank" rel="noopener">campaign #${t.campaign}</a>` : ''}</td>
        <td class="small">${(t.why || []).map(esc).join('<br>')}${t.filter_note ? `<br><span class="chip warn">${esc(t.filter_note)}</span>` : ''}</td><td>${num(t.score, 1)}</td><td class="small">${(t.exposures || []).map(e => `${esc(e.filter)} ${e.exp}s${e.n > 1 ? '×' + e.n : ''}`).join(', ')}<br><span class="chip">${esc(t.series ? 'VS series' : t.class)}</span></td>
        <td>${num(t.mag_plan, 1)}</td><td>${num(t.alt_max, 0)}°</td><td>${num(t.moon_sep, 0)}°</td></tr>`).join('');
    const left = (p.left || []).map(l => `<li>${esc(l.name)}: ${esc(l.why)}</li>`).join('');
    const dark = (p.dark || []).map(ut).join(' to ');
    const oh = p.overheads && Object.keys(p.overheads).length ? `Overheads measured on recent nights: ${Object.entries(p.overheads).map(([k, v]) => `${esc(k.replace('_s', '').replace('_', ' '))} ${num(v, 0)} s`).join(', ')}. ` : '';
    return `<div class="card"><div class="row"><b>Night of ${esc(p.day)}</b><span class="small muted">dark ${dark}, ${num((p.used_s || 0) / 3600, 1)} h planned, ${(p.targets || []).length} pointings for ${p.n_candidates || 0} candidate stars${p.grouped ? `, ${p.grouped} sharing a field` : ''}</span>
        <span class="spacer"></span>${p.csv ? `<a class="b primary" href="${fileUrl(p.csv)}" download>N.I.N.A. Target Scheduler CSV</a>` : ''}</div>
      <div style="overflow-x:auto"><table class="compact"><tr><th>start</th><th>star</th><th>why tonight</th><th>need</th><th>exposures</th><th>mag</th><th>alt</th><th>Moon</th></tr>${rows || '<tr><td colspan="8">nothing fits tonight</td></tr>'}</table></div>
      ${left ? `<details><summary>${(p.left || []).length} stars left out, and why</summary><ul class="small">${left}</ul></details>` : ''}
      <p class="small muted">${oh}Import the CSV in Target Scheduler with its Type filter: each Type (VS short, VS medium, VS long, VS series, VS standard) copies the exposure plan of the template target you set up once. Sections: ${Object.entries(sections || {}).map(([k, v]) => `${esc(k)} = ${esc(v)}`).join(', ')}.</p></div>`;
  }

  function measRow(m, buttons) {
    return `<tr><td>${esc(m.target)}</td><td>${esc(m.filter)}</td><td>${jdDay(m.jd)} ${jdUt(m.jd)}</td><td><b>${num(m.mag, 3)}</b> ± ${num(m.err, 3)}</td>
      <td>${m.ncomp}${m.cname === 'ENSEMBLE' ? ' (ensemble)' : ''}</td><td>${m.kdev === null || m.kdev === undefined ? '—' : (m.kdev > 0 ? '+' : '') + num(m.kdev, 3)}</td>
      <td>${num(m.snr, 0)}</td><td>${num(m.airmass, 2)}</td><td class="small">${flags(m.flags)} ${notes(m.notes)} ${m.report ? `<span class="chip">${esc(m.report)}</span>` : ''}</td>
      <td>${buttons}</td></tr>`;
  }
  const HEAD = '<tr><th>star</th><th>filter</th><th>time</th><th>magnitude</th><th>comps</th><th>check star</th><th>S/N</th><th>airmass</th><th></th><th></th></tr>';

  function reviewCard(rs) {
    if (!rs.length) return '';
    return `<h2>To look at (${rs.length})</h2><div class="card"><p class="small muted">These measurements have a warning (orange). Grey notes are for information only. Accept the ones that look right (compare with the light curves below); rejected ones never go into a file.</p>
      <div style="overflow-x:auto"><table class="compact">${HEAD}${rs.map(m => measRow(m, `<button class="b good" data-vsm="${m.id}" data-a="accept">Accept</button><button class="b bad" data-vsm="${m.id}" data-a="reject">Reject</button>`)).join('')}</table></div></div>`;
  }

  function reportsCard(reps, webobs) {
    if (!reps.length) return '<div class="card empty">No AAVSO files yet. One is made for each night once the night is over.</div>';
    const lv = { ok: '✅', note: 'ℹ️', check: '⚠️' };
    return `<div class="card"><p class="small muted">Download a file and upload it at <a href="${esc(webobs)}" target="_blank" rel="noopener">AAVSO WebObs → Upload observations</a>, then press "I uploaded it". Files are checked against the AAVSO Extended File Format first.</p>
      <table class="compact"><tr><th>night</th><th>file</th><th>measurements</th><th>before you upload</th><th></th></tr>${reps.map(r => `<tr><td>${esc(r.night)}</td>
      <td><a href="${fileUrl(r.path)}" download>${esc(r.file)}</a></td><td>${r.rows} of ${r.stars} stars</td>
      <td class="small">${r.summary ? `<p>${esc(r.summary)}${r.summary_by === 'local AI' ? ' <span class="muted">(worded by the local AI from the checks)</span>' : ''}</p>` : ''}
        ${r.errors ? `<span class="chip bad">${r.errors} errors</span>` : '<span class="chip ok">format OK</span>'}${r.warnings ? ` <span class="chip warn">${r.warnings} warnings</span>` : ''}
        ${(r.checks || []).length ? `<details><summary>checks</summary><ul>${r.checks.map(c => `<li>${lv[c[0]] || ''} ${esc(c[1])}</li>`).join('')}</ul></details>` : ''}
        ${(r.problems || []).length ? `<details><summary>format details</summary><ul>${r.problems.map(p => `<li>${p[0] ? 'line ' + p[0] + ': ' : ''}${esc(p[2])}</li>`).join('')}</ul></details>` : ''}</td>
      <td>${r.uploaded ? `<span class="chip ok">uploaded ${ago(r.uploaded)}</span>` : `<button class="b primary" data-vsup="${esc(r.file)}">I uploaded it</button>`}</td></tr>`).join('')}</table></div>`;
  }

  function compCard(fl, chet) {
    if (!fl.length && !chet.length) return '';
    const kind = { variable: 'varies', offset: 'off its chart value', catalog: 'known variable' };
    return `<h2>Comparison stars</h2><div class="card"><p class="small muted">Every comparison star is compared with the others in every frame and night after night, and looked up in AAVSO VSX and Gaia DR3. A star that varies leaves the ensemble at once and the measurements that used it are done again. Reports for the AAVSO sequence team are drafted; read them and send them yourself through <a href="https://www.aavso.org/chet-help" target="_blank" rel="noopener">CHET</a>.</p>
      ${fl.length ? `<div style="overflow-x:auto"><table class="compact"><tr><th>star</th><th>chart</th><th>what</th><th>status</th><th></th></tr>${fl.map(f => `<tr><td><b>${esc(f.auid)}</b>${f.label ? ' (' + esc(f.label) + ')' : ''}<br><span class="small muted">near ${esc(f.star || '')}${f.band && f.band !== '*' ? ', ' + esc(f.band) : ''}</span></td>
        <td>${esc(f.chart || '')}</td><td class="small"><span class="chip warn">${esc(kind[f.kind] || f.kind)}</span> ${esc(f.why)}</td>
        <td>${f.cleared ? '<span class="chip">kept by you</span>' : f.excluded ? '<span class="chip bad">left out</span>' : '<span class="chip">watched</span>'}</td>
        <td>${f.excluded && !f.cleared ? `<button class="b" data-vscomp="${esc(f.auid)}" data-band="${esc(f.band)}" data-do="clear">Keep using it</button>` : `<button class="b bad" data-vscomp="${esc(f.auid)}" data-band="${esc(f.band)}" data-do="exclude">Leave it out</button>`}</td></tr>`).join('')}</table></div>` : ''}
      ${chet.length ? `<h3>CHET drafts</h3><ul class="small">${chet.map(c => `<li><a href="${fileUrl(c.path)}" target="_blank">${esc(c.auid)}</a>: ${esc(c.why)} ${c.sent ? '<span class="chip ok">sent</span>' : `<button class="b" data-vschet="${esc(c.file)}">I sent it</button>`}</li>`).join('')}</ul>` : ''}</div>`;
  }

  function transformCard(tr) {
    if (!tr) return '';
    const a = tr.active || {};
    const src = a.source === 'auto' ? `measured on standard fields (${a.sets} night${a.sets === 1 ? '' : 's'}, ${esc(a.since || '')} to ${esc(a.latest || '')})`
      : a.source === 'manual' ? 'your own coefficients from the settings' : 'none yet';
    const coefs = a.coefs ? Object.entries(a.coefs).map(([k, v]) => `${esc(k)} = ${num(v[0], 3)} ± ${num(v[1], 3)}`).join(', ') : '';
    const sets = (tr.sets || []).map(s => `<tr><td>${esc(s.night)}</td><td>${esc(s.field)}</td><td class="small">${Object.entries(s.coefs || {}).map(([k, v]) => `${esc(k)} ${num(v[0], 3)}`).join(', ') || 'too few standard stars'}</td>
      <td>${esc(s.state)}</td><td>${s.state === 'held' ? `<button class="b good" data-vstr="${esc(s.night)}" data-field="${esc(s.field)}" data-acc="1">Accept</button><button class="b bad" data-vstr="${esc(s.night)}" data-field="${esc(s.field)}" data-acc="0">Reject</button>` : ''}</td></tr>`).join('');
    return `<details class="card"><summary><b>Colour transformation</b> <span class="small muted">in use: ${src}</span></summary>
      <p class="small">${tr.auto ? `Standard fields (M67, NGC 7790 or your own) are added to the plan once a month${tr.due ? ` (<b>due:</b> ${esc(tr.due)})` : ''}; each night's coefficients are fitted like the AAVSO Transform Generator does, and a new night is used only when it agrees with the others.` : 'Measuring coefficients on standard fields is off (you have your own coefficients; set "transform_auto": true to measure them anyway).'}</p>
      ${coefs ? `<p class="small mono">${coefs}</p>` : ''}
      ${sets ? `<table class="compact"><tr><th>night</th><th>field</th><th>coefficients</th><th>state</th><th></th></tr>${sets}</table>` : ''}</details>`;
  }

  function qualityCard(x) {
    const nights = x.nights || [], oh = x.overhead || {}, rej = x.rejected || [];
    if (!nights.length && !(oh.nights || []).length && !rej.length) return '';
    const nrow = n => `<tr><td>${esc(n.night)}</td><td>${n.photometric ? '<span class="chip ok">photometric</span>' : '<span class="chip warn">not photometric</span>'}</td>
      <td class="small">${Object.entries(n.bands || {}).map(([b, v]) => `${esc(b)}: ${v.k == null ? '—' : 'k = ' + num(v.k, 2)}, scatter ${num(v.scatter, 3)}${v.cloud ? `, ${v.cloud} cloudy` : ''}`).join('<br>')}</td></tr>`;
    const orow = o => `<tr><td>${esc(o.night)}</td><td>${o.open == null ? '—' : num(o.open * 100, 0) + '%'} of ${num(o.hours, 1)} h</td><td>${o.slews} (${num(o.slew_total_min, 0)} min)</td>
      <td>${o.download_s == null ? '—' : num(o.download_s, 0) + ' s'}</td><td>${o.filter_s == null ? '—' : num(o.filter_s, 0) + ' s'}</td><td>${o.slew_s == null ? '—' : num(o.slew_s, 0) + ' s'}</td></tr>`;
    return `<h2>Night quality</h2><div class="card">
      ${nights.length ? `<p class="small muted">Zero points of all frames against airmass, per filter: the extinction (k, magnitudes per airmass), the scatter, and frames that lost light to cloud (flagged above half).</p><table class="compact"><tr><th>night</th><th></th><th>per filter</th></tr>${nights.map(nrow).join('')}</table>` : ''}
      ${(oh.nights || []).length ? `<p class="small muted">Time between exposures, from the images' own time stamps. The planner uses these to fit the night as it really runs; stars that share a field are planned as one pointing to save slews.</p>
        <table class="compact"><tr><th>night</th><th>shutter open</th><th>slews</th><th>download</th><th>filter change</th><th>slew</th></tr>${oh.nights.map(orow).join('')}</table>` : ''}
      ${rej.length ? `<details><summary>${rej.length} frame(s) set aside by the frame check</summary><ul class="small">${rej.map(f => `<li>${esc(f.path)}: ${esc(f.note)} <button class="b" data-vsany="${esc(f.path)}">Measure anyway</button></li>`).join('')}</ul></details>` : ''}</div>`;
  }

  function settingsCard(x) {
    return `<details class="card"><summary><b>Settings</b> (private: they stay on this computer)</summary>
      <p class="small muted">Every setting is explained in the <a href="${GUIDE}" target="_blank" rel="noopener">setup guide</a>. The Target Tool key is shown masked; leave it as it is to keep it.</p>
      <textarea id="vsSettings" class="mono small" rows="24" style="width:100%">${esc(JSON.stringify(x.settings, null, 2))}</textarea>
      <div class="row"><button class="b primary" id="vsSave">Save settings</button><span class="small" id="vsSaveMsg"></span></div></details>`;
  }

  async function render(el) {
    el.innerHTML = '<h2>Variable-star observatory</h2><div class="card">loading…</div>';
    let x;
    try { x = await api('api/vs'); } catch (e) { el.innerHTML = `<h2>Variable-star observatory</h2><div class="card empty">Could not load: ${esc(e.message)}</div>`; return; }
    const fr = x.frames || {};
    el.innerHTML = `<h2>Variable-star observatory</h2>${readyCard(x)}
      ${eventsCard(x.events || [], x.boosts || {})}
      <h2>Tonight's targets</h2>${planCard(x.plan, x.sections)}${campaignsCard(x.campaigns || [])}
      ${reviewCard(x.review || [])}
      <h2>AAVSO files</h2>${reportsCard(x.reports || [], x.webobs)}
      ${(x.plots || []).length ? `<h2>Light curves</h2><div class="card"><div class="grid g2">${x.plots.map(p => `<img class="plot" src="${fileUrl(p)}" alt="light curve" loading="lazy">`).join('')}</div></div>` : ''}
      ${compCard(x.comp_flags || [], x.chet || [])}
      <h2>Images</h2><div class="card"><p class="small">${fr.ok || 0} measured and ready · ${fr.review || 0} with a warning · ${fr.rejected || 0} set aside by the frame check · ${fr.standard || 0} standard-field frames · ${fr.failed || 0} failed · ${fr.skipped || 0} skipped (calibration frames, other filters)</p>
        ${(x.recent || []).length ? `<details><summary>Latest measurements</summary><div style="overflow-x:auto"><table class="compact">${HEAD}${x.recent.map(m => measRow(m, m.status === 'rejected' ? `<button class="b" data-vsm="${m.id}" data-a="reopen">Look again</button>` : m.status === 'reported' ? '<span class="chip ok">uploaded</span>' : `<button class="b bad" data-vsm="${m.id}" data-a="reject">Reject</button>`)).join('')}</table></div></details>` : ''}
        ${(x.failed || []).length ? `<details><summary>Images that could not be measured</summary><ul class="small">${x.failed.map(f => `<li>${esc(f.path)}: ${esc(f.note)}</li>`).join('')}</ul></details>` : ''}</div>
      ${qualityCard(x)}
      ${transformCard(x.transform)}
      ${settingsCard(x)}`;
    const act = async (b, body, id = 0) => { b.disabled = true; try { await api('api/vs/' + id, body); render(el); } catch (e) { alertLine(b, e); } };
    el.querySelectorAll('[data-vsrun]').forEach(b => b.onclick = async () => { b.disabled = true; try { await api('api/run/' + b.dataset.vsrun, {}); b.textContent = 'queued ✓'; } catch (e) { b.textContent = e.message; } });
    el.querySelectorAll('[data-vsm]').forEach(b => b.onclick = () => act(b, { action: b.dataset.a }, b.dataset.vsm));
    el.querySelectorAll('[data-vsup]').forEach(b => b.onclick = () => act(b, { action: 'uploaded', file: b.dataset.vsup }));
    el.querySelectorAll('[data-vscomp]').forEach(b => b.onclick = () => act(b, { action: 'comp', auid: b.dataset.vscomp, band: b.dataset.band, do: b.dataset.do }));
    el.querySelectorAll('[data-vschet]').forEach(b => b.onclick = () => act(b, { action: 'chet_sent', file: b.dataset.vschet }));
    el.querySelectorAll('[data-vstr]').forEach(b => b.onclick = () => act(b, { action: 'transform', night: b.dataset.vstr, field: b.dataset.field, accept: b.dataset.acc === '1' }));
    el.querySelectorAll('[data-vsany]').forEach(b => b.onclick = () => act(b, { action: 'measure_anyway', file: b.dataset.vsany }));
    const save = el.querySelector('#vsSave');
    if (save) save.onclick = async () => {
      const msg = el.querySelector('#vsSaveMsg');
      let obj;
      try { obj = JSON.parse(el.querySelector('#vsSettings').value); } catch (e) { msg.textContent = 'Not valid JSON: ' + e.message; return; }
      try { await api('api/vs/0', { action: 'settings', settings: obj }); msg.textContent = 'Saved ✓'; } catch (e) { msg.textContent = e.message; }
    };
  }
  function alertLine(b, e) { b.disabled = false; b.title = e.message; b.textContent = 'failed: ' + e.message; }

  S.addTab('vstars', 'Variable-star observatory', render);
})();
