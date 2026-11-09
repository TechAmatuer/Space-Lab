// Space Lab Home page, Candidate of the day, the space-science radar and the Results log.
// Runs on the public page AND inside Meridian through window.SpaceLab (see EXTRAS.md).
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const D = () => S.data || {};
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode: still works for this visit */ } },
  };
  const ISSUES = () => { const r = (D().site || {}).repo; return r ? `https://github.com/${r}/issues/new` : ''; };   // from data.json
  // the same answers as the GitHub review form (.github/ISSUE_TEMPLATE/candidate-review.yml)
  const ANS = [['Planet-like', 'planet-like'], ['Eclipsing binary (two stars)', 'eclipsing-binary'], ['Comes from a nearby star', 'nearby-star'],
               ['Noise / instrument glitch', 'noise'], ['Not sure', 'not-sure']];
  const ago = t => { if (!t) return '—'; const s = Date.now() / 1000 - t; return s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago'; };
  const day = t => t ? new Date(t * 1000).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
  const goTab = id => { const b = document.querySelector(`nav.tabs button[data-tab="${id}"]`); if (b) b.click(); };

  // ------------------------------------------------------------------ candidate of the day
  function streak() {
    const votes = LS.get('sl_daily', {});
    let n = 0;
    const base = (D().daily || {}).date;                                          // the site's "today" (UTC), same for everyone
    const d = base ? new Date(base + 'T12:00:00Z') : new Date();
    if (!votes[d.toISOString().slice(0, 10)]) d.setUTCDate(d.getUTCDate() - 1);   // today not voted yet: the streak is still alive
    while (votes[d.toISOString().slice(0, 10)]) { n++; d.setUTCDate(d.getUTCDate() - 1); }
    return n;
  }
  function bars(counts) {
    const rows = ANS.map(([a]) => [a, counts[a] || 0]), total = rows.reduce((t, r) => t + r[1], 0);
    if (!total) return '';
    return `<div class="hm-bars">${rows.filter(r => r[1]).map(([a, n]) => `<div class="hm-bar"><span>${esc(a)}</span><i style="width:${Math.round(n / total * 100)}%"></i><b>${n}</b></div>`).join('')}</div>`;
  }
  function facts(c) {
    const st = c.star || {};
    return `dips every <b class="mono">${num(c.period, 3)} days</b> · ${Math.round((c.depth || 0) * 1e6)} ppm deep · ${num((c.duration || 0) * 24, 1)} h long · ~${num(c.rp, 1)}× Earth if it is a planet${st.teff ? ` · ${Math.round(st.teff)} K star` : ''}${st.dist_pc ? `, ${Math.round(st.dist_pc * 3.26)} light-years away` : ''}`;
  }
  function dailyCard(box) {
    const d = D().daily || {}, c = d.cand;
    if (!c) { box.innerHTML = '<h3>🪐 Candidate of the day</h3><p class="muted">The first candidate of the day appears after the next update.</p>'; return; }
    const mine = LS.get('sl_daily', {})[d.date];
    const prev = d.prev;
    const reveal = mine ? `<div class="note hm-thanks"><b>Your vote: ${esc(mine)}.</b> Daily streak: <b>${streak()}</b> day${streak() === 1 ? '' : 's'} 🔥. Results tomorrow.<br>
        For comparison: the local AI said <b>${esc(c.ai || 'nothing yet')}</b>${(c.flags || []).length ? '; automatic checks flagged ' + c.flags.map(esc).join(', ') : '; no automatic check raised a flag'}${c.fpp ? '; statistical test: ' + esc(c.fpp) : ''}.
        ${bars(d.votes || {}) ? '<div class="small muted">Votes so far today:</div>' + bars(d.votes) : ''}
        <div><a class="small" target="_blank" rel="noopener" href="${ISSUES()}?template=candidate-review.yml&title=${encodeURIComponent('TIC ' + c.tic + ' review')}&tic=${c.tic}&verdict=${encodeURIComponent(mine)}">Explain your reasoning on GitHub ↗</a></div></div>`
      : `<div class="row hm-answers">${ANS.map(([a, k]) => `<button type="button" class="b hm-vote" data-k="${k}">${esc(a)}</button>`).join('')}</div>
        <p class="small muted">One vote per day, no account needed. Everyone gets the same candidate; tomorrow you'll see how everyone voted.</p>`;
    box.innerHTML = `<div class="row"><h3>🪐 Candidate of the day</h3><span class="spacer"></span><span class="small muted">${esc(d.date)} (UTC)</span></div>
      <div class="hm-daily"><div><img class="plot" src="${esc(c.plot || '')}" alt="light-curve plots of TIC ${c.tic}"></div>
      <div><h4 style="margin-top:0">TIC ${c.tic}</h4><div class="small">${facts(c)}</div><p>${esc(d.blurb || '')}</p>
      <details class="small"><summary>How to judge it</summary><ol><li><b>Top left:</b> is there a small dip under every red line?</li><li><b>Bottom left:</b> a flat-bottomed U suits a planet; a sharp V often means two stars.</li><li><b>Bottom right:</b> odd and even dips should be equally deep, and the dashed line flat.</li></ol></details>
      <b>Planet or not?</b>${reveal}</div></div>
      ${prev && prev.cand ? `<div class="hm-prev"><h4>Yesterday: TIC ${prev.cand.tic}</h4>
        ${bars(prev.votes || {}) || '<p class="small muted">No quick votes were counted yesterday.</p>'}
        <div class="kv small"><b>GitHub reviews</b><span>${Object.entries(prev.github || {}).map(([k, n]) => `${n}× ${esc(k)}`).join(' · ') || 'none yet'}</span>
        <b>Local AI</b><span>${esc(prev.cand.ai || 'no verdict')}</span><b>The volunteer</b><span>${esc(prev.volunteer || 'not reviewed yet')}</span></div></div>` : ''}`;
    box.querySelectorAll('.hm-vote').forEach(b => b.addEventListener('click', () => {
      const a = ANS.find(x => x[1] === b.dataset.k);
      const v = LS.get('sl_daily', {}); v[d.date] = a[0]; LS.set('sl_daily', v);
      S.track(`vote-${d.date}-${a[1]}`, true);
      dailyCard(box);
    }));
  }

  // ------------------------------------------------------------------ home slots
  function youCard() {
    const d = D().daily || {}, voted = d.date && LS.get('sl_daily', {})[d.date];
    const all = ((D().tess || {}).cands || []), mine = new Set(LS.get('sl_reviews', []).map(r => String(r.tic)));
    const left = all.filter(c => !mine.has(String(c.tic))).length, lv = window.SpaceLabExtras && window.SpaceLabExtras.level ? window.SpaceLabExtras.level() : null;
    const sg = window.SpaceLabStargazing, p = sg && sg.place();
    return `<div class="row"><h3>👋 Your Space Lab</h3><span class="spacer"></span>${lv ? `<span class="small">You're a <b>${esc(lv.name)}</b> · ${lv.points} points${streak() ? ` · 🔥 ${streak()}-day streak` : ''}</span>` : ''}</div>
      <p class="small">Everything here is for you to use and to help with. Pick something:</p>
      <div class="hm-actions">
        <button type="button" class="hm-act" data-go="today-daily"><b>🪐 ${voted ? 'Voted today ✓' : 'Vote on today\'s candidate'}</b><span>${voted ? 'see how it compares' : 'one tap, no account'}</span></button>
        <button type="button" class="hm-act" data-tab="play"><b>🔍 Review candidates</b><span>${left ? `${left} waiting for you` : 'you\'ve seen them all 🎉'}</span></button>
        <button type="button" class="hm-act" ${p ? 'data-go="hm-tonight"' : 'data-place="1"'}><b>🌙 ${p ? 'Your sky tonight' : 'Set your location'}</b><span>${p ? esc(p.label) : 'for tonight\'s sky, ISS passes, dark skies'}</span></button>
        <button type="button" class="hm-act" data-tab="stargazing"><b>🔭 Go stargazing</b><span>dark skies, reminders, what's that light?</span></button>
      </div>`;
  }
  function tonightCard() {
    const sg = window.SpaceLabStargazing, p = sg && sg.place();
    if (p) return sg.tonightHTML(p);
    const b = D().beyond || {}, s = b.sky, w = b.space_weather || {};
    if (!s) return '<h3>🌙 Tonight</h3><p class="muted small">Sky data appears after the next update.</p>';
    const vis = (s.planets || []).filter(p => !/too close/.test(p.where));
    const kp = (w.kp || []).filter(k => k.kp !== null).slice(-1)[0];
    const shower = (s.showers || [])[0];
    return `<h3>🌙 Tonight</h3><ul class="hm-list">
      <li><b>Moon:</b> ${esc(s.moon.phase)}, ${Math.round(s.moon.illumination * 100)}% lit</li>
      ${vis.length ? `<li><b>Planets:</b> ${vis.map(p => `${esc(p.name)} (${/evening/.test(p.where) ? 'evening' : 'morning'})`).join(', ')}</li>` : ''}
      ${shower ? `<li><b>Meteors:</b> ${esc(shower.name)} ${shower.days <= 0 ? 'peak now' : 'peak in ' + shower.days + ' day' + (shower.days > 1 ? 's' : '')}</li>` : ''}
      ${kp ? `<li><b>Aurora:</b> Kp ${num(kp.kp, 1)} ${kp.kp >= 5 ? '<span class="chip warn">storm: look north after dark</span>' : '(quiet)'}</li>` : ''}</ul>
      <button type="button" class="b hm-go" data-tab="sky">Space Station passes over you →</button>`;
  }
  function radarItem(x, full) {
    return `<li class="hm-radar-item" data-topic="${esc(x.topic)}"><span class="hm-score" title="importance ${x.score}/10">${'●'.repeat(Math.round(x.score / 2))}<i>${'●'.repeat(5 - Math.round(x.score / 2))}</i></span>
      <span><a href="${esc(x.url)}" target="_blank" rel="noopener"><b>${esc(x.title)}</b></a>${x.lab ? ' <span class="chip ok">about this lab\'s targets</span>' : ''}
      <br><span class="small">${esc(x.summary || '')}</span>${full && x.why ? `<br><span class="small muted">Why it matters: ${esc(x.why)}</span>` : ''}
      <br><span class="small muted">${esc(x.topic)} · ${(x.sources || []).map(esc).join(', ')} · ${ago(x.date)}</span></span></li>`;
  }
  function radarCard(full) {
    const r = D().radar || {}, items = r.items || [];
    if (!items.length) return '<h3>📡 Space science radar</h3><p class="muted small">The local AI reads everything new in space science every 6 hours; the first scan appears soon.</p>';
    const topics = [...new Set(items.map(x => x.topic))];
    return `<div class="row"><h3>📡 Space science radar</h3><span class="spacer"></span><span class="small muted">updated ${ago(r.t)} · ${r.n_sources || '?'} sources read by a local AI</span></div>
      ${r.digest ? `<p>${esc(r.digest.text)}</p>` : ''}
      ${full ? `<div class="row hm-topics"><button type="button" class="b primary hm-topic" data-topic="">All</button>${topics.map(t => `<button type="button" class="b hm-topic" data-topic="${esc(t)}">${esc(t)}</button>`).join('')}</div>` : ''}
      <ul class="list hm-radar">${items.slice(0, full ? 60 : 5).map(x => radarItem(x, full)).join('')}</ul>
      ${full ? `<p class="small muted">Ranked by importance (●●●●● = major) by a local AI that read the title and summary of every story from space agencies, observatories, research sites and science news. It can be wrong: follow the links for the real story.${S.local && (r.failed || []).length ? ` <b>Feeds that failed last time:</b> ${r.failed.map(esc).join(', ')}.` : ''}</p>` : '<button type="button" class="b hm-go" data-tab="brief">More space science news →</button>'}`;
  }
  function wireRadar(el) {
    el.querySelectorAll('.hm-topic').forEach(b => b.addEventListener('click', () => {
      el.querySelectorAll('.hm-topic').forEach(x => x.classList.toggle('primary', x === b));
      el.querySelectorAll('.hm-radar-item').forEach(li => { li.style.display = !b.dataset.topic || li.dataset.topic === b.dataset.topic ? '' : 'none'; });
    }));
  }
  const KIND = { aavso: ['⏱', 'Transit timings'], ctoi: ['🪐', 'Planet candidates reported'], mpc: ['☄️', 'Asteroid positions'], vsx: ['✨', 'Variable stars'], other: ['📄', 'Other'] };
  function resultsSummary() {
    const rs = D().results || [];
    if (!rs.length) return '<h3>🏆 Results</h3><p class="small muted">Nothing sent to professional databases yet. When it is, every submission and what happened to it shows up here.</p>';
    const by = {};
    rs.forEach(r => { by[r.kind] = (by[r.kind] || 0) + 1; });
    return `<h3>🏆 Results</h3><div class="hm-counts">${Object.entries(by).map(([k, n]) => `<div><b>${n}</b><span>${KIND[k] ? KIND[k][0] + ' ' + KIND[k][1] : esc(k)}</span></div>`).join('')}</div>
      <ul class="hm-list">${rs.slice(0, 3).map(r => `<li>${esc(r.title)} <span class="chip ${/confirmed|accepted|published/.test(r.status) ? 'ok' : ''}">${esc(r.status)}</span></li>`).join('')}</ul>
      <button type="button" class="b hm-go" data-tab="results">All results →</button>`;
  }
  function home(el) {
    let slots = el.querySelector('#home-slots');
    if (!slots) {           // Meridian: put the cards between "Ask" and your tasks (that part refreshes on its own)
      slots = document.createElement('div'); slots.id = 'home-slots';
      const ask = el.querySelector('#askBox');
      if (ask) ask.after(slots); else el.prepend(slots);
    }
    slots.innerHTML = `<div class="card hm-you">${youCard()}</div><div class="card hm-daily-card" id="today-daily"></div>
      <div class="grid g2"><div class="card" id="hm-tonight">${tonightCard()}</div><div class="card">${resultsSummary()}</div></div>
      <div class="card">${radarCard(false)}</div>
      ${S.local ? '' : `<div class="card"><h3>🙋 How you can help</h3><ul class="hm-list"><li><b>Vote</b> on the candidate of the day, or review more in <a href="#play" data-tabgo="play">Play & review</a>.</li>
        <li><b>Measure a transit</b> for NASA's Exoplanet Watch with free telescope data (see the Exoplanet Watch tab).</li>
        <li><b>Suggest ideas or report mistakes</b> with the 💬 button under any box${(D().site || {}).repo ? `, or on <a href="https://github.com/${esc(D().site.repo)}/issues" target="_blank" rel="noopener">GitHub</a>` : ''}.</li>
        <li><b>Follow along:</b> add <a href="feed.xml">the feed</a> to a news reader, or put this page on your phone's home screen.</li></ul></div>`}`;
    dailyCard(slots.querySelector('#today-daily'));
    const sg = window.SpaceLabStargazing, p = sg && sg.place();
    if (p && sg.wireTonight) sg.wireTonight(slots.querySelector('#hm-tonight'), p);
    slots.querySelectorAll('.hm-act').forEach(b => b.addEventListener('click', () => {
      if (b.dataset.place && sg) return sg.openPlace();
      if (b.dataset.tab) return goTab(b.dataset.tab);
      const t = document.getElementById(b.dataset.go); if (t && t.scrollIntoView) t.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }));
    slots.querySelectorAll('.hm-go').forEach(b => b.addEventListener('click', () => goTab(b.dataset.tab)));
    slots.querySelectorAll('[data-tabgo]').forEach(a => a.addEventListener('click', ev => { ev.preventDefault(); goTab(a.dataset.tabgo); }));
  }

  // ------------------------------------------------------------------ Results tab
  function resultRows(rs, edit) {
    return rs.map(r => `<tr><td class="mono small">${day(r.created)}</td><td>${KIND[r.kind] ? KIND[r.kind][0] : ''} <b>${esc(r.title)}</b><div class="small muted">${esc(r.dest || '')}${r.note ? ' · ' + esc(r.note) : ''}</div></td>
      <td><span class="chip ${/confirmed|accepted|published/.test(r.status) ? 'ok' : /not accepted|withdrawn/.test(r.status) ? 'bad' : 'warn'}">${esc(r.status)}</span>${edit && !r.public ? ' <span class="chip">private</span>' : ''}</td>
      <td>${r.link ? `<a href="${esc(r.link)}" target="_blank" rel="noopener">link ↗</a>` : ''}${edit ? ` <button type="button" class="b hm-edit" data-id="${r.id}">Edit</button>` : ''}</td></tr>`).join('');
  }
  function resultsTab(el) {
    const rs = D().results || [];
    el.innerHTML = `<div class="card"><h3>🏆 Results</h3><p>Everything this lab has sent to professional science databases, and what happened to it. Most citizen-science results are small pieces of bigger studies: a transit time that keeps a planet's schedule accurate, an old position that sharpens an asteroid's orbit.</p>${resultsSummary().replace(/<h3>.*?<\/h3>/, '').replace(/<button[^>]*hm-go[^>]*>.*?<\/button>/, '')}</div>
      <div class="card hm-results">${rs.length ? `<table class="compact"><tr><th>date</th><th>what</th><th>status</th><th></th></tr>${resultRows(rs, false)}</table>` : '<p class="muted">Nothing submitted yet.</p>'}</div>
      <div class="card"><h3>Where results go</h3><ul class="hm-list"><li><b>AAVSO Exoplanet Database</b>: transit times, used by NASA's Exoplanet Watch to keep planets' schedules accurate for big telescopes.</li>
      <li><b>ExoFOP (Community TOIs)</b>: planet candidates that the TESS team and astronomers can follow up.</li><li><b>Minor Planet Center</b>: asteroid positions, including old ones found in archive images.</li>
      <li><b>AAVSO VSX</b>: newly noticed variable stars, such as pairs of stars that eclipse each other.</li></ul></div>`;
    if (S.local && S.results) ownerResults(el);
  }
  function ownerResults(el) {           // Meridian only: add and edit entries
    const box = document.createElement('div'); box.className = 'card hm-owner';
    el.querySelector('.hm-results').after(box);
    const form = (r = {}) => `<h3>${r.id ? 'Edit entry' : 'Add a result'}</h3><div class="bx-form">
      <label>Type <select class="hm-f-kind">${Object.entries(KIND).map(([k, v]) => `<option value="${k}" ${r.kind === k ? 'selected' : ''}>${v[1]}</option>`).join('')}</select></label>
      <label>Target <input class="hm-f-target" value="${esc(r.target || '')}" placeholder="e.g. TIC 123456 or WASP-12 b"></label>
      <label style="flex:1;min-width:240px">What <input class="hm-f-title" value="${esc(r.title || '')}" placeholder="e.g. Transit timing of WASP-12 b" style="max-width:none;width:100%"></label>
      <label>Status <select class="hm-f-status">${['submitted', 'accepted', 'published', 'confirmed', 'not accepted', 'withdrawn'].map(s => `<option ${r.status === s ? 'selected' : ''}>${s}</option>`).join('')}</select></label>
      <label style="flex:1;min-width:240px">Link (optional) <input class="hm-f-link" value="${esc(r.link || '')}" placeholder="link to the record (optional)" style="max-width:none;width:100%"></label>
      <label style="flex:1;min-width:240px">Note (public) <input class="hm-f-note" value="${esc(r.note || '')}" style="max-width:none;width:100%"></label>
      <label><span><input type="checkbox" class="hm-f-public" ${r.public === 0 ? '' : 'checked'}> show on the public page</span></label></div>
      <div class="row"><button type="button" class="b primary hm-f-save">Save</button>${r.id ? '<button type="button" class="b bad hm-f-del">Delete</button><button type="button" class="b hm-f-new">New entry</button>' : ''}<span class="small hm-f-msg"></span></div>
      <h3 style="margin-top:14px">Everything in your log</h3><div class="hm-all small muted">loading…</div>`;
    const render = (r) => {
      box.innerHTML = form(r);
      const v = c => box.querySelector(c);
      v('.hm-f-save').addEventListener('click', () => {
        const e = { kind: v('.hm-f-kind').value, target: v('.hm-f-target').value, title: v('.hm-f-title').value, status: v('.hm-f-status').value,
                    link: v('.hm-f-link').value, note: v('.hm-f-note').value, public: v('.hm-f-public').checked };
        if (!e.title.trim()) { v('.hm-f-msg').textContent = 'Say what was submitted.'; return; }
        S.results.save(r && r.id, e).then(() => { v('.hm-f-msg').textContent = 'Saved ✓'; setTimeout(() => resultsTab(el), 600); }, err => { v('.hm-f-msg').textContent = err.message; });
      });
      const del = v('.hm-f-del');
      if (del) del.addEventListener('click', () => {
        if (del.dataset.sure) S.results.remove(r.id).then(() => resultsTab(el));
        else { del.dataset.sure = 1; del.textContent = 'Click again to delete'; }
      });
      const nw = v('.hm-f-new'); if (nw) nw.addEventListener('click', () => render({}));
      S.results.list().then(x => {
        const all = x.items || [];
        v('.hm-all').innerHTML = all.length ? `<table class="compact">${resultRows(all, true)}</table>` : 'Empty. Exoplanet Watch uploads you mark as done are added automatically; CTOIs and MPC reports via "I submitted it".';
        box.querySelectorAll('.hm-edit').forEach(b => b.addEventListener('click', () => render(all.find(a => String(a.id) === b.dataset.id))));
      });
    };
    render({});
  }

  // ------------------------------------------------------------------ TESS tab: the big picture and what's next
  function funnelSVG(f) {
    const steps = [[f.stars, 'stars searched'], [f.signals, 'had repeating dips or a lone dip'], [f.signals - f.auto_rejected, 'passed the automatic checks'],
                   [f.review + f.planet_like, 'still in the running'], [f.planet_like, 'marked planet-like by the volunteer']];
    const max = Math.max(1, steps[0][0]);
    return `<svg class="hm-funnel" viewBox="0 0 600 ${steps.length * 38}" role="img" aria-label="from stars searched to planet-like signals">${steps.map(([n, label], i) => {
      const w = Math.max(6, Math.sqrt((n || 0) / max) * 380);
      return `<rect x="${(380 - w) / 2}" y="${i * 38 + 4}" width="${w.toFixed(1)}" height="28" rx="5" class="hm-f${i}"/><text x="392" y="${i * 38 + 23}" class="hm-ft"><tspan font-weight="bold">${Number(n || 0).toLocaleString()}</tspan> ${esc(label)}</text>`;
    }).join('')}</svg>`;
  }
  function bigPicture() {
    const f = (D().tess || {}).funnel;
    if (!f || !f.stars) return '';
    return `<div class="card hm-big"><h3>🔭 The big picture</h3><p class="small">Why so few planets? Most dips in a star's light are something else. This is what has happened to every signal this computer found:</p>${funnelSVG(f)}
      <details class="small"><summary><b>Not every dip is a planet</b>: what else makes a star dim?</summary><ul class="hm-list">
        <li><b>Two stars eclipsing each other</b>: V-shaped dips, and odd and even dips of different depths. By far the most common impostor.</li>
        <li><b>A different star nearby</b>: TESS pixels are big (21 arc-seconds), so a faint eclipsing pair next door can fake a dip. The centroid test checks this.</li>
        <li><b>Starspots and pulsations</b>: the star itself changes brightness in waves, not sharp dips.</li>
        <li><b>The spacecraft</b>: thruster firings, scattered sunlight and gaps in the data can all leave dips.</li>
        <li><b>A real planet</b>: flat-bottomed, the same depth every time, and it comes from the target star. Even then, astronomers confirm it with other telescopes before calling it a planet.</li></ul></details></div>`;
  }
  function nextTransits() {
    const now = Date.now(), btjdNow = now / 864e5 + 2440587.5 - 2457000;
    const up = ((D().tess || {}).cands || []).filter(c => c.kind === 'periodic' && c.period && c.t0).map(c => {
      const n = Math.ceil((btjdNow - c.t0) / c.period);
      return { c, ms: (c.t0 + n * c.period + 2457000 - 2440587.5) * 864e5, epochs: n };
    }).sort((a, b) => a.ms - b.ms).slice(0, 5);
    if (!up.length) return '';
    return `<div class="card hm-next"><h3>⏳ Next predicted dips</h3><p class="small">If these signals are real, each star dims again at these times. Predictions get less certain the longer it has been since TESS watched (could be off by hours).</p>
      <table class="compact">${up.map(x => `<tr><td><a href="#tic-${x.c.tic}" data-tic-go="${x.c.tic}">TIC ${x.c.tic}</a></td><td>${new Date(x.ms).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</td>
        <td class="mono hm-count" data-ms="${Math.round(x.ms)}"></td><td class="small muted">${Math.round(x.c.depth * 1e6)} ppm${x.c.depth >= 0.005 ? ' · deep enough for a good amateur telescope' : ''}</td></tr>`).join('')}</table></div>`;
  }
  function tickCounts(el) {
    const tick = () => {
      if (!el.isConnected) return clearInterval(t);
      el.querySelectorAll('.hm-count').forEach(c => { const s = (+c.dataset.ms - Date.now()) / 1000; c.textContent = s <= 0 ? 'now' : `in ${Math.floor(s / 3600)}h ${Math.floor(s % 3600 / 60)}m`; });
    };
    const t = setInterval(tick, 30000); tick();
  }
  function tessTop(el) {
    if (el.querySelector('.hm-big, .hm-next')) return;
    const box = document.createElement('div'); box.className = 'hm-tesstop';
    box.innerHTML = `<div class="grid g2">${bigPicture()}${nextTransits()}</div>`;
    const anchor = el.querySelector('.card');
    if (anchor) anchor.after(box); else el.prepend(box);
    tickCounts(box);
    box.querySelectorAll('[data-tic-go]').forEach(a => a.addEventListener('click', ev => {
      const card = el.querySelector(`.card[data-tic="${a.dataset.ticGo}"]`);
      if (card) { ev.preventDefault(); if (card.scrollIntoView) card.scrollIntoView({ block: 'start' }); card.classList.add('flash'); }
    }));
  }

  // ------------------------------------------------------------------ "Suggest an improvement" on every box
  const SKIP = '.task, .jobcard, #todayBody .card, [data-tic], [data-pc], .hm-you, .sl-cand';   // not on candidates, tasks or job status
  const tabTitle = tab => { const b = document.querySelector(`nav.tabs button[data-tab="${tab === 'brief' && !document.querySelector('nav.tabs button[data-tab="brief"]') ? 'arxiv' : tab}"]`); return b ? b.textContent.replace(/\d+$/, '').trim() : tab; };
  const clean = t => String(t || '').replace(/\p{Extended_Pictographic}|️/gu, '').replace(/\s+/g, ' ').trim();
  function addSuggest(tab, el) {
    if (!el || tab === 'setup') return;
    const sug = ((D().community || {}).suggestions) || {};
    el.querySelectorAll('.card').forEach(card => {
      if (card.matches(SKIP) || card.parentElement.closest('.card') || card.querySelector(':scope > .sl-suggest')) return;
      const h = card.querySelector('h3');
      if (!h) return;
      const section = `${clean(tabTitle(tab))} › ${clean(h.textContent)}`.slice(0, 80), list = sug[section] || [];
      const f = document.createElement('div'); f.className = 'sl-suggest';
      f.innerHTML = `<button type="button" class="sl-sugbtn">💬 Suggest an improvement${list.length ? ` (${list.length})` : ''}</button>`;
      card.appendChild(f);
      f.querySelector('.sl-sugbtn').addEventListener('click', () => {
        if (f.querySelector('.sl-sugform')) { f.querySelector('.sl-sugform').remove(); return; }
        const form = document.createElement('div'); form.className = 'sl-sugform';
        form.innerHTML = `${list.length ? `<ul class="sl-suglist">${list.map(x => `<li><b>${esc(x.user)}</b>: ${esc(x.text)} ${x.url ? `<a href="${esc(x.url)}" target="_blank" rel="noopener">↗</a>` : ''}${x.state === 'closed' ? ' <span class="chip ok">done</span>' : ''}</li>`).join('')}</ul>` : ''}
          <textarea rows="2" placeholder="How could “${esc(clean(h.textContent))}” be better?"></textarea>
          <div class="row"><a class="b primary sl-sugsend" target="_blank" rel="noopener">Send on GitHub</a><button type="button" class="b sl-sugcopy">Copy for Discord</button><span class="small muted sl-sugmsg"></span></div>
          <p class="small muted">Suggestions are public on GitHub (free account needed) or paste them in the Discord channel.</p>`;
        f.appendChild(form);
        const ta = form.querySelector('textarea'), send = form.querySelector('.sl-sugsend');
        const link = () => { send.href = `${ISSUES()}?template=suggestion.yml&title=${encodeURIComponent('Suggestion: ' + section)}&section=${encodeURIComponent(section)}&idea=${encodeURIComponent(ta.value.slice(0, 1500))}`; };
        ta.addEventListener('input', link); link();
        send.addEventListener('click', () => S.track('suggest', true));
        form.querySelector('.sl-sugcopy').addEventListener('click', () => {
          const txt = `💡 Space Lab suggestion for "${section}": ${ta.value}`;
          try { navigator.clipboard && navigator.clipboard.writeText(txt); } catch (_) {}
          form.querySelector('.sl-sugmsg').textContent = 'Copied: paste it in Discord.';
        });
        ta.focus && ta.focus();
      });
    });
  }

  // ------------------------------------------------------------------ Space brief: explore the research news
  function newsExplorer(el) {
    const host = el.querySelector('#paperNews');
    if (!host || host.querySelector('.hm-news')) return;
    const b = D().brief || {}, sets = { paper: b.paper_news || [], all: (b.paper_news || []).concat(b.news || []) };
    const st = { set: 'paper', days: 0, topic: '', q: '' };
    host.innerHTML = `<div class="hm-news"><p class="small muted">Stories from popular science outlets and research news. Pick what you want to see.</p>
      <div class="row hm-nf"><input type="search" class="hm-nq" placeholder="Search, e.g. black hole, Mars, JWST" style="flex:1;min-width:200px">
        <select class="hm-nset"><option value="paper">Stories that cite a research paper</option><option value="all">All space news this week</option></select>
        <select class="hm-ndays"><option value="0">Any time</option><option value="1">Last 24 hours</option><option value="3">Last 3 days</option><option value="7">Last week</option></select></div>
      <div class="row hm-ntopics"></div><ul class="list hm-nlist"></ul></div>`;
    const draw = () => {
      const items = sets[st.set].filter(n => (!st.days || (n.date && n.date > Date.now() / 1000 - st.days * 86400)) && (!st.topic || n.topic === st.topic) &&
        (!st.q || (n.title + ' ' + (n.source || '') + ' ' + (n.papers || []).map(p => p.title).join(' ')).toLowerCase().includes(st.q))).sort((a, b) => (b.date || 0) - (a.date || 0));
      const topics = [...new Set(sets[st.set].map(n => n.topic).filter(Boolean))];
      host.querySelector('.hm-ntopics').innerHTML = `<button type="button" class="b hm-nt ${st.topic ? '' : 'primary'}" data-t="">All topics</button>` + topics.map(t => `<button type="button" class="b hm-nt ${st.topic === t ? 'primary' : ''}" data-t="${esc(t)}">${esc(t)}</button>`).join('');
      host.querySelectorAll('.hm-nt').forEach(x => x.addEventListener('click', () => { st.topic = x.dataset.t; draw(); }));
      host.querySelector('.hm-nlist').innerHTML = items.slice(0, 60).map(n => `<li><span><a href="${esc(n.url)}" target="_blank" rel="noopener"><b>${esc(n.title)}</b></a>
        <br><span class="small muted">${esc(n.source || '')}${n.date ? ' · ' + ago(n.date) : ''}${n.topic ? ` · <span class="chip">${esc(n.topic)}</span>` : ''}</span>
        ${(n.papers || []).length ? `<br><span class="small">📄 ${(n.papers || []).map(p => `<a href="${esc(p.url)}" target="_blank" rel="noopener">${esc(p.title || p.id)}</a>${p.venue ? ` <span class="muted">(${esc(p.venue)}${p.year ? ', ' + p.year : ''})</span>` : ''}`).join(' · ')}</span>` : ''}</span></li>`).join('') || '<li class="muted">Nothing matches. Try another topic or a longer time.</li>';
    };
    host.querySelector('.hm-nq').addEventListener('input', e => { st.q = e.target.value.trim().toLowerCase(); draw(); });
    host.querySelector('.hm-nset').addEventListener('change', e => { st.set = e.target.value; st.topic = ''; draw(); });
    host.querySelector('.hm-ndays').addEventListener('change', e => { st.days = +e.target.value; draw(); });
    draw();
  }

  // ------------------------------------------------------------------ wiring
  S.addTab('results', 'Results', resultsTab);
  S.onRender((tab, el) => {
    if (!el) return;
    if (tab === 'today') home(el);
    if (tab === 'tess') tessTop(el);
    if (tab === 'brief') newsExplorer(el);
    setTimeout(() => addSuggest(tab, el), 0);       // after every other extra has added its boxes
    if (tab === 'brief' && !el.querySelector('.hm-radar-card')) {
      const card = document.createElement('div'); card.className = 'card hm-radar-card'; card.innerHTML = radarCard(true);
      const anchor = el.querySelector('#arxivTop') || el.querySelector('.card');
      if (anchor) anchor.after(card); else el.prepend(card);
      wireRadar(card);
    }
  });
  window.SpaceLabHome = { streak };
})();
