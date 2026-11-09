// Space Lab "Sky watch" and "Beyond astronomy" tabs (biology, chemistry, engineering and Earth science that touch space).
// Runs on the public page AND on the local Space Lab page through window.SpaceLab (see EXTRAS.md). All live numbers come from
// data.json (the "beyond" job on the volunteer's Mac fetches them from NASA, NOAA, JPL, USGS, LIGO and others).
// Your location is only ever used inside your own browser (ISS passes), never sent anywhere.
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const B = () => ((S.data || {}).beyond) || {};
  const D2R = Math.PI / 180;
  const ago = t => { if (!t) return 'not yet'; const s = Date.now() / 1000 - t; return s < 5400 ? Math.round(s / 60) + ' min ago' : s < 129600 ? Math.round(s / 3600) + ' h ago' : Math.round(s / 86400) + ' days ago'; };
  const dt = (x, opt) => { const d = new Date(x); return isNaN(d) ? esc(x || '—') : d.toLocaleString([], opt || { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }); };
  const utc = s => (s && !/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s.replace(' ', 'T') + 'Z' : s);   // NOAA/JPL times are UTC without a zone
  const mjdDate = m => m ? new Date((m - 40587) * 864e5).toLocaleDateString([], { month: 'short', day: 'numeric', year: 'numeric' }) : '—';
  const link = (u, t) => /^https?:\/\//i.test(String(u || '')) ? `<a href="${esc(u)}" target="_blank" rel="noopener">${t} ↗</a>` : t;   // web links only
  const card = (title, body, cls = '') => `<div class="card ${cls}"><h3>${title}</h3>${body}</div>`;
  const none = what => `<p class="muted small">No ${what} data yet: it appears after the next "Sky watch & Beyond astronomy" run.</p>`;
  const compass = az => ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'][Math.round(((az % 360) + 360) % 360 / 45) % 8];

  // ------------------------------------------------------------------ small drawing helpers
  function moonSVG(illum, waxing) {
    // lit part drawn as two arcs; right side lit when waxing (northern-hemisphere view)
    const r = 40, k = 1 - 2 * illum, sx = Math.abs(k) * r;
    const lit = waxing ? 1 : 0;
    const d = `M 50 ${50 - r} A ${r} ${r} 0 0 ${lit} 50 ${50 + r} A ${sx} ${r} 0 0 ${(k > 0) === !!waxing ? 0 : 1} 50 ${50 - r} Z`;
    return `<svg class="bx-moon" viewBox="0 0 100 100" width="90" height="90" role="img" aria-label="Moon ${Math.round(illum * 100)}% lit"><circle cx="50" cy="50" r="${r}" fill="#2a2f38"/><path d="${d}" fill="#e9e4cf"/></svg>`;
  }
  function lineSVG(pts, opt = {}) {   // pts: [[x, y]], numbers
    const w = opt.w || 640, h = opt.h || 160, p = 30;
    const xs = pts.map(q => q[0]), ys = pts.map(q => opt.log ? Math.log10(q[1]) : q[1]);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = opt.ymin ?? Math.min(...ys), y1 = opt.ymax ?? Math.max(...ys);
    const X = x => p + (x - x0) / ((x1 - x0) || 1) * (w - p - 6), Y = y => h - 18 - (y - y0) / ((y1 - y0) || 1) * (h - 28);
    const path = pts.map((q, i) => `${i ? 'L' : 'M'}${X(q[0]).toFixed(1)},${Y(ys[i]).toFixed(1)}`).join('');
    const bands = (opt.bands || []).map(([v, lab]) => `<line x1="${p}" x2="${w - 6}" y1="${Y(v)}" y2="${Y(v)}" class="bx-grid"/><text x="2" y="${Y(v) + 4}" class="bx-ax">${lab}</text>`).join('');
    return `<svg class="bx-line" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="${esc(opt.label || 'chart')}">${bands}<path d="${path}" class="bx-path"/></svg>`;
  }
  function worldMap(dots, cls) {    // dots: {lat, lon, r, title, cls}
    const earth = (B().earth || []).find(e => e.key === 'day') || (B().earth || []).find(e => e.key === 'night');
    return `<div class="bx-map ${cls || ''}">${earth ? `<img src="${esc(earth.img)}" alt="map of Earth">` : '<div class="bx-mapbg"></div>'}${dots.filter(d => d.lat !== null && d.lat !== undefined && d.lon !== null && d.lon !== undefined)
      .map(d => `<span class="bx-dot ${d.cls || ''}" title="${esc(d.title || '')}" style="left:${((d.lon + 180) / 360 * 100).toFixed(2)}%;top:${((90 - d.lat) / 180 * 100).toFixed(2)}%;width:${d.r || 8}px;height:${d.r || 8}px"></span>`).join('')}</div>`;
  }

  // ------------------------------------------------------------------ ISS passes, computed in the visitor's browser
  function gmst(ms) { const jd = ms / 864e5 + 2440587.5, d = jd - 2451545; return ((280.46061837 + 360.98564736629 * d) % 360) * D2R; }
  function sunECEF(ms) {
    const n = ms / 864e5 + 2440587.5 - 2451545, g = (357.528 + 0.9856003 * n) * D2R;
    const lam = (280.46 + 0.9856474 * n + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * D2R, eps = (23.439 - 4e-7 * n) * D2R;
    const x = Math.cos(lam), y = Math.cos(eps) * Math.sin(lam), z = Math.sin(eps) * Math.sin(lam), th = gmst(ms);
    return [x * Math.cos(th) + y * Math.sin(th), -x * Math.sin(th) + y * Math.cos(th), z];
  }
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  function issPasses(iss, lat, lon, minEl = 10, includePast = false) {
    const a = 6378.137, e2 = 0.00669438, la = lat * D2R, lo = lon * D2R, N = a / Math.sqrt(1 - e2 * Math.sin(la) ** 2);
    const o = [N * Math.cos(la) * Math.cos(lo), N * Math.cos(la) * Math.sin(lo), N * (1 - e2) * Math.sin(la)];
    const up = [Math.cos(la) * Math.cos(lo), Math.cos(la) * Math.sin(lo), Math.sin(la)], east = [-Math.sin(lo), Math.cos(lo), 0];
    const north = [-Math.sin(la) * Math.cos(lo), -Math.sin(la) * Math.sin(lo), Math.cos(la)];
    const P = iss.xyz, sub = 6, passes = [];
    let cur = null;
    for (let i = 0; i < P.length - 1; i++) {
      for (let k = 0; k < sub; k++) {
        const f = k / sub, p = [0, 1, 2].map(j => P[i][j] + (P[i + 1][j] - P[i][j]) * f), ms = (iss.t0 + (i + f) * iss.step) * 1000;
        const d = [p[0] - o[0], p[1] - o[1], p[2] - o[2]], rng = Math.hypot(...d);
        const el = Math.asin(dot(d, up) / rng) / D2R, az = (Math.atan2(dot(d, east), dot(d, north)) / D2R + 360) % 360;
        if (el >= minEl) {
          if (!includePast && ms < Date.now() - 60000) continue;
          const sun = sunECEF(ms), s = dot(p, sun), lit = s > 0 || Math.hypot(p[0] - s * sun[0], p[1] - s * sun[1], p[2] - s * sun[2]) > 6371;
          const dark = Math.asin(dot(sun, up)) / D2R < -6;
          if (!cur) cur = { start: ms, az0: az, max: el, tmax: ms, visible: false };
          if (el > cur.max) { cur.max = el; cur.tmax = ms; cur.azmax = az; }
          cur.end = ms; cur.az1 = az; cur.visible = cur.visible || (lit && dark);
        } else if (cur) { passes.push(cur); cur = null; }
      }
    }
    if (cur) passes.push(cur);
    return passes;
  }
  function issNow(iss) {
    const i = Math.floor((Date.now() / 1000 - iss.t0) / iss.step);
    if (i < 0 || i >= iss.xyz.length) return null;
    const [x, y, z] = iss.xyz[i];
    return { lat: Math.atan2(z, Math.hypot(x, y)) / D2R, lon: Math.atan2(y, x) / D2R };
  }
  function issCard() {
    const iss = B().iss;
    if (!iss || !iss.xyz) return card('🛰 International Space Station passes', none('ISS'));
    const now = issNow(iss);
    return card('🛰 When can I see the Space Station?', `<p class="small">The ISS is the third-brightest thing in the night sky. It shows up as a steady, fast-moving star (no blinking) for a few minutes, just after sunset or before sunrise.</p>
      ${now ? worldMap([{ lat: now.lat, lon: now.lon, r: 14, cls: 'iss', title: 'the ISS right now' }], 'small') + `<p class="small muted">Right now it is over ${num(Math.abs(now.lat), 0)}°${now.lat >= 0 ? 'N' : 'S'}, ${num(Math.abs(now.lon), 0)}°${now.lon >= 0 ? 'E' : 'W'} (yellow dot), moving at about 28,000 km/h.</p>` : ''}
      <button type="button" class="b primary bx-issgo">Find passes over me</button><div class="bx-issout"></div>
      <p class="small muted">Your location stays in your browser: the calculation happens on this page. Orbit data from CelesTrak, epoch ${esc(iss.epoch || '')} UTC; times are good to about a minute.</p>`, 'bx-iss');
  }
  function wireISS(el) {
    const b = el.querySelector('.bx-issgo'); if (!b) return;
    b.addEventListener('click', () => {
      const out = el.querySelector('.bx-issout');
      let saved = null; try { saved = JSON.parse(localStorage.getItem('sl_place')); } catch (_) { /* none */ }
      const show = (lat, lon) => {
        const ps = issPasses(B().iss, lat, lon), vis = ps.filter(p => p.visible);
        const row = p => `<tr><td>${dt(p.start, { weekday: 'short', hour: '2-digit', minute: '2-digit' })}</td><td class="mono">${Math.round((p.end - p.start) / 60000) || 1} min</td><td class="mono">${Math.round(p.max)}°</td><td>${compass(p.az0)} → ${compass(p.az1)}</td><td>${p.visible ? '<span class="chip ok">visible</span>' : '<span class="small muted">daylight or Earth\'s shadow</span>'}</td></tr>`;
        out.innerHTML = ps.length ? `<p>${vis.length ? `<b>${vis.length}</b> visible pass${vis.length > 1 ? 'es' : ''} in the next day and a half.` : 'No visible passes in the next day and a half (the ISS passes over in daylight or in Earth\'s shadow). Check back in a few days.'}</p>
          <table class="compact"><tr><th>starts</th><th>lasts</th><th>highest</th><th>direction</th><th></th></tr>${ps.map(row).join('')}</table>
          <p class="small muted">"Highest" is degrees above the horizon (90° = straight up). Higher passes are brighter. Look toward where it starts.</p>`
          : '<p>No passes above 10° in the next day and a half.</p>';
      };
      if (saved && isFinite(saved.lat)) return show(saved.lat, saved.lon);
      if (!navigator.geolocation) { out.textContent = 'This browser cannot share a location. Set a ZIP code in the Stargazing tab instead.'; return; }
      out.textContent = 'Asking your browser for your location…';
      navigator.geolocation.getCurrentPosition(pos => show(pos.coords.latitude, pos.coords.longitude),
        () => { out.textContent = 'Location not shared, so passes cannot be worked out. Nothing was stored. You can set a ZIP code in the Stargazing tab instead.'; }, { timeout: 15000, maximumAge: 600000 });
    });
  }

  // ------------------------------------------------------------------ Sky watch tab
  function skyCard() {
    const s = B().sky;
    if (!s) return card('🌙 This week\'s sky', none('sky'));
    const waxing = /Waxing|First/.test(s.moon.phase) || (s.moon.phase === 'Full Moon');
    const planets = s.planets.map(p => `<tr><td><b>${esc(p.name)}</b></td><td>${esc(p.where)}</td><td class="mono">${num(p.elongation, 0)}° from the Sun</td></tr>`).join('');
    const showers = (s.showers || []).map(m => `<li><b>${esc(m.name)}</b>: peak ${m.days === 0 ? '<b>tonight</b>' : m.days < 0 ? Math.abs(m.days) + ' days ago (still active)' : m.days === 1 ? 'tomorrow night' : 'in ' + m.days + ' days'} (${esc(m.peak)}), up to ~${m.zhr} meteors an hour under dark skies. Debris from ${esc(m.parent)}.</li>`).join('');
    return card('🌙 This week\'s sky', `<div class="row" style="align-items:center">${moonSVG(s.moon.illumination, waxing)}<div><b>${esc(s.moon.phase)}</b>, ${Math.round(s.moon.illumination * 100)}% lit.<div class="small muted">${s.moon.illumination > 0.7 ? 'Bright moonlight washes out faint stars and meteors this week.' : s.moon.illumination < 0.3 ? 'Dark skies: a good week for faint stars, galaxies and meteors.' : 'Moderate moonlight.'}</div></div></div>
      <table class="compact"><tr><th>planet</th><th>where to look</th><th></th></tr>${planets}</table>
      <h4>Meteor showers</h4>${showers ? `<ul>${showers}</ul>` : '<p class="small muted">No major shower in the next 30 days. A few random meteors cross the sky every hour anyway.</p>'}
      <p class="small muted">Works anywhere on Earth: no location used. Evening planets sit low in the west after sunset; morning planets in the east before sunrise.</p>`);
  }
  let sunSound = null;
  function listenSun(btn) {
    if (sunSound) { try { sunSound.close(); } catch (_) {} sunSound = null; btn.textContent = '🔊 Listen to the Sun'; return; }
    const xr = (B().space_weather || {}).xray || [], AC = window.AudioContext || window.webkitAudioContext;
    if (!xr.length || !AC) return;
    const ctx = new AC(), osc = ctx.createOscillator(), g = ctx.createGain(), T = 8;
    osc.type = 'triangle'; osc.connect(g); g.connect(ctx.destination); g.gain.value = 0.15;
    xr.forEach(([, f], i) => { const lf = Math.max(-8.5, Math.min(-3.5, Math.log10(f))); osc.frequency.setValueAtTime(150 * Math.pow(2, (lf + 8.5) * 0.75), i / xr.length * T); });
    g.gain.setValueAtTime(0, T);
    osc.start(); sunSound = ctx; btn.textContent = '⏹ Stop';
    setTimeout(() => { if (sunSound === ctx) listenSun(btn); }, T * 1000 + 200);
  }
  function weatherCard() {
    const w = B().space_weather;
    if (!w) return card('☀️ Space weather', none('space weather'));
    const kp = (w.kp || []).filter(k => k.kp !== null), last = kp[kp.length - 1];
    const sc = w.scales || {}, lvl = x => (x && x.scale !== null && x.scale !== undefined && x.scale !== '0' && x.scale !== 0) ? `<span class="chip warn">${esc(x.scale)} ${esc(x.text || '')}</span>` : '<span class="chip ok">none</span>';
    const kpBars = kp.map(k => `<span class="bx-kp" style="height:${Math.max(4, k.kp / 9 * 60)}px" data-lvl="${k.kp >= 5 ? 'storm' : k.kp >= 4 ? 'active' : 'quiet'}" title="${esc(k.t)} UTC: Kp ${num(k.kp, 1)}"></span>`).join('');
    const xr = (w.xray || []).filter(x => x[1] > 0).map(([t, f]) => [Date.parse(utc(t)), f]);
    const au = w.aurora_40n;
    return card('☀️ Space weather', `<p class="small">The Sun throws out light flashes (flares) and clouds of charged gas. When those hit Earth's magnetic field they can cause auroras, radio blackouts and trouble for satellites.</p>
      <div class="kv"><b>Geomagnetic activity (Kp)</b><span>${last ? `<b>${num(last.kp, 1)}</b> of 9 (${last.kp >= 5 ? 'storm' : last.kp >= 4 ? 'unsettled' : 'quiet'})` : '—'}</span>
      <b>Radio blackouts</b><span>${lvl(sc.R)}</span><b>Solar radiation storm</b><span>${lvl(sc.S)}</span><b>Geomagnetic storm</b><span>${lvl(sc.G)}</span>
      <b>Aurora chance near 40°N</b><span>${au === null || au === undefined ? '—' : `<b>${au}%</b> ${au >= 10 ? '(worth a look north after dark!)' : '(very unlikely at mid-latitudes)'}`}</span></div>
      ${kpBars ? `<div class="bx-kpbars" aria-label="Kp over the last three days">${kpBars}</div><div class="small muted">Kp every 3 hours, last 3 days. 5 or more = geomagnetic storm.</div>` : ''}
      ${xr.length > 5 ? `<h4>The Sun's X-ray brightness, last 24 hours</h4>${lineSVG(xr, { log: true, ymin: -8.5, ymax: -3.5, label: 'X-ray flux', bands: [[-7, 'B'], [-6, 'C'], [-5, 'M'], [-4, 'X']] })}<div class="small muted">Spikes are solar flares. Letters are flare classes: each is 10× stronger than the one before. X-class flares can black out radio on Earth's day side.</div>
        <button type="button" class="b bx-sun">🔊 Listen to the Sun</button> <span class="small muted">the last day of X-rays as sound: higher pitch = brighter Sun</span>` : ''}
      ${(w.flares || []).length ? `<h4>Recent flares</h4><div>${w.flares.slice(0, 10).map(f => `<span class="chip ${/^X/.test(f.cls) ? 'bad' : /^M/.test(f.cls) ? 'warn' : ''}" title="${esc(f.peak)} UTC">${esc(f.cls)} · ${dt(utc(f.peak), { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })}</span>`).join(' ')}</div>` : ''}
      ${(w.radio || []).length ? `<p class="small">The Sun's radio brightness at 10.7 cm: <b>${num(w.radio[w.radio.length - 1].flux, 0)}</b> solar flux units (about 70 at solar minimum, 200+ near maximum). Radio astronomers have measured it daily since 1947.</p>` : ''}
      <p class="small muted">Data: NOAA Space Weather Prediction Center.</p>`);
  }
  function fireballCard() {
    const f = B().fireballs;
    if (!f) return card('🔥 Fireballs', none('fireball'));
    return card('🔥 Fireballs spotted by government sensors', `<p class="small">Small asteroids burn up in the atmosphere as bright fireballs. US government satellites record the biggest ones; the energy is in kilotons of TNT (the Chelyabinsk fireball in 2013 was about 440).</p>
      ${worldMap(f.map(x => ({ lat: x.lat, lon: x.lon, r: 6 + Math.min(18, Math.sqrt((x.energy_kt || 0.05) * 40)), cls: 'fire', title: `${x.date}: ${num(x.energy_kt, 2)} kt` })), 'small')}
      <table class="compact"><tr><th>date (UTC)</th><th>energy</th><th>height</th><th>speed</th></tr>${f.slice(0, 10).map(x => `<tr><td>${esc(x.date)}</td><td class="mono">${num(x.energy_kt, 2)} kt</td><td class="mono">${x.alt_km ? num(x.alt_km, 0) + ' km' : '—'}</td><td class="mono">${x.vel_kms ? num(x.vel_kms, 1) + ' km/s' : '—'}</td></tr>`).join('')}</table>
      <p class="small muted">Data: NASA/JPL Center for Near-Earth Object Studies.</p>`);
  }
  function cometCard() {
    const c = B().comets;
    if (!c) return card('☄️ Comets near the Sun', none('comet'));
    return card('☄️ Comets near the Sun', `<p class="small">Comets brighten as they swing close to the Sun. Lower "brightness number" = brighter comet; under about 8 is usually a binocular object.</p>
      <table class="compact"><tr><th>comet</th><th>closest to Sun</th><th>distance then</th><th>brightness number</th></tr>${c.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${x.days_to_perihelion === null ? '—' : x.days_to_perihelion < 0 ? Math.round(-x.days_to_perihelion) + ' days ago' : 'in ' + Math.round(x.days_to_perihelion) + ' days'}</td><td class="mono">${num(x.q_au, 2)} AU</td><td class="mono">${num(x.m1, 1)}</td></tr>`).join('')}</table>
      <p class="small muted">AU = Earth–Sun distance. "Brightness number" is the comet's total absolute magnitude (M1) from JPL; real brightness also depends on its distance from us. Data: JPL Small-Body Database.</p>`);
  }
  function gwCard() {
    const g = B().gw;
    if (!g) return card('🌊 Gravitational waves', none('gravitational-wave'));
    return card('🌊 Ripples in space-time', `<p class="small">When black holes or neutron stars crash into each other, they shake space itself. The LIGO, Virgo and KAGRA detectors feel the ripples. These are the latest significant alerts.</p>
      <ul class="list">${g.map(x => `<li><span><b>${link(x.url, esc(x.id))}</b> ${x.retracted ? '<span class="chip">retracted (a false alarm)</span>' : ''}<br><span class="small muted">${dt(utc(x.created))}${x.one_per_years ? ` · a false alarm this strong would happen by chance about once every ${Number(x.one_per_years).toLocaleString()} years` : ''}</span></span></li>`).join('')}</ul>
      <p class="small muted">Data: GraceDB (LIGO/Virgo/KAGRA public alerts).</p>`);
  }
  function snCard() {
    const s = B().supernovae, near = (B().sn_nearby || []).filter(x => x && x.id && isFinite(x.dist));
    const nearHtml = near.length ? `<p class="small" style="margin-top:8px"><b>In nearby galaxies</b> (within ${Math.round(100 * 3.26)} million light-years, matched to the 3D map; the closest are worth a look with a telescope):</p>
      <table class="compact"><tr><th>object</th><th>galaxy</th><th>distance</th><th>first seen</th><th>brightness</th></tr>${near.slice(0, 12).map(x => `<tr><td>${link(x.url, esc(x.id))}</td><td>${esc(x.host || '')}</td>
        <td class="mono">${Math.round(x.dist * 3.2616)} million ly</td><td>${mjdDate(x.first_mjd)}</td><td class="mono">${isFinite(x.mag_last) ? 'mag ' + Number(x.mag_last).toFixed(1) : '—'}</td></tr>`).join('')}</table>` : '';
    if (!s && !near.length) return card('💥 Exploding stars', none('supernova'));
    if (!s) return card('💥 Possible new supernovae', nearHtml + '<p class="small muted">Candidates, not confirmed: confirmation needs a spectrum. Data: ZTF via the ALeRCE broker.</p>');
    return card('💥 Possible new supernovae', `<p class="small">Robotic surveys catch stars exploding in distant galaxies every night. A machine-learning classifier (ALeRCE) flags these as likely Type Ia supernovae: white dwarfs blowing up, used to measure the expansion of the universe.</p>
      <table class="compact"><tr><th>object</th><th>first seen</th><th>classifier</th><th></th></tr>${s.map(x => `<tr><td>${link(x.url, esc(x.id))}</td><td>${mjdDate(x.first_mjd)}</td><td class="mono">${x.prob ? Math.round(x.prob * 100) + '%' : '—'}</td><td>${x.ra !== null ? link(`https://sky.esa.int/esasky/?target=${x.ra}%20${x.dec}&hips=DSS2+color&fov=0.1`, 'sky') : ''}</td></tr>`).join('')}</table>
      ${nearHtml}<p class="small muted">Candidates, not confirmed: confirmation needs a spectrum. Data: ZTF via the ALeRCE broker.</p>`);
  }
  function liveCard() {
    const L = B().live || [];
    if (!L.length) return '';
    return card('📡 Live from space', `<p class="small">The newest pictures from spacecraft watching the Sun and the Earth, refreshed every time this page updates (tap one to enlarge).</p>
      <div class="bx-live">${L.map(x => `<figure><img class="plot" src="${esc(x.img)}" alt="${esc(x.caption)}" loading="lazy"><figcaption class="small">${esc(x.caption)}<br><span class="muted">${esc(x.source)} · ${ago(x.t)}${x.page ? ` · <a href="${esc(x.page)}" target="_blank" rel="noopener">latest at the source ↗</a>` : ''}</span></figcaption></figure>`).join('')}</div>`, 'bx-livecard');
  }
  function skyTab(el) {
    el.innerHTML = `<div class="card"><h3>🔭 Sky watch</h3><p>What's happening up there right now: the Moon and planets, the Space Station, the Sun's mood, fireballs, comets, black-hole collisions and exploding stars. Updated every 3 hours ${B().t ? '(last ' + ago(B().t) + ')' : ''}.</p></div>
      <div class="grid g2">${skyCard()}${issCard()}</div>${liveCard()}${weatherCard()}<div class="grid g2">${fireballCard()}${cometCard()}</div><div class="grid g2">${gwCard()}${snCard()}</div>`;
    wireISS(el);
    const sb = el.querySelector('.bx-sun'); if (sb) sb.addEventListener('click', () => listenSun(sb));
  }

  // ------------------------------------------------------------------ Beyond astronomy: static science notes
  const EXTREMO = [
    ['Tardigrades', 'Half-millimetre "water bears". In 2007 some survived 10 days exposed to open space on the FOTON-M3 mission by drying out and shutting down.'],
    ['Deinococcus radiodurans', 'A bacterium that shrugs off radiation thousands of times the lethal human dose. Pellets of it survived three years outside the Space Station (Japan\'s Tanpopo experiment).'],
    ['Methanopyrus kandleri', 'Lives at deep-sea hot vents; one strain grows at 122 °C, the record for life.'],
    ['Planococcus halocryophilus', 'Grows at −15 °C in Arctic permafrost: a model for life in Mars\'s frozen ground.'],
    ['Salt-loving archaea', 'Thrive in brines ten times saltier than seawater and can survive trapped inside salt crystals. Mars has ancient salt deposits.'],
    ['Lichens', 'Xanthoria elegans survived about 18 months outside the Space Station (ESA\'s EXPOSE-E experiment).']];
  const WHERE = [['Mars', 'Ancient rivers and lakes; maybe salty water underground today. Perseverance is collecting rock samples.'],
    ['Europa (Jupiter)', 'A salty ocean under the ice with more water than Earth\'s oceans. NASA\'s Europa Clipper arrives in 2030.'],
    ['Enceladus (Saturn)', 'Geysers spray its ocean into space. Cassini found hydrogen (food for microbes) and phosphates in them.'],
    ['Titan (Saturn)', 'Lakes of liquid methane and complex organic chemistry. NASA\'s Dragonfly drone is due to launch in 2028.'],
    ['Venus\'s clouds', 'Temperate but acidic cloud layers. A 2020 claim of phosphine gas there is still disputed.']];
  const BIOSIG = [['Oxygen + methane together', 'They destroy each other, so finding both means something keeps making them. On Earth, that\'s life.'],
    ['Ozone', 'Made from oxygen; easy to spot in ultraviolet and infrared spectra.'],
    ['The "red edge"', 'Plants reflect far more light just beyond red (about 700 nm). An alien forest could show it.'],
    ['Dimethyl sulfide', 'On Earth made almost only by sea life. A JWST hint on planet K2-18 b (2023–25) is unconfirmed; other teams found the signal weak or absent.'],
    ['False alarms', 'Oxygen can also build up without life, when starlight splits water and the hydrogen escapes to space. Context matters.']];
  const MOLECULES = [['H₂', 'Hydrogen gas: by far the most common molecule in the universe.'], ['CO', 'Carbon monoxide: used to map cold gas clouds where stars form.'],
    ['H₂O', 'Water: ice coats dust grains in every star-forming cloud.'], ['HCN', 'Hydrogen cyanide: toxic to us, but a key ingredient for making amino acids and DNA bases.'],
    ['Glycine', 'The simplest amino acid, measured in comet 67P\'s gas by Rosetta (2016).'], ['C₆₀', 'Buckyballs (football-shaped carbon) found around a dying star by Spitzer (2010).'],
    ['Benzonitrile', 'The first specific ring-shaped (aromatic) molecule found in space, in the Taurus molecular cloud (2018).'],
    ['CH₃⁺', 'The methyl cation, a key step in carbon chemistry, spotted by JWST in a planet-forming disk (2023).']];
  const METEOR = [['Murchison meteorite', 'Fell in Australia in 1969. Contains more than 70 different amino acids (most not used by life) plus the DNA/RNA bases uracil and xanthine.'],
    ['Ribose in meteorites', 'The sugar in RNA was found in two meteorites (Murchison and NWA 801) in 2019.'],
    ['Asteroid Ryugu', 'Japan\'s Hayabusa2 returned samples in 2020; they contain uracil, one of RNA\'s letters.'],
    ['Asteroid Bennu', 'NASA\'s OSIRIS-REx returned 121 g in 2023: 14 of the 20 amino acids life uses, all five DNA/RNA bases, ammonia, and salts left by evaporating brine (published 2025).']];
  const QUAKES = [['Moonquakes', 'Apollo seismometers (1969–77) recorded thousands. The Moon "rings" for an hour or more because its rock is dry. Shallow moonquakes reached about magnitude 5.'],
    ['Marsquakes', 'NASA\'s InSight lander heard 1,319 marsquakes (2018–22); the biggest, magnitude 4.7 on 4 May 2022. Their echoes revealed Mars\'s liquid core.'],
    ['Starquakes', 'Stars vibrate like bells. TESS and Kepler measure these tiny flickers to weigh stars and find their ages (asteroseismology).']];
  const FRAUN = [{ el: 'H', name: 'Hydrogen', lines: [656.3, 486.1, 434.0, 410.2], note: 'The most common element; its red line (656 nm) makes nebulae glow pink.' },
    { el: 'Na', name: 'Sodium', lines: [589.0, 589.6], note: 'The yellow pair: the same yellow as old street lamps.' },
    { el: 'Mg', name: 'Magnesium', lines: [516.7, 517.3, 518.4], note: 'A green triplet.' },
    { el: 'Ca', name: 'Calcium', lines: [393.4, 396.8], note: 'Two deep violet lines in the Sun ("H and K").' },
    { el: 'Fe', name: 'Iron', lines: [438.4, 527.0, 495.8, 466.8], note: 'Thousands of iron lines cross the Sun\'s spectrum; these are a few strong ones.' },
    { el: 'O₂', name: 'Oxygen (in our air)', lines: [686.7, 759.4], note: 'Made by Earth\'s atmosphere, not the Sun: exactly the kind of line that could reveal oxygen on an exoplanet.' }];
  const wl2rgb = w => {
    let r = 0, g = 0, b = 0;
    if (w < 440) { r = (440 - w) / 60; b = 1; } else if (w < 490) { g = (w - 440) / 50; b = 1; } else if (w < 510) { g = 1; b = (510 - w) / 20; }
    else if (w < 580) { r = (w - 510) / 70; g = 1; } else if (w < 645) { r = 1; g = (645 - w) / 65; } else r = 1;
    const f = w < 420 ? 0.3 + 0.7 * (w - 380) / 40 : w > 700 ? 0.3 + 0.7 * (780 - w) / 80 : 1;
    return `rgb(${[r, g, b].map(v => Math.round(255 * Math.max(0, v) * f)).join(',')})`;
  };
  function spectrumHTML() {
    const stops = []; for (let w = 380; w <= 780; w += 20) stops.push(`${wl2rgb(w)} ${((w - 380) / 4).toFixed(1)}%`);
    return `<div class="bx-spec" style="background:linear-gradient(90deg, ${stops.join(',')})">${FRAUN.map(f => f.lines.map(l => `<i data-el="${esc(f.el)}" style="left:${((l - 380) / 4).toFixed(2)}%"></i>`).join('')).join('')}</div>
      <div class="bx-specax small muted"><span>400 nm</span><span>500</span><span>600</span><span>700</span></div>
      <div class="row bx-els">${FRAUN.map(f => `<button type="button" class="b" data-el="${esc(f.el)}">${esc(f.el)} · ${esc(f.name)}</button>`).join('')}</div><p class="bx-elnote small"></p>`;
  }

  // ------------------------------------------------------------------ Engineering calculators
  const ENGINES = [['Merlin Vacuum (Falcon 9 upper stage)', 348], ['RL10 (Centaur, hydrogen)', 465], ['Raptor Vacuum (Starship)', 380], ['Solid rocket motor', 270], ['Ion thruster (Dawn)', 3100]];
  const DV = [['Ground to low Earth orbit (with losses)', 9.4], ['Low Earth orbit → toward the Moon', 3.1], ['Low Earth orbit → escape Earth', 3.2], ['Low Earth orbit → toward Mars', 3.6]];
  const BODIES = { Earth: [398600.4418, 6371], Moon: [4902.8, 1737.4], Mars: [42828.4, 3389.5] };
  function rocketCard() {
    return card('🚀 Rocket equation', `<p class="small">How much a rocket can change its speed (Δv) depends on how fast its exhaust leaves (Isp) and how much of it is fuel. Tsiolkovsky worked this out in 1903: Δv = Isp × g₀ × ln(full mass ÷ empty mass).</p>
      <div class="bx-form"><label>Engine <select class="bx-eng">${ENGINES.map(([n, i]) => `<option value="${i}">${esc(n)}: ${i} s</option>`).join('')}</select></label>
      <label>Empty mass (t) <input type="number" class="bx-dry" value="4" min="0.1" step="0.1"></label><label>Fuel (t) <input type="number" class="bx-prop" value="111" min="0" step="1"></label>
      <label>Payload (t) <input type="number" class="bx-pay" value="10" min="0" step="0.5"></label></div><div class="bx-rout"></div>
      <p class="small muted">The default is roughly a Falcon 9 upper stage. Numbers are approximate; real missions also lose speed to gravity and air.</p>`, 'bx-rocket');
  }
  function orbitCard() {
    return card('🛰 Orbit calculator', `<p class="small">The higher the orbit, the slower you go and the longer one lap takes. At about 35,786 km above Earth, one lap takes exactly a day: satellites there seem to hang still in the sky.</p>
      <div class="bx-form"><label>World <select class="bx-body">${Object.keys(BODIES).map(b => `<option>${b}</option>`).join('')}</select></label>
      <label>Height above the surface (km) <input type="number" class="bx-alt" value="420" min="0" step="10"></label>
      <span class="row">${[['ISS', 420], ['Hubble', 515], ['GPS', 20200], ['Geostationary', 35786]].map(([n, a]) => `<button type="button" class="b bx-preset" data-alt="${a}">${n}</button>`).join('')}</span></div><div class="bx-oout"></div>`, 'bx-orbit');
  }
  function wireEngineering(el) {
    const r = el.querySelector('.bx-rocket');
    if (r) {
      const calc = () => {
        const isp = +r.querySelector('.bx-eng').value, dry = +r.querySelector('.bx-dry').value, prop = +r.querySelector('.bx-prop').value, pay = +r.querySelector('.bx-pay').value;
        const m0 = dry + prop + pay, m1 = dry + pay, dv = m1 > 0 ? isp * 9.80665 * Math.log(m0 / m1) / 1000 : 0;
        r.querySelector('.bx-rout').innerHTML = `<p>Δv = <b class="mono">${num(dv, 2)} km/s</b> (fuel is ${Math.round(prop / m0 * 100)}% of the total mass)</p>
          <ul class="small">${DV.map(([n, v]) => `<li>${dv >= v ? '✅' : '❌'} ${esc(n)}: needs about ${v} km/s</li>`).join('')}</ul>`;
      };
      r.querySelectorAll('select, input').forEach(i => i.addEventListener('input', calc)); calc();
    }
    const o = el.querySelector('.bx-orbit');
    if (o) {
      const calc = () => {
        const [mu, R] = BODIES[o.querySelector('.bx-body').value], h = Math.max(0, +o.querySelector('.bx-alt').value), a = R + h;
        const v = Math.sqrt(mu / a), T = 2 * Math.PI * Math.sqrt(a * a * a / mu), esc2 = Math.sqrt(2 * mu / a);
        const fmt = s => s < 7200 ? num(s / 60, 1) + ' minutes' : s < 172800 ? num(s / 3600, 2) + ' hours' : num(s / 86400, 2) + ' days';
        o.querySelector('.bx-oout').innerHTML = `<div class="kv"><b>Speed</b><span class="mono">${num(v, 2)} km/s (${Math.round(v * 3600).toLocaleString()} km/h)</span><b>One lap</b><span class="mono">${fmt(T)}</span>
          <b>Sunrises a day</b><span class="mono">${num(86400 / T, 1)}</span><b>Speed to escape from here</b><span class="mono">${num(esc2, 2)} km/s</span></div>`;
      };
      o.querySelectorAll('select, input').forEach(i => i.addEventListener('input', calc));
      o.querySelectorAll('.bx-preset').forEach(b => b.addEventListener('click', () => { o.querySelector('.bx-body').value = 'Earth'; o.querySelector('.bx-alt').value = b.dataset.alt; calc(); }));
      calc();
    }
    const L = el.querySelector('.bx-launch');
    if (L) {
      const tick = () => {
        if (!L.isConnected) return clearInterval(timer);
        L.querySelectorAll('[data-net]').forEach(s => {
          const ms = Date.parse(s.dataset.net) - Date.now();
          if (isNaN(ms)) return;
          const d = Math.floor(Math.abs(ms) / 864e5), h = Math.floor(Math.abs(ms) / 36e5) % 24, m = Math.floor(Math.abs(ms) / 6e4) % 60;
          s.textContent = ms > 0 ? `T−${d ? d + 'd ' : ''}${h}h ${m}m` : 'launch time passed (awaiting update)';
        });
      };
      const timer = setInterval(tick, 30000); tick();
    }
  }
  function launchCard() {
    const l = B().launches;
    if (!l) return card('🚀 Launches', none('launch'));
    const row = (x, up) => `<tr><td><b>${esc(x.name)}</b><div class="small muted">${esc(x.provider || '')}${x.orbit ? ' · to ' + esc(x.orbit) : ''}${x.pad ? ' · ' + esc(x.pad) : ''}</div>${x.mission ? `<details class="small"><summary>mission</summary>${esc(x.mission)}</details>` : ''}</td>
      <td>${dt(x.net)}${up ? `<div class="small mono" data-net="${esc(x.net)}"></div>` : ''}</td><td><span class="chip ${x.status === 'Success' ? 'ok' : x.status === 'Failure' ? 'bad' : ''}">${esc(x.status || '')}</span></td></tr>`;
    return card('🚀 Rocket launches', `<h4>Coming up</h4><table class="compact bx-launch">${(l.upcoming || []).map(x => row(x, true)).join('')}</table>
      <h4>Just launched</h4><table class="compact">${(l.previous || []).map(x => row(x, false)).join('')}</table>
      <p class="small muted">Go = confirmed date; TBD/TBC = date not firm. Data: The Space Devs Launch Library.</p>`);
  }

  // ------------------------------------------------------------------ Beyond astronomy tab
  function habitableCard() {
    const h = B().habitable;
    if (!h) return card('🌍 Possibly habitable worlds', none('exoplanet'));
    const rows = h.filter(p => p.similarity).sort((a, b) => b.similarity - a.similarity).slice(0, 15);
    return card('🌍 The most Earth-like planets found so far', `<p class="small">Planets less than 2.5 times Earth's width that get roughly Earth-like sunlight from their star. "Habitable" only means liquid water might be possible on the surface, not that anything lives there.</p>
      <table class="compact"><tr><th>planet</th><th>size</th><th>sunlight</th><th>temperature*</th><th>distance</th><th>Earth-likeness</th><th>in the sky</th></tr>${rows.map(p => `<tr><td><b>${esc(p.pl_name)}</b><div class="small muted">${esc(p.st_spectype || '')} star, found ${esc(p.disc_year || '')} (${esc(p.disc_method || '')})</div></td>
        <td class="mono">${num(p.pl_rade, 2)}× Earth</td><td class="mono">${num(p.pl_insol, 2)}× Earth's</td><td class="mono">${p.pl_eqt ? Math.round(p.pl_eqt - 273) + ' °C' : '—'}</td>
        <td class="mono">${p.sy_dist ? Math.round(p.sy_dist * 3.26) + ' ly' : '—'}</td><td><span class="bx-meter"><i style="width:${Math.round(p.similarity * 100)}%"></i></span> ${num(p.similarity, 2)}</td>
        <td class="small">${p.ra !== undefined && p.ra !== null ? `${p.constellation ? 'in <b>' + esc(p.constellation) + '</b>' : ''}${p.sy_vmag ? `, star magnitude ${num(p.sy_vmag, 1)} (${p.sy_vmag < 6 ? 'naked eye' : p.sy_vmag < 9 ? 'binoculars' : p.sy_vmag < 13 ? 'small telescope' : 'big telescope'})` : ''}
          <br><button type="button" class="b bx-findhab" data-ra="${p.ra}" data-dec="${p.dec}" data-name="${esc(p.hostname)}">Where is it now?</button> ${link(`https://sky.esa.int/esasky/?target=${p.ra}%20${p.dec}&hips=DSS2+color&fov=0.5`, 'sky map')}<span class="bx-findout"></span>` : ''}</td></tr>`).join('')}</table>
      <p class="small muted">*Temperature without any atmosphere (Earth's would be about −18 °C; greenhouse gases warm it to +15 °C). "Earth-likeness" is a rough score from size and sunlight only (1 = Earth), not the full Earth Similarity Index. Data: NASA Exoplanet Archive.</p>`);
  }
  function earthCard() {
    const e = B().earth || [], q = B().quakes;
    const imgs = e.map(x => `<figure class="bx-earth"><img class="plot" src="${esc(x.img)}" alt="${esc(x.caption)}" loading="lazy"><figcaption class="small">${esc(x.caption)}${x.date ? ' · ' + esc(String(x.date).slice(0, 16)) : ''}<br><span class="muted">${esc(x.source)}</span></figcaption></figure>`).join('');
    const quakes = q ? `<h4>Earthquakes of magnitude 4.5+ this week (${q.length})</h4>${worldMap(q.map(x => ({ lat: x.lat, lon: x.lon, r: Math.max(5, (x.mag - 4) * 7), cls: 'quake', title: `M${x.mag} ${x.place || ''}` })), 'small')}
      <table class="compact"><tr><th>magnitude</th><th>where</th><th>when</th><th>depth</th></tr>${q.slice(0, 8).map(x => `<tr><td class="mono"><b>${num(x.mag, 1)}</b></td><td>${x.url ? link(x.url, esc(x.place || '?')) : esc(x.place || '?')}${x.tsunami ? ' <span class="chip warn">tsunami info</span>' : ''}</td><td>${dt(x.time)}</td><td class="mono">${x.depth_km ?? '—'} km</td></tr>`).join('')}</table>
      <p class="small muted">Satellites like GRACE and InSAR radar measure how the ground moves after big quakes. Data: USGS.</p>` : '';
    return card('🌎 Earth from space', (imgs ? `<div class="bx-earths">${imgs}</div>` : '<p class="muted small">Satellite pictures appear after the next run.</p>') + quakes);
  }
  const list = rows => `<dl class="bx-dl">${rows.map(([h, t]) => `<dt>${esc(h)}</dt><dd>${esc(t)}</dd>`).join('')}</dl>`;
  function beyondTab(el) {
    el.innerHTML = `<div class="card"><h3>🧬 Beyond astronomy</h3><p>Space science is also biology, chemistry, engineering and Earth science. Live data where it exists, short explanations everywhere else.</p>
      <nav class="row bx-jump">${[['bio', '🦠 Biology'], ['chem', '⚗️ Chemistry'], ['eng', '🛠 Engineering'], ['earth', '🌎 Earth & planets']].map(([id, t]) => `<button type="button" class="b" data-jump="bx-${id}">${t}</button>`).join('')}</nav></div>
      <h2 id="bx-bio">🦠 Biology: is anything else alive out there?</h2>${habitableCard()}
      <div class="grid g2">${card('🐻 Extremophiles: life at the limits', list(EXTREMO) + '<p class="small muted">If life survives these places on Earth, similar places on other worlds are worth checking.</p>')}${card('📍 Where to look in our solar system', list(WHERE))}</div>
      ${card('🔬 How would we recognise life on an exoplanet?', list(BIOSIG) + '<p class="small muted">JWST splits starlight that passes through a planet\'s air into a rainbow and looks for the missing colours each gas absorbs. Try that idea in the Chemistry section below.</p>')}
      <h2 id="bx-chem">⚗️ Chemistry: what is the universe made of?</h2>
      ${card('🌈 Read a star like an astronomer', `<p class="small">Sunlight spread into a rainbow has dark lines where atoms in the Sun's outer layers soak up particular colours (Fraunhofer found 574 of them in 1814). Each element leaves its own barcode. Tap an element to find its lines.</p>${spectrumHTML()}`, 'bx-specard')}
      <div class="grid g2">${card('🧪 Molecules found in space', list(MOLECULES) + '<p class="small muted">More than 300 different molecules have been identified between the stars, most by radio telescopes.</p>')}${card('🪨 Life\'s building blocks from space rocks', list(METEOR) + '<p class="small muted">Meteorites and asteroid samples show the ingredients for life formed in space before Earth existed.</p>')}</div>
      <h2 id="bx-eng">🛠 Engineering: how do we get there?</h2>${launchCard()}<div class="grid g2">${rocketCard()}${orbitCard()}</div>
      <h2 id="bx-earth">🌎 Earth and other worlds</h2>${earthCard()}${card('🫨 Quakes on other worlds', list(QUAKES))}
      <p class="small muted">Live data updated ${B().t ? ago(B().t) : '—'}. Sources: NASA (Exoplanet Archive, GIBS, EPIC), USGS, The Space Devs. Science notes written for this page; corrections welcome on GitHub.</p>`;
    el.querySelectorAll('.bx-findhab').forEach(b => b.addEventListener('click', () => {
      const out = b.parentElement.querySelector('.bx-findout');
      let pl = null; try { pl = JSON.parse(localStorage.getItem('sl_place')); } catch (_) { /* none */ }
      if (!pl) { out.innerHTML = ' Set your location (📍 at the top) first.'; return; }
      const SG = window.SpaceLabStargazing; if (!SG) return;
      const [alt, az] = SG.altAz(+b.dataset.ra, +b.dataset.dec, pl.lat, pl.lon, new Date());
      out.innerHTML = alt > 0 ? ` Right now ${esc(b.dataset.name)} is <b>${Math.round(alt)}°</b> up in the <b>${compass(az)}</b>.` : ` ${esc(b.dataset.name)} is below your horizon right now.`;
    }));
    el.querySelectorAll('[data-jump]').forEach(b => b.addEventListener('click', () => { const t = document.getElementById(b.dataset.jump); if (t && t.scrollIntoView) t.scrollIntoView({ behavior: 'smooth' }); }));
    el.querySelectorAll('.bx-els button').forEach(b => b.addEventListener('click', () => {
      const on = !b.classList.contains('primary');
      el.querySelectorAll('.bx-els button').forEach(x => x.classList.remove('primary'));
      el.querySelectorAll('.bx-spec i').forEach(i => i.classList.toggle('hl', on && i.dataset.el === b.dataset.el));
      if (on) b.classList.add('primary');
      const f = FRAUN.find(x => x.el === b.dataset.el);
      el.querySelector('.bx-elnote').textContent = on && f ? `${f.name}: ${f.lines.map(l => l + ' nm').join(', ')}. ${f.note}` : '';
    }));
    wireEngineering(el);
  }

  S.addTab('sky', 'Sky watch', skyTab);
  S.addTab('beyond', 'Beyond astronomy', beyondTab);
  window.SpaceLabBeyond = { issPasses, sunECEF, gmst, wl2rgb };
})();
