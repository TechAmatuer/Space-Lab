'use strict';
// Variable-star observatory tab (local page only: it shows the observatory's own settings, plan and measurements).
// Server side: spacelab/vsobs.py (tonight's targets), spacelab/vsphot.py (photometry, AAVSO files), VARIABLE_STARS.md.
// Nothing here sends anything to the AAVSO: the observer downloads each file and uploads it at WebObs.
(function () {
  const S = window.SpaceLab;
  if (!S || !S.local) return;
  const GUIDE = 'https://github.com/TechAmatuer/Space-Lab/blob/main/VARIABLE_STARS.md';
  const ut = t => t ? new Date(t * 1000).toISOString().slice(11, 16) + ' UT' : '—';
  const jdUt = jd => ut((jd - 2440587.5) * 86400);
  const jdDay = jd => new Date((jd - 2440587.5) * 86400000).toISOString().slice(0, 10);
  const flags = f => (f || []).map(x => `<span class="chip warn">${esc(x)}</span>`).join(' ');

  function readyCard(x) {
    const all = x.ready.every(r => r[1]);
    const runs = x.runs || {};
    const run = (k, label) => `<button class="b" data-vsrun="${k}">${label}</button> <span class="small muted">${runs[k] ? esc(runs[k].summary || runs[k].status || '') + ' · ' + ago(runs[k].started) : 'not run yet'}</span>`;
    return `<div class="card"><p>For an automated observatory that reports variable-star photometry to the AAVSO. Every afternoon it picks tonight's
      targets (AAVSO alerts and campaigns first, then the stars most overdue for data) and writes them as a N.I.N.A. Target Scheduler file.
      Every hour it measures the new images against the AAVSO's comparison stars. When a night is over it makes one AAVSO report file,
      already checked, for you to upload. Nothing is sent to the AAVSO from here.</p>
      <ul class="checks">${x.ready.map(r => `<li>${r[1] ? '✅' : '⬜'} ${esc(r[0])}</li>`).join('')}</ul>
      ${all ? '' : `<p class="small">Set it up once: the <a href="${GUIDE}" target="_blank" rel="noopener">setup guide</a> walks through it, and the settings are at the bottom of this page.</p>`}
      <div class="row">${run('vsplan', 'Plan tonight now')}</div><div class="row">${run('vsphot', 'Measure new images now')}</div></div>`;
  }

  function planCard(p, sections) {
    if (!p) return '<div class="card empty">No plan yet. It is made every afternoon once the observatory is set up.</div>';
    const rows = (p.targets || []).map(t => `<tr><td>${ut(t.start)}</td><td><b>${esc(t.name)}</b><br><span class="small muted">${esc(t.type || '')}${t.period ? ', P = ' + num(t.period, 4) + ' d' : ''}</span></td>
        <td class="small">${(t.why || []).map(esc).join('<br>')}</td><td>${num(t.score, 1)}</td><td class="small">${(t.exposures || []).map(e => `${esc(e.filter)} ${e.exp}s${e.n > 1 ? '×' + e.n : ''}`).join(', ')}<br><span class="chip">${esc(t.series ? 'VS series' : t.class)}</span></td>
        <td>${num(t.mag_plan, 1)}</td><td>${num(t.alt_max, 0)}°</td><td>${num(t.moon_sep, 0)}°</td></tr>`).join('');
    const left = (p.left || []).map(l => `<li>${esc(l.name)}: ${esc(l.why)}</li>`).join('');
    const dark = (p.dark || []).map(ut).join(' to ');
    return `<div class="card"><div class="row"><b>Night of ${esc(p.day)}</b><span class="small muted">dark ${dark}, ${num((p.used_s || 0) / 3600, 1)} h planned, ${(p.targets || []).length} of ${p.n_candidates || 0} stars</span>
        <span class="spacer"></span>${p.csv ? `<a class="b primary" href="${fileUrl(p.csv)}" download>N.I.N.A. Target Scheduler CSV</a>` : ''}</div>
      <div style="overflow-x:auto"><table class="compact"><tr><th>start</th><th>star</th><th>why tonight</th><th>need</th><th>exposures</th><th>mag</th><th>alt</th><th>Moon</th></tr>${rows || '<tr><td colspan="8">nothing fits tonight</td></tr>'}</table></div>
      ${left ? `<details><summary>${(p.left || []).length} stars left out, and why</summary><ul class="small">${left}</ul></details>` : ''}
      <p class="small muted">Import the CSV in Target Scheduler with its Type filter: each Type (VS short, VS medium, VS long, VS series) copies the exposure plan of the template target you set up once. Sections: ${Object.entries(sections || {}).map(([k, v]) => `${esc(k)} = ${esc(v)}`).join(', ')}.</p></div>`;
  }

  function measRow(m, buttons) {
    return `<tr><td>${esc(m.target)}</td><td>${esc(m.filter)}</td><td>${jdDay(m.jd)} ${jdUt(m.jd)}</td><td><b>${num(m.mag, 3)}</b> ± ${num(m.err, 3)}</td>
      <td>${m.ncomp}${m.cname === 'ENSEMBLE' ? ' (ensemble)' : ''}</td><td>${m.kdev === null || m.kdev === undefined ? '—' : (m.kdev > 0 ? '+' : '') + num(m.kdev, 3)}</td>
      <td>${num(m.snr, 0)}</td><td>${num(m.airmass, 2)}</td><td class="small">${flags(m.flags)} ${m.report ? `<span class="chip">${esc(m.report)}</span>` : ''}</td>
      <td>${buttons}</td></tr>`;
  }
  const HEAD = '<tr><th>star</th><th>filter</th><th>time</th><th>magnitude</th><th>comps</th><th>check star</th><th>S/N</th><th>airmass</th><th></th><th></th></tr>';

  function reviewCard(rs) {
    if (!rs.length) return '';
    return `<h2>To look at (${rs.length})</h2><div class="card"><p class="small muted">These measurements have a warning. Accept the ones that look right (compare with the light curves below); rejected ones never go into a file.</p>
      <div style="overflow-x:auto"><table class="compact">${HEAD}${rs.map(m => measRow(m, `<button class="b good" data-vsm="${m.id}" data-a="accept">Accept</button><button class="b bad" data-vsm="${m.id}" data-a="reject">Reject</button>`)).join('')}</table></div></div>`;
  }

  function reportsCard(reps, webobs) {
    if (!reps.length) return '<div class="card empty">No AAVSO files yet. One is made for each night once the night is over.</div>';
    return `<div class="card"><p class="small muted">Download a file and upload it at <a href="${esc(webobs)}" target="_blank" rel="noopener">AAVSO WebObs → Upload observations</a>, then press "I uploaded it". Files are checked against the AAVSO Extended File Format first.</p>
      <table class="compact"><tr><th>night</th><th>file</th><th>measurements</th><th>check</th><th></th></tr>${reps.map(r => `<tr><td>${esc(r.night)}</td>
      <td><a href="${fileUrl(r.path)}" download>${esc(r.file)}</a></td><td>${r.rows} of ${r.stars} stars</td>
      <td>${r.errors ? `<span class="chip bad">${r.errors} errors</span>` : '<span class="chip ok">format OK</span>'}${r.warnings ? ` <span class="chip warn">${r.warnings} warnings</span>` : ''}
        ${(r.problems || []).length ? `<details><summary class="small">details</summary><ul class="small">${r.problems.map(p => `<li>${p[0] ? 'line ' + p[0] + ': ' : ''}${esc(p[2])}</li>`).join('')}</ul></details>` : ''}</td>
      <td>${r.uploaded ? `<span class="chip ok">uploaded ${ago(r.uploaded)}</span>` : `<button class="b primary" data-vsup="${esc(r.file)}">I uploaded it</button>`}</td></tr>`).join('')}</table></div>`;
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
      <h2>Tonight's targets</h2>${planCard(x.plan, x.sections)}
      ${reviewCard(x.review || [])}
      <h2>AAVSO files</h2>${reportsCard(x.reports || [], x.webobs)}
      ${(x.plots || []).length ? `<h2>Light curves</h2><div class="card"><div class="grid g2">${x.plots.map(p => `<img class="plot" src="${fileUrl(p)}" alt="light curve" loading="lazy">`).join('')}</div></div>` : ''}
      <h2>Images</h2><div class="card"><p class="small">${fr.ok || 0} measured and ready · ${fr.review || 0} with a warning · ${fr.failed || 0} failed · ${fr.skipped || 0} skipped (calibration frames, other filters)</p>
        ${(x.recent || []).length ? `<details><summary>Latest measurements</summary><div style="overflow-x:auto"><table class="compact">${HEAD}${x.recent.map(m => measRow(m, m.status === 'rejected' ? `<button class="b" data-vsm="${m.id}" data-a="reopen">Look again</button>` : m.status === 'reported' ? '<span class="chip ok">uploaded</span>' : `<button class="b bad" data-vsm="${m.id}" data-a="reject">Reject</button>`)).join('')}</table></div></details>` : ''}
        ${(x.failed || []).length ? `<details><summary>Images that could not be measured</summary><ul class="small">${x.failed.map(f => `<li>${esc(f.path)}: ${esc(f.note)}</li>`).join('')}</ul></details>` : ''}</div>
      ${settingsCard(x)}`;
    el.querySelectorAll('[data-vsrun]').forEach(b => b.onclick = async () => { b.disabled = true; try { await api('api/run/' + b.dataset.vsrun, {}); b.textContent = 'queued ✓'; } catch (e) { b.textContent = e.message; } });
    el.querySelectorAll('[data-vsm]').forEach(b => b.onclick = async () => { b.disabled = true; try { await api('api/vs/' + b.dataset.vsm, { action: b.dataset.a }); render(el); } catch (e) { alertLine(b, e); } });
    el.querySelectorAll('[data-vsup]').forEach(b => b.onclick = async () => { b.disabled = true; try { await api('api/vs/0', { action: 'uploaded', file: b.dataset.vsup }); render(el); } catch (e) { alertLine(b, e); } });
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
