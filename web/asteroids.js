// Space Lab asteroid guide: close approaches made easy to picture (size, speed, distance), famous asteroids with
// plain-English summaries and the scientific papers about them (via Wikipedia), and links to 3D orbit viewers.
// Runs on the public page AND inside Meridian through window.SpaceLab (see EXTRAS.md).
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const D = () => S.data || {};
  const LD_AU = 0.00256955529;
  const MON = { Jan: 0, Feb: 1, Mar: 2, Apr: 3, May: 4, Jun: 5, Jul: 6, Aug: 7, Sep: 8, Oct: 9, Nov: 10, Dec: 11 };
  const when = cd => { const m = /^(\d{4})-(\w{3})-(\d{2}) (\d{2}):(\d{2})/.exec(cd || ''); return m ? Date.UTC(+m[1], MON[m[2]], +m[3], +m[4], +m[5]) : null; };
  // size from brightness (H): dark rock (5% albedo) to shiny rock (25%)
  const sizeRange = h => [1329 / Math.sqrt(0.25) * Math.pow(10, -h / 5) * 1000, 1329 / Math.sqrt(0.05) * Math.pow(10, -h / 5) * 1000];
  const fmtM = m => m >= 1000 ? num(m / 1000, 1) + ' km' : Math.round(m) + ' m';
  const likeA = m => m < 8 ? 'a car' : m < 25 ? 'a bus or a house' : m < 60 ? 'a 15-storey building' : m < 120 ? 'a football field' : m < 300 ? 'a stadium' : m < 1000 ? 'a few city blocks' : 'a mountain';
  const jpl = des => `https://ssd.jpl.nasa.gov/tools/sbdb_lookup.html#/?sstr=${encodeURIComponent(String(des || '').replace(/[()]/g, '').trim())}`;
  const wikiFor = des => { const W = ((D().beyond || {}).wiki || {}).items || {}, k = String(des || '').replace(/[()]/g, '').trim(); return W[k] || null; };
  const sentences = (t, n) => (String(t || '').match(/[^.!?]+[.!?]+/g) || [t || '']).slice(0, n).join(' ');

  function distanceBar(ld) {      // Earth, the Moon's distance (1 LD) and the asteroid, on a log scale up to 30 LD
    const x = v => 12 + Math.log10(1 + Math.min(v, 30)) / Math.log10(31) * 296;
    return `<svg class="as-dist" viewBox="0 0 320 44" role="img" aria-label="closest distance ${num(ld, 1)} times the Moon's distance">
      <line x1="12" y1="22" x2="308" y2="22" class="as-axis"/><circle cx="12" cy="22" r="7" class="as-earth"/><text x="12" y="42" text-anchor="middle">Earth</text>
      <circle cx="${x(1).toFixed(1)}" cy="22" r="3.5" class="as-moon"/><text x="${x(1).toFixed(1)}" y="42" text-anchor="middle">Moon</text>
      <circle cx="${x(ld).toFixed(1)}" cy="22" r="4" class="as-rock"/><text x="${x(ld).toFixed(1)}" y="12" text-anchor="middle">${ld < 30 ? num(ld, 1) + ' LD' : '30+ LD'}</text></svg>`;
  }
  function flybyCard(r) {
    const au = parseFloat(r.dist), v = parseFloat(r.v_rel), h = parseFloat(r.h), t = when(r.cd);
    if (!au) return '';
    const ld = au / LD_AU, [s0, s1] = isNaN(h) ? [null, null] : sizeRange(h), w = wikiFor(r.des);
    const dt = t ? (t - Date.now()) / 864e5 : null;
    return `<div class="as-fly"><div class="row"><b>${esc(r.des)}</b><span class="spacer"></span><span class="small muted">${t ? new Date(t).toLocaleDateString([], { month: 'short', day: 'numeric' }) : esc(r.cd)}${dt !== null ? ` · ${dt > 1 ? 'in ' + Math.round(dt) + ' days' : dt > 0 ? 'within a day' : 'just passed'}` : ''}</span></div>
      ${distanceBar(ld)}
      <div class="small">${s0 ? `About <b>${fmtM(s0)}–${fmtM(s1)}</b> across: the size of ${likeA(Math.sqrt(s0 * s1))}. ` : ''}Passes <b>${num(ld, 1)}×</b> the Moon's distance (${Math.round(au * 1.496e8).toLocaleString()} km)${v ? ` at <b>${Math.round(v * 3600).toLocaleString()} km/h</b>, ${Math.round(v / 0.9)}× faster than a rifle bullet` : ''}.</div>
      <div class="small"><a href="${jpl(r.des)}" target="_blank" rel="noopener">its orbit (NASA JPL) ↗</a>${w ? ` · <a href="${esc(w.url)}" target="_blank" rel="noopener">Wikipedia ↗</a>` : ''}</div></div>`;
  }
  function famousCard(key, w) {
    return `<details class="as-famous"><summary><b>${esc(w.title)}</b> <span class="small muted">${esc(sentences(w.extract, 1))}</span></summary>
      <p>${esc(sentences(w.extract, 4))} <a href="${esc(w.url)}" target="_blank" rel="noopener">Wikipedia ↗</a> · <a href="${jpl(key)}" target="_blank" rel="noopener">orbit ↗</a></p>
      ${(w.citations || []).length ? `<div class="small"><b>What scientists have published</b> (cited on Wikipedia):<ol>${w.citations.map(c => `<li>${esc(c.text)} <a href="${esc(c.url)}" target="_blank" rel="noopener">read ↗</a></li>`).join('')}</ol></div>` : ''}</details>`;
  }
  function guide(el) {
    if (el.querySelector('.as-guide')) return;
    const a = D().asteroids || {}, wiki = ((D().beyond || {}).wiki || {}).items || {};
    const fly = (a.cad || []).slice().sort((x, y) => parseFloat(x.dist) - parseFloat(y.dist)).slice(0, 6);
    const famous = Object.entries(wiki).filter(([, w]) => w.famous);
    const listed = Object.entries(wiki).filter(([, w]) => !w.famous);
    const box = document.createElement('div'); box.className = 'as-guide';
    box.innerHTML = `<div class="card"><h3>☄️ Asteroids passing Earth, made easy to picture</h3>
        <p class="small">Close approaches in the next weeks, closest first. "LD" is the Moon's distance (384,400 km). Sizes are estimated from how bright each one looks, so they're a range. None of these will hit Earth.</p>
        <div class="as-flys">${fly.map(flybyCard).join('') || '<p class="muted small">No close approaches listed right now.</p>'}</div>
        <p class="small"><a href="https://eyes.nasa.gov/apps/asteroids/" target="_blank" rel="noopener">Watch every known near-Earth asteroid fly around the Sun in 3D (NASA Eyes) ↗</a></p></div>
      ${famous.length ? `<div class="card"><h3>🌟 Famous asteroids and what we've learned</h3><p class="small">Summaries and the research papers behind them, from Wikipedia's citations. Tap one to open.</p>${famous.map(([k, w]) => famousCard(k, w)).join('')}</div>` : ''}
      ${listed.length ? `<div class="card"><h3>📚 On this page's lists</h3>${listed.map(([k, w]) => famousCard(k, w)).join('')}</div>` : ''}
      <div class="card"><h3>❓ How dangerous is it?</h3><ul class="hm-list small">
        <li><b>Torino scale (0–10):</b> 0 means no hazard. Almost every object is 0; a 1 usually drops to 0 once more observations come in.</li>
        <li><b>Palermo scale:</b> compares the risk with the normal background chance of an impact. Below −2: nothing to worry about.</li>
        <li><b>Why old photos help:</b> an asteroid seen years ago gives a much longer track, so its future path can be pinned down. That's what this lab's precovery search does.</li>
        <li><b>13 April 2029:</b> asteroid Apophis (about 340 m) passes closer than many satellites, about 32,000 km above Earth, bright enough to see without a telescope from Europe and Africa. It will not hit.</li></ul></div>`;
    const first = el.querySelector('.card');
    if (first) first.after(box); else el.prepend(box);
  }
  S.onRender((tab, el) => { if (tab === 'asteroids' && el) guide(el); });
})();
