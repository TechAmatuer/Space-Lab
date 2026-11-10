// Space Lab, around Earth: the space stations, the big telescopes, the Earth and weather watchers and the satellite swarms,
// where they are right now. spacelab/earthorbit.py writes one orbit per spacecraft (from CelesTrak's current orbit data);
// this page moves each along it, second by second. Earth is to scale, spacecraft are dots. Plain canvas, no libraries.
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const D = () => S.data || {};
  const EO = () => ((D().beyond || {}).earth_orbits) || null;
  const R = 6371.0, MU = 398600.4418;
  const COLOR = { stations: '#ffd166', telescopes: '#ef8bff', earth: '#7ee08a', weather: '#6fc3ff', navigation: '#ffb36b', geo: '#b9c4d6', starlink: '#9aa7ff' };
  const G = { ready: false };
  const raf = f => (window.requestAnimationFrame ? window.requestAnimationFrame(f) : setTimeout(f, 50));
  const unraf = id => (window.cancelAnimationFrame ? window.cancelAnimationFrame(id) : clearTimeout(id));
  let V = null;

  async function load() {
    if (G.ready || G.loading) return G.loading;
    const eo = EO();
    G.loading = (async () => {
      const m = JSON.parse(await S.load(eo.json + (eo.made ? '?v=' + Math.round(eo.made) : '')));
      if (!m || !Array.isArray(m.objects) || !m.objects.length) throw new Error('the orbit file is empty');
      Object.assign(G, { m, byKey: {} });
      m.objects.forEach(o => { G.byKey[o.key] = o; });
      G.ready = true;
    })();
    return G.loading;
  }
  // where a spacecraft is at time t (unix seconds): along its stored orbit, one full turn from t0
  function pos(o, t) {
    const n = o.pts.length, x = ((((t - G.m.t0) / o.period_s) % 1) + 1) % 1 * n, k = Math.floor(x), f = x - k;
    const a = o.pts[k % n], b = o.pts[(k + 1) % n];
    return [a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f, a[2] + (b[2] - a[2]) * f];
  }
  // a swarm satellite: turned about its orbit's axis by the time passed (near-circular orbits)
  function spos(p, t) {
    const th = 2 * Math.PI * (t - G.m.t0) / (p[6] * 60), c = Math.cos(th), s = Math.sin(th);
    const cx = p[4] * p[2] - p[5] * p[1], cy = p[5] * p[0] - p[3] * p[2], cz = p[3] * p[1] - p[4] * p[0];
    return [p[0] * c + cx * s, p[1] * c + cy * s, p[2] * c + cz * s];
  }
  const gmst = t => { const jd = t / 86400 + 2440587.5, T = (jd - 2451545) / 36525; return ((280.46061837 + 360.98564736629 * (jd - 2451545) + 0.000387933 * T * T) % 360) * Math.PI / 180; };
  function facts(o, t) {
    const p = pos(o, t), q = pos(o, t + 10), r = Math.hypot(...p), v = Math.hypot(q[0] - p[0], q[1] - p[1], q[2] - p[2]) / 10;
    let lon = (Math.atan2(p[1], p[0]) - gmst(t)) * 180 / Math.PI;
    lon = ((lon + 540) % 360) - 180;
    return { alt: r - R, v, lat: Math.asin(p[2] / r) * 180 / Math.PI, lon };
  }
  const groupName = g => ((G.m.groups || []).find(x => x[0] === g) || [g, g])[1];
  const dur = s => s < 2 * 3600 ? `${num(s / 60, 0)} minutes` : s < 3 * 86400 ? `${num(s / 3600, 1)} hours` : `${num(s / 86400, 1)} days`;
  const place = (lat, lon) => `${num(Math.abs(lat), 1)}° ${lat < 0 ? 'S' : 'N'}, ${num(Math.abs(lon), 1)}° ${lon < 0 ? 'W' : 'E'}`;
  function rows(o) {
    const f = facts(o, V.t);
    return `<tr><th>Height above the ground</th><td>${num(f.alt, 0)} km${o.apogee_km - o.perigee_km > 2000 ? ` (between ${num(o.perigee_km, 0)} and ${num(o.apogee_km, 0)} km on its stretched orbit)` : ''}</td></tr>
      <tr><th>Speed</th><td>${num(f.v, 2)} km/s (${num(f.v * 3600, 0)} km/h)</td></tr>
      <tr><th>Once around Earth</th><td>${dur(o.period_s)}</td></tr>
      <tr><th>Right above</th><td>${place(f.lat, f.lon)}</td></tr>`;
  }
  function card(o) {
    return `<div class="ss-card"><div class="row"><h3><span class="ss-dot" style="background:${COLOR[o.group] || '#fff'}"></span>${esc(o.name)}</h3><span class="spacer"></span><button class="b ev-x" aria-label="Close">✕</button></div>
      <div class="small muted">${esc(groupName(o.group))} · NORAD ${esc(o.norad)}</div><p class="small">${esc(o.what || '')}</p>
      <table class="small ss-facts ev-facts">${rows(o)}</table><div class="ev-media"></div></div>`;
  }

  // ------------------------------------------------------------------ drawing (a turntable view, as in the solar system map)
  function proj(p) {
    const ca = Math.cos(V.yaw), sa = Math.sin(V.yaw), X = p[0] * ca - p[1] * sa, Y = p[0] * sa + p[1] * ca, ct = Math.cos(V.tilt), st = Math.sin(V.tilt);
    return [V.w / 2 + X * V.scale, V.h / 2 - (Y * ct + p[2] * st) * V.scale, -Y * st + p[2] * ct];
  }
  const hidden = s => s[2] < 0 && Math.hypot(s[0] - V.w / 2, s[1] - V.h / 2) < R * V.scale;     // behind the Earth
  function draw() {
    if (!V || !G.ready) return;
    const cv = V.canvas, dpr = Math.min(3, window.devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    V.w = w; V.h = h;
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const t = V.t, er = R * V.scale, cx = w / 2, cy = h / 2;
    // the Earth, with a latitude/longitude grid that turns with it
    const gr = ctx.createRadialGradient(cx - er * 0.3, cy - er * 0.35, er * 0.1, cx, cy, er);
    gr.addColorStop(0, '#3d7fd0'); gr.addColorStop(0.7, '#1d4f91'); gr.addColorStop(1, '#0e2a52');
    ctx.fillStyle = gr; ctx.beginPath(); ctx.arc(cx, cy, er, 0, 7); ctx.fill();
    ctx.strokeStyle = 'rgba(190,220,255,.22)'; ctx.lineWidth = 1;
    const g0 = gmst(t), sph = (la, lo) => { const a = la * Math.PI / 180, b = lo * Math.PI / 180 + g0; return proj([R * Math.cos(a) * Math.cos(b), R * Math.cos(a) * Math.sin(b), R * Math.sin(a)]); };
    const curve = pts => { ctx.beginPath(); let pen = false; for (const s of pts) { if (s[2] < 0) { pen = false; continue; } if (pen) ctx.lineTo(s[0], s[1]); else { ctx.moveTo(s[0], s[1]); pen = true; } } ctx.stroke(); };
    for (let la = -60; la <= 60; la += 30) { const pts = []; for (let lo = 0; lo <= 360; lo += 6) pts.push(sph(la, lo)); curve(pts); }
    for (let lo = 0; lo < 360; lo += 30) { const pts = []; for (let la = -90; la <= 90; la += 6) pts.push(sph(la, lo)); curve(pts); }
    ctx.strokeStyle = 'rgba(255,210,120,.45)'; { const pts = []; for (let lo = 0; lo <= 360; lo += 4) pts.push(sph(0, lo)); curve(pts); }        // equator
    // swarms
    for (const [kind, sw] of Object.entries(G.m.swarms || {})) {
      if (V.off[kind]) continue;
      ctx.fillStyle = COLOR[kind] || '#ccc';
      for (const p of sw.pts) {
        const s = proj(spos(p, t));
        if (hidden(s)) continue;
        ctx.globalAlpha = kind === 'starlink' ? 0.55 : 0.8;
        ctx.fillRect(s[0] - 1.2, s[1] - 1.2, 2.4, 2.4);
      }
    }
    ctx.globalAlpha = 1;
    // spacecraft: their orbit, then the dot and the name
    V.hit = [];
    const labels = [];
    for (const o of G.m.objects) {
      if (V.off[o.group]) continue;
      const on = V.sel === o.key;
      ctx.strokeStyle = COLOR[o.group] || '#fff'; ctx.globalAlpha = on ? 0.9 : 0.28; ctx.lineWidth = on ? 1.6 : 1;
      ctx.beginPath(); let pen = false;
      for (const p of o.pts) { const s = proj(p); if (hidden(s)) { pen = false; continue; } if (pen) ctx.lineTo(s[0], s[1]); else { ctx.moveTo(s[0], s[1]); pen = true; } }
      ctx.stroke(); ctx.globalAlpha = 1;
      const s = proj(pos(o, t));
      if (hidden(s) || s[0] < -20 || s[0] > w + 20 || s[1] < -20 || s[1] > h + 20) continue;
      ctx.fillStyle = COLOR[o.group] || '#fff'; ctx.beginPath(); ctx.arc(s[0], s[1], on ? 4.5 : 3.2, 0, 7); ctx.fill();
      if (on) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(s[0], s[1], 10, 0, 7); ctx.stroke(); }
      V.hit.push({ key: o.key, x: s[0], y: s[1] });
      labels.push([o, s]);
    }
    ctx.font = '12px system-ui, -apple-system, sans-serif'; ctx.textBaseline = 'middle';
    const placed = [];
    labels.sort((a, b) => (b[0].key === V.sel) - (a[0].key === V.sel));
    for (const [o, s] of labels) {
      const tw = ctx.measureText(o.name).width + 8, bx = [s[0] + 8, s[1] - 8, tw, 16];
      if (o.key !== V.sel && placed.some(q => bx[0] < q[0] + q[2] && q[0] < bx[0] + bx[2] && bx[1] < q[1] + q[3] && q[1] < bx[1] + bx[3])) continue;
      placed.push(bx);
      ctx.fillStyle = 'rgba(5,8,15,.6)'; ctx.fillRect(bx[0], bx[1], tw, 16);
      ctx.fillStyle = o.key === V.sel ? '#fff' : COLOR[o.group] || '#fff'; ctx.fillText(o.name, bx[0] + 4, s[1]);
    }
    // scale bar and time
    const km = [1000, 5000, 10000, 20000, 50000, 100000].find(k => k * V.scale > 70) || 100000;
    ctx.fillStyle = 'rgba(220,230,245,.85)'; ctx.fillRect(12, h - 18, km * V.scale, 2);
    ctx.fillText(`${km.toLocaleString()} km`, 12, h - 28);
    const when = V.el.querySelector('.ev-when');
    if (when) when.textContent = (V.live ? 'Live · ' : '') + new Date(t * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' }) + (V.live ? '' : V.rate > 1 ? ` · ${V.rate}× faster` : '');
    const tb = V.el.querySelector('.ev-facts'), so = V.sel && G.byKey[V.sel];
    if (so && tb) tb.innerHTML = rows(so);
  }

  // ------------------------------------------------------------------ controls
  const setRadius = r => { V.scale = Math.min(V.w || 600, V.h || 420) / 2 / r; };
  function select(key) {
    const o = G.byKey[key];
    if (!o) return;
    V.sel = key;
    const box = V.el.querySelector('.ev-cardbox');
    box.innerHTML = card(o);
    if (window.SpaceLabMedia) window.SpaceLabMedia.media(box.querySelector('.ev-media'), { q: o.name, wiki: o.wiki || o.name });
    const r = Math.hypot(...pos(o, V.t)), seen = Math.min(V.w, V.h) / 2 / V.scale;      // zoom out when it is off the picture
    if (r > seen * 0.9) setRadius(Math.max(r * 1.25, 9000));
    V.el.querySelectorAll('.ev-pick').forEach(b => b.classList.toggle('on', b.dataset.k === key));
    S.track('earth/craft', true);
    draw();
  }
  function wire(el) {
    const cv = V.canvas;
    let drag = null, moved = 0;
    cv.addEventListener('pointerdown', e => { drag = [e.clientX, e.clientY, V.yaw, V.tilt]; moved = 0; cv.setPointerCapture && cv.setPointerCapture(e.pointerId); });
    cv.addEventListener('pointermove', e => {
      if (!drag) return;
      const dx = e.clientX - drag[0], dy = e.clientY - drag[1];
      moved = Math.max(moved, Math.abs(dx) + Math.abs(dy));
      V.yaw = drag[2] + dx * 0.008; V.tilt = Math.max(-1.55, Math.min(1.55, drag[3] + dy * 0.008));
      draw();
    });
    cv.addEventListener('pointerup', e => {
      const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top;
      drag = null;
      if (moved > 5) return;
      const hit = (V.hit || []).map(h => [Math.hypot(h.x - x, h.y - y), h]).sort((a, b) => a[0] - b[0])[0];
      if (hit && hit[0] < 16) select(hit[1].key);
    });
    cv.addEventListener('wheel', e => { e.preventDefault(); V.scale *= Math.exp(-e.deltaY * 0.0015); draw(); }, { passive: false });
    el.addEventListener('click', e => {
      const t = e.target.closest('button, input');
      if (!t) return;
      if (t.matches('.ev-zoom')) { setRadius(+t.dataset.r); draw(); }
      else if (t.matches('.ev-pick')) select(t.dataset.k);
      else if (t.matches('.ev-x')) { V.sel = null; el.querySelector('.ev-cardbox').innerHTML = ''; el.querySelectorAll('.ev-pick').forEach(b => b.classList.remove('on')); draw(); }
      else if (t.matches('.ev-now')) { V.live = true; V.rate = 1; V.t = Date.now() / 1000; syncPlay(); draw(); }
      else if (t.matches('.ev-play')) { V.live = false; V.rate = V.rate === 1 ? 60 : V.rate === 60 ? 600 : 1; if (V.rate === 1) V.live = true; syncPlay(); }
      else if (t.matches('.ev-grp')) { V.off[t.dataset.g] = !t.checked; draw(); }
    });
  }
  function syncPlay() { const b = V.el.querySelector('.ev-play'); if (b) b.textContent = V.rate === 1 ? '▶ 60× faster' : V.rate === 60 ? '▶▶ 600× faster' : '■ Real time'; }
  function tick() {
    if (!V || !document.body.contains(V.canvas)) { V = null; return; }
    const now = performance.now(), dt = (now - (V.last || now)) / 1000;
    V.last = now;
    V.t = V.live ? Date.now() / 1000 : V.t + dt * V.rate;
    if (V.canvas.offsetParent !== null || document.fullscreenElement) draw();          // nothing to draw while the tab is hidden
    V.raf = raf(tick);
  }

  function mount(el) {
    const eo = EO();
    if (!el) return;
    if (!eo || !eo.json) { el.innerHTML = ''; return; }
    if (V && V.el === el) return;
    el.innerHTML = `<div class="card ev-top"><div class="row"><h3>🛰 Around Earth, live</h3><span class="spacer"></span><span class="small muted">${Number(eo.n || 0)} spacecraft and ${Number(eo.n_swarm || 0).toLocaleString()} swarm satellites</span></div>
        <p class="small">Most of what people have launched circles the Earth. Here are the space stations, the big telescopes in Earth orbit, the satellites that watch the land and the weather, and the swarms behind your phone's map and internet, where they are right now. Drag to turn, scroll to zoom, tap a spacecraft for what it does.</p>
        <div class="row ss-bar"><button class="b ev-zoom" data-r="9000">Low orbit</button><button class="b ev-zoom" data-r="32000">GPS and Galileo</button><button class="b ev-zoom" data-r="48000">Geostationary ring</button><button class="b ev-zoom" data-r="160000">Chandra's long orbit</button><span class="spacer"></span><button class="b primary ev-full">⛶ Full screen</button></div></div>
      <div class="ss-wrap ev-wrap"><canvas class="ss-canvas ev-canvas" aria-label="Earth with the spacecraft around it. The list below has every spacecraft as a button."></canvas>
        <div class="ss-timebar"><button class="b ev-now">Now</button><button class="b ev-play">▶ 60× faster</button><span class="small mono ev-when"></span></div>
        <div class="ss-cardbox ev-cardbox"></div><div class="ss-loading small">Loading the orbits…</div></div>
      <div class="card small ev-list"></div>`;
    V = { el, canvas: el.querySelector('.ev-canvas'), yaw: 0.6, tilt: 0.45, scale: 0.02, t: Date.now() / 1000, live: true, rate: 1, sel: null, off: {}, w: 600, h: 420 };
    load().then(() => {
      const ld = el.querySelector('.ss-loading'); if (ld) ld.remove();
      V.w = V.canvas.clientWidth || 600; V.h = V.canvas.clientHeight || 420;
      setRadius(9000);
      const m = G.m, sw = m.swarms || {};
      el.querySelector('.ev-list').innerHTML = `<b>What is up there</b> <span class="muted">tap a name to find it; untick a group to hide it</span>
        ${(m.groups || []).map(([g, name]) => {
          const items = m.objects.filter(o => o.group === g), s = sw[g];
          if (!items.length && !s) return '';
          const note = s ? ` <span class="muted">${s.names && s.names.length ? esc(s.names.join(', ')) : ''}${g === 'starlink' ? `${Number(s.pts.length)} of ${Number(s.total).toLocaleString()} shown` : g === 'geo' ? `${Number(s.total)} satellites` : ''}</span>` : '';
          return `<div class="ss-group"><label><input type="checkbox" class="ev-grp" data-g="${esc(g)}" checked> <b><span class="ss-dot" style="background:${COLOR[g] || '#ccc'}"></span>${esc(name)}</b></label>${note} ${items.map(o => `<button class="b ev-pick" data-k="${esc(o.key)}">${esc(o.name)}</button>`).join('')}</div>`;
        }).join('')}
        <p class="muted">${esc(m.source || '')} Orbits as of ${new Date(m.made * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}, refreshed twice a day.${(m.left_out || []).length ? ` Not shown this time (no current orbit data): ${esc(m.left_out.join(', '))}.` : ''}</p>`;
      wire(el);
      if (window.SpaceLabFull) {
        const wrap = el.querySelector('.ev-wrap'), bar = el.querySelector('.ev-top .ss-bar'), home = bar.parentNode;
        window.SpaceLabFull(wrap, el.querySelector('.ev-full'), on => { if (on) { wrap.appendChild(bar); S.track('earth/fullscreen', true); } else home.appendChild(bar); draw(); });
      }
      draw();
      unraf(V.raf); V.raf = raf(tick);
    }).catch(e => { el.querySelector('.ss-loading').textContent = 'The orbits could not be loaded: ' + e.message; });
  }
  window.SpaceLabEarth = { mount, pos, spos, facts, gmst, data: G, view: () => V, select: k => select(k) };
})();
