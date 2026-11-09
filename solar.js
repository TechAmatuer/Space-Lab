// Space Lab solar system: where the planets and the spacecraft out there are right now. Positions come from NASA/JPL Horizons
// (spacelab/solar.py asks once a day and saves each object's position and velocity every few days); this page works out the
// spot for any second in between, so the numbers tick live. Drag to turn, scroll or pinch to zoom, tap anything for what it is,
// what it does and how far away it is. Plain canvas, no libraries; reads only this site's own two files.
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const D = () => S.data || {};
  const SO = () => ((D().beyond || {}).solar) || null;
  const AU_KM = 149597870.7, C_KMS = 299792.458, MU = 2.9591220828559e-4;      // Sun's pull, AU^3 / day^2
  const KMS = AU_KM / 86400;                                                    // AU/day -> km/s
  const LS = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } } };
  const G = { ready: false };
  let V = null;

  // ------------------------------------------------------------------ data
  function parseBin(buf) {
    const dv = new DataView(buf);
    if (dv.getUint8(0) !== 83 || dv.getUint8(1) !== 76 || dv.getUint8(2) !== 83) throw new Error('not a Space Lab solar system file');
    return { count: dv.getUint32(4, true), t0: dv.getFloat64(8, true), f: new Float32Array(buf, 16) };
  }
  const nowJd = () => Date.now() / 86400000 + 2440587.5;
  // position and velocity (AU, AU/day, around the Sun) of object o at Julian date jd, or null outside what is stored
  function state(o, jd) {
    const f = G.f, t = jd - G.t0, b = o.off * 7, n = o.n;
    if (!n || t < f[b] || t > f[b + (n - 1) * 7]) return null;
    let lo = 0, hi = n - 1;
    while (hi - lo > 1) { const m = (lo + hi) >> 1; if (f[b + m * 7] <= t) lo = m; else hi = m; }
    const i = b + lo * 7, j = b + hi * 7, h = f[j] - f[i] || 1, s = (t - f[i]) / h, s2 = s * s, s3 = s2 * s;
    const a = 2 * s3 - 3 * s2 + 1, bb = (s3 - 2 * s2 + s) * h, c = -2 * s3 + 3 * s2, d = (s3 - s2) * h;
    const p = [0, 1, 2].map(k => a * f[i + 1 + k] + bb * f[i + 4 + k] + c * f[j + 1 + k] + d * f[j + 4 + k]);
    const v = [0, 1, 2].map(k => f[i + 4 + k] + (f[j + 4 + k] - f[i + 4 + k]) * s);
    return { p, v };
  }
  // the closed path an object follows around the Sun, from where it is and how it moves (nothing for paths that never return)
  function orbit(o) {
    if (o._orbit !== undefined) return o._orbit;
    const st = state(o, G.t0);
    o._orbit = null;
    if (!st) return null;
    const r = st.p, v = st.v, rm = Math.hypot(...r), v2 = v[0] * v[0] + v[1] * v[1] + v[2] * v[2];
    const hv = [r[1] * v[2] - r[2] * v[1], r[2] * v[0] - r[0] * v[2], r[0] * v[1] - r[1] * v[0]], hm = Math.hypot(...hv);
    const ev = [(v[1] * hv[2] - v[2] * hv[1]) / MU - r[0] / rm, (v[2] * hv[0] - v[0] * hv[2]) / MU - r[1] / rm, (v[0] * hv[1] - v[1] * hv[0]) / MU - r[2] / rm];
    const e = Math.hypot(...ev), a = 1 / (2 / rm - v2 / MU);
    if (!(e < 0.985) || !(a > 0) || !hm) return null;
    const P = e > 1e-6 ? ev.map(x => x / e) : r.map(x => x / rm), hn = hv.map(x => x / hm);
    const Q = [hn[1] * P[2] - hn[2] * P[1], hn[2] * P[0] - hn[0] * P[2], hn[0] * P[1] - hn[1] * P[0]];
    const pts = [], N = 240, pp = a * (1 - e * e);
    for (let k = 0; k <= N; k++) { const th = k / N * 2 * Math.PI, rr = pp / (1 + e * Math.cos(th)); pts.push([0, 1, 2].map(i => rr * (Math.cos(th) * P[i] + Math.sin(th) * Q[i]))); }
    o._orbit = { pts, a, e, years: Math.sqrt(a * a * a) };
    return o._orbit;
  }

  // ------------------------------------------------------------------ words and numbers
  const km = k => k < 1e6 ? `${Math.round(k).toLocaleString()} km` : k < 1e9 ? `${num(k / 1e6, k < 1e7 ? 2 : 1)} million km` : `${num(k / 1e9, 2)} billion km`;
  const au = a => `${num(a, a < 0.1 ? 4 : a < 10 ? 3 : 2)} AU`;
  function lightTime(k) {
    const s = k / C_KMS;
    if (s < 90) return `${num(s, 1)} seconds`;
    if (s < 5400) return `${Math.floor(s / 60)} min ${Math.round(s % 60)} s`;
    return `${Math.floor(s / 3600)} h ${Math.round(s % 3600 / 60)} min`;
  }
  const dateOf = jd => new Date((jd - 2440587.5) * 86400000);
  const fmtDate = jd => dateOf(jd).toLocaleDateString([], { year: 'numeric', month: 'short', day: 'numeric' });
  const groupName = g => ((G.meta.groups || []).find(x => x[0] === g) || [0, g])[1];
  const obj = key => G.byKey[key];

  function facts(o, jd) {
    const st = state(o, jd), e = obj('earth'), es = e && state(e, jd);
    if (!st) return null;
    const r = Math.hypot(...st.p), sp = Math.hypot(...st.v) * KMS;
    const out = { r, speed: sp, away: (st.p[0] * st.v[0] + st.p[1] * st.v[1] + st.p[2] * st.v[2]) / r * KMS };
    if (es && o.key !== 'earth') out.earth = Math.hypot(st.p[0] - es.p[0], st.p[1] - es.p[1], st.p[2] - es.p[2]);
    return out;
  }
  function liveRows(o) {                                  // the numbers that change: rebuilt every second while the card is open
    const jd = V.jd, f = facts(o, jd), orb = o.kind !== 'craft' ? orbit(o) : null;
    const rows = !f ? `<tr><td colspan="2" class="muted">No position is stored for this date (the stored path covers ${fmtDate(G.t0 - G.meta.back)} to ${fmtDate(G.t0 + G.meta.ahead)}).</td></tr>` : `
      <tr><th>From the Sun</th><td>${au(f.r)} · ${km(f.r * AU_KM)}</td></tr>
      ${f.earth !== undefined ? `<tr><th>From Earth</th><td>${au(f.earth)} · ${km(f.earth * AU_KM)}</td></tr>
      <tr><th>${o.kind === 'craft' ? 'A radio signal takes' : 'Its light takes'}</th><td>${lightTime(f.earth * AU_KM)} to reach Earth</td></tr>` : `<tr><th>Sunlight takes</th><td>${lightTime(f.r * AU_KM)} to get here</td></tr>`}
      <tr><th>Speed around the Sun</th><td>${num(f.speed, f.speed < 10 ? 2 : 1)} km/s (${Math.round(f.speed * 3600).toLocaleString()} km/h)${o.group === 'leaving' ? `, moving away at ${num(f.away, 1)} km/s: ${km(f.away * 86400)} farther every day` : ''}</td></tr>
      ${orb ? `<tr><th>One trip around the Sun</th><td>${orb.years < 2 ? num(orb.years * 365.25, 0) + ' days' : num(orb.years, orb.years < 20 ? 1 : 0) + ' years'}</td></tr>` : ''}`;
    return `<caption>${V.live ? 'Right now (updates every second)' : 'On ' + esc(fmtDate(jd))}</caption>${rows}`;
  }
  // spacecraft at a world, under their headings, each name linking to its Wikipedia article (older files: [name, who, note])
  const wikiUrl = t => 'https://en.wikipedia.org/wiki/' + encodeURIComponent(String(t).replace(/ /g, '_'));
  const wlink = (t, label, bold) => `<a href="${esc(wikiUrl(t))}" target="_blank" rel="noopener" title="Wikipedia: ${esc(t)}">${bold ? `<b>${esc(label)}</b>` : esc(label)}</a>`;
  function atList(at) {
    const items = at.map(x => (Array.isArray(x) ? { n: x[0], by: x[1], note: x[2] } : x || {}));
    const secs = [...new Set(items.map(x => x.sec || ''))];
    return secs.map(sec => `${sec ? `<div class="ss-sec">${esc(sec)}</div>` : ''}<ul>${items.filter(x => (x.sec || '') === sec).map(x =>
      `<li>${x.wiki ? wlink(x.wiki, x.n, true) : `<b>${esc(x.n)}</b>`}${x.by ? ` (${esc(x.by)})` : ''}: ${esc(x.note || '')}${
        x.links && x.links.length ? `<br><span class="ss-links">${x.links.map(l => wlink(l[1], l[0])).join(' · ')}</span>` : ''}</li>`).join('')}</ul>`).join('');
  }
  function card(o) {
    const jd = V.jd, at = (G.meta.at || {})[o.key];
    const yrs = o.launched ? (jd - (Date.parse(o.launched + 'T00:00:00Z') / 86400000 + 2440587.5)) / 365.25 : null;
    return `<div class="ss-card"><div class="row"><h3><span class="ss-dot" style="background:${esc(o.color)}"></span>${esc(o.name)}</h3><span class="spacer"></span><button class="b ss-x" aria-label="Close">✕</button></div>
      <div class="small muted">${esc(groupName(o.group))}${o.by ? ' · ' + esc(o.by) : ''}${o.launched ? ` · launched ${esc(o.launched)}${yrs > 0 ? ` (${num(yrs, yrs < 3 ? 1 : 0)} years ago)` : ''}` : ''}</div>
      <p class="small">${esc(o.what || '')}</p>
      <table class="small ss-facts">${liveRows(o)}</table>
      <div class="ss-media"></div>
      ${at && at.length ? `<details class="small ss-at"${at.length <= 8 ? ' open' : ''}><summary><b>Spacecraft ${o.kind === 'craft' ? 'with it' : 'here, and what was left here'}</b> <span class="muted">(${at.length}; tap a name for its Wikipedia article; list checked ${esc(G.meta.lists_checked || '')})</span></summary>${atList(at)}</details>` : ''}
      <div class="row ss-acts"><button class="b ss-centre" data-k="${esc(o.key)}">Centre on it</button><button class="b ss-share" data-k="${esc(o.key)}">Share link</button></div></div>`;
  }

  // ------------------------------------------------------------------ drawing
  // squeeze mode draws every distance as its square root, so Mercury and Voyager fit in one picture
  const T = p => { if (!V.squeeze) return p; const r = Math.hypot(p[0], p[1], p[2]); if (r < 1e-9) return p; const k = Math.sqrt(r) / r; return [p[0] * k, p[1] * k, p[2] * k]; };
  function proj(p) {
    const q = T(p), c = V.c, x = q[0] - c[0], y = q[1] - c[1], z = q[2] - c[2];
    const ca = Math.cos(V.yaw), sa = Math.sin(V.yaw), X = x * ca - y * sa, Y = x * sa + y * ca;
    return [V.w / 2 + X * V.scale, V.h / 2 - (Y * Math.cos(V.tilt) + z * Math.sin(V.tilt)) * V.scale];
  }
  function line(ctx, pts, close) {
    ctx.beginPath();
    let pen = false;
    for (const p of pts) {
      const s = proj(p);
      if (s[0] < -4000 || s[0] > V.w + 4000 || s[1] < -4000 || s[1] > V.h + 4000) { pen = false; continue; }
      if (pen) ctx.lineTo(s[0], s[1]); else { ctx.moveTo(s[0], s[1]); pen = true; }
    }
    if (close) ctx.closePath();
    ctx.stroke();
  }
  const RECENT = 150;                                     // days of path shown for a spacecraft that is not selected
  function trail(o, jd, full) {                           // stored points up to now (past) and after it (future)
    const f = G.f, b = o.off * 7, t = jd - G.t0, past = [], future = [];
    const stepSkip = Math.max(1, Math.floor(o.n / 900));
    for (let k = 0; k < o.n; k += stepSkip) {
      const i = b + k * 7;
      if (!full && Math.abs(f[i] - t) > RECENT) continue;  // the whole journey only for the one you picked: 25 full paths are a tangle
      (f[i] <= t ? past : future).push([f[i + 1], f[i + 2], f[i + 3]]);
    }
    const st = state(o, jd);
    if (st) { past.push(st.p); future.unshift(st.p); }
    return { past, future };
  }
  const SIZE = { sun: 7, jupiter: 5.5, saturn: 5, uranus: 4.2, neptune: 4.2, earth: 3.6, venus: 3.5, mars: 3, mercury: 2.6 };
  function draw() {
    if (!V || !G.ready) return;
    const cv = V.canvas, dpr = Math.min(3, window.devicePixelRatio || 1), w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    V.w = w; V.h = h;
    const ctx = cv.getContext('2d');
    if (!ctx) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const jd = V.jd, sel = V.sel ? obj(V.sel) : null;
    if (V.follow && obj(V.follow)) { const st = state(obj(V.follow), jd); if (st) V.c = T(st.p); }
    // paths of planets and small worlds
    ctx.lineWidth = 1;
    for (const o of G.meta.objects) {
      if (o.kind === 'craft' || V.hidden[o.group]) continue;
      const orb = orbit(o);
      if (!orb) continue;
      ctx.strokeStyle = o.kind === 'planet' ? 'rgba(150,170,205,.42)' : (sel === o ? 'rgba(200,220,255,.6)' : 'rgba(150,170,205,.16)');
      line(ctx, orb.pts, true);
    }
    // spacecraft trails: where each has been (solid) and where it is going (dashed)
    for (const o of G.meta.objects) {
      if (o.kind !== 'craft' || V.hidden[o.group]) continue;
      // the far travellers' long straight paths belong to the wide views; near the Sun their coarse early years are only clutter
      const wide = V.squeeze || Math.min(w, h) / 2 / V.scale > 12;
      const on = sel === o, tr = trail(o, jd, on || V.allPaths || (wide && o.group === 'leaving'));
      ctx.strokeStyle = o.color; ctx.globalAlpha = on ? 0.95 : 0.3; ctx.lineWidth = on ? 1.6 : 1;
      ctx.setLineDash([]); line(ctx, tr.past, false);
      ctx.globalAlpha = on ? 0.7 : 0.16; ctx.setLineDash([3, 5]); line(ctx, tr.future, false);
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
    // the Sun
    const sp = proj([0, 0, 0]), glow = ctx.createRadialGradient(sp[0], sp[1], 0, sp[0], sp[1], 46);
    glow.addColorStop(0, 'rgba(255,236,170,.95)'); glow.addColorStop(0.12, 'rgba(255,200,90,.55)'); glow.addColorStop(1, 'rgba(255,170,60,0)');
    ctx.fillStyle = glow; ctx.beginPath(); ctx.arc(sp[0], sp[1], 46, 0, 7); ctx.fill();
    ctx.fillStyle = '#fff3c4'; ctx.beginPath(); ctx.arc(sp[0], sp[1], SIZE.sun, 0, 7); ctx.fill();
    // bodies and labels (the selected one and planets get their label first; a label that would cover another is left out)
    V.hit = [{ key: 'sun', x: sp[0], y: sp[1] }];
    const placed = [], labels = [];
    const order = G.meta.objects.slice().sort((a, b) => ((b === sel) - (a === sel)) || ((b.kind === 'planet') - (a.kind === 'planet')) || ((b.kind === 'craft') - (a.kind === 'craft')));
    ctx.font = '12px system-ui, -apple-system, sans-serif'; ctx.textBaseline = 'middle';
    for (const o of order) {
      if (V.hidden[o.group]) continue;
      const st = state(o, jd);
      if (!st) continue;
      const s = proj(st.p);
      if (s[0] < -20 || s[0] > w + 20 || s[1] < -20 || s[1] > h + 20) continue;
      V.hit.push({ key: o.key, x: s[0], y: s[1] });
      ctx.fillStyle = o.color;
      if (o.kind === 'craft') { ctx.save(); ctx.translate(s[0], s[1]); ctx.rotate(Math.PI / 4); ctx.fillRect(-3, -3, 6, 6); ctx.restore(); }
      else { ctx.beginPath(); ctx.arc(s[0], s[1], SIZE[o.key] || 2.4, 0, 7); ctx.fill(); }
      if (o === sel) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.4; ctx.beginPath(); ctx.arc(s[0], s[1], 11, 0, 7); ctx.stroke(); }
      // the name goes right of the dot, or left, above or below it when that spot is taken; planets are always named
      const tw = ctx.measureText(o.name).width + 8;
      const spots = [[s[0] + 8, s[1] - 8], [s[0] - 8 - tw, s[1] - 8], [s[0] - tw / 2, s[1] - 26], [s[0] - tw / 2, s[1] + 10]];
      const free = p => !placed.some(q => p[0] < q[0] + q[2] && q[0] < p[0] + tw && p[1] < q[1] + q[3] && q[1] < p[1] + 16);
      const at = spots.find(free) || ((o === sel || o.kind === 'planet') ? spots[0] : null);
      if (!at) continue;
      placed.push([at[0], at[1], tw, 16]); labels.push([o, at, tw]);
    }
    for (const [o, at, tw] of labels) {
      ctx.fillStyle = 'rgba(5,8,15,.6)'; ctx.fillRect(at[0], at[1], tw, 16);
      ctx.fillStyle = o === sel ? '#fff' : o.kind === 'planet' ? '#dfe7f3' : o.color; ctx.fillText(o.name, at[0] + 4, at[1] + 8);
    }
    // scale bar
    const span = 110 / V.scale, real = V.squeeze ? null : [0.001, 0.002, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1, 2, 5, 10, 20, 50, 100, 200].filter(x => x <= span).pop();
    if (real) {
      const px = real * V.scale, x0 = 14, y0 = h - 62;
      ctx.strokeStyle = '#9aa6b8'; ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x0, y0 - 4); ctx.lineTo(x0, y0); ctx.lineTo(x0 + px, y0); ctx.lineTo(x0 + px, y0 - 4); ctx.stroke();
      ctx.fillStyle = '#9aa6b8'; ctx.fillText(`${real} AU = ${km(real * AU_KM)}`, x0 + px + 8, y0 - 2);
    }
    const lab = V.el.querySelector('.ss-when');
    if (lab) lab.textContent = V.live ? `Live · ${dateOf(jd).toLocaleString([], { dateStyle: 'medium', timeStyle: 'medium' })}` : `${fmtDate(jd)} (${V.dt > 0 ? '+' : ''}${num(V.dt, 0)} days from now)`;
    const tb = V.el.querySelector('.ss-cardbox .ss-facts');
    if (sel && tb && V.cardJd !== jd) { tb.innerHTML = liveRows(sel); V.cardJd = jd; }      // keep the open card's numbers current
  }
  let pending = false;
  const redraw = () => { if (!pending && V) { pending = true; requestAnimationFrame(() => { pending = false; tick(); draw(); }); } };
  function tick() {
    if (!V) return;
    const now = performance.now(), dts = Math.min(0.1, (now - (V.last || now)) / 1000);
    V.last = now;
    if (V.playing) {
      V.dt += V.rate * dts;
      if (V.dt >= G.meta.ahead - 0.5) { V.dt = G.meta.ahead - 0.5; V.playing = false; syncPlay(); }
      const sl = V.el.querySelector('.ss-time'); if (sl) sl.value = V.dt;
      redraw();
    }
    V.live = !V.playing && Math.abs(V.dt) < 1e-6;
    V.jd = nowJd() + V.dt;
    if (!V.playing) V.last = 0;
  }
  function syncPlay() { const b = V.el.querySelector('.ss-play'); if (b) b.textContent = V.playing ? '❚❚ Pause' : '▶ Play (1 week per second)'; }

  // ------------------------------------------------------------------ actions
  function setRadius(r) { V.scale = Math.min(V.w || 800, V.h || 500) / 2 / (V.squeeze ? Math.sqrt(r) : r); }
  function select(key, centre) {
    const o = obj(key);
    if (!o) return;
    V.sel = key; V.cardJd = null;
    V.el.querySelector('.ss-cardbox').innerHTML = card(o);
    const mh = V.el.querySelector('.ss-cardbox .ss-media');            // its Wikipedia summary, NASA's and other agencies' pictures
    if (mh && window.SpaceLabMedia) window.SpaceLabMedia.media(mh, { q: o.q || String(o.name).replace(/\s*\(.*\)/, ''), wiki: o.wiki || o.name,
      expect: o.wiki ? null : /planet|asteroid|comet|spacecraft|probe|telescope|mission|satellite|dwarf|observatory|orbiter/i, agencies: o.kind === 'craft' ? [] : ['hubble', 'webb'] });
    if (centre) {
      V.follow = key;
      const f = facts(o, V.jd);
      if (f) setRadius(o.key === 'earth' ? 0.03 : o.group === 'telescopes' || o.key === 'soho' ? 0.03 : Math.max(0.6, f.r * 1.35));
      if (o.group === 'telescopes' || o.key === 'soho') V.follow = 'earth';
    }
    V.el.querySelectorAll('.ss-pick').forEach(b => b.classList.toggle('on', b.dataset.k === key));
    redraw();
    S.track('solar/object', true);
  }
  function shareUrl(key) { return (((D().site || {}).url) || (location.origin + location.pathname)) + '?obj=' + encodeURIComponent(key) + '#solar'; }
  function copy(text, btn) {
    const done = () => { const t = btn.textContent; btn.textContent = 'Copied ✓'; setTimeout(() => { btn.textContent = t; }, 1500); };
    try { navigator.clipboard.writeText(text).then(done, () => window.prompt('Copy this link:', text)); } catch (_) { window.prompt('Copy this link:', text); }
  }
  function wire(el) {
    const cv = V.canvas, ptr = new Map();
    let moved = 0, pinch = 0, down = null;
    cv.addEventListener('pointerdown', e => { ptr.set(e.pointerId, [e.clientX, e.clientY]); try { cv.setPointerCapture(e.pointerId); } catch (_) { /* fine */ } moved = 0; down = performance.now();
      if (ptr.size === 2) { const [a, b] = [...ptr.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); } });
    cv.addEventListener('pointermove', e => {
      const p = ptr.get(e.pointerId); if (!p) return;
      const dx = e.clientX - p[0], dy = e.clientY - p[1];
      ptr.set(e.pointerId, [e.clientX, e.clientY]); moved += Math.abs(dx) + Math.abs(dy);
      if (ptr.size === 2) { const [a, b] = [...ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (pinch) V.scale = Math.max(1.5, Math.min(4e6, V.scale * d / pinch)); pinch = d; }
      else { V.yaw += dx * 0.006; V.tilt = Math.max(0, Math.min(1.5, V.tilt + dy * 0.006)); }
      redraw();
    });
    const up = e => {
      const was = ptr.size; ptr.delete(e.pointerId); pinch = 0;
      if (was === 1 && moved < 6 && performance.now() - down < 600) {
        const r = cv.getBoundingClientRect(), x = e.clientX - r.left, y = e.clientY - r.top, lim = e.pointerType === 'touch' ? 24 : 14;
        let best = null, bd = lim * lim;
        for (const hpt of V.hit || []) { const d = (hpt.x - x) * (hpt.x - x) + (hpt.y - y) * (hpt.y - y); if (d < bd && hpt.key !== 'sun') { bd = d; best = hpt.key; } }
        if (best) select(best, false);
      }
    };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('wheel', e => { e.preventDefault(); V.scale = Math.max(1.5, Math.min(4e6, V.scale * Math.exp(-e.deltaY * 0.0015))); redraw(); }, { passive: false });
    if (window.ResizeObserver) new ResizeObserver(() => redraw()).observe(cv);
    el.addEventListener('click', e => {
      const t = e.target.closest('button');
      if (!t) return;
      if (t.matches('.ss-pick')) select(t.dataset.k, true);
      else if (t.matches('.ss-x')) { V.sel = null; V.el.querySelector('.ss-cardbox').innerHTML = ''; el.querySelectorAll('.ss-pick').forEach(b => b.classList.remove('on')); redraw(); }
      else if (t.matches('.ss-centre')) select(t.dataset.k, true);
      else if (t.matches('.ss-share')) copy(shareUrl(t.dataset.k), t);
      else if (t.matches('.ss-zoom')) { V.follow = null; V.c = [0, 0, 0]; setRadius(+t.dataset.r); redraw(); }
      else if (t.matches('.ss-now')) { V.dt = 0; V.playing = false; syncPlay(); el.querySelector('.ss-time').value = 0; redraw(); }
      else if (t.matches('.ss-play')) { V.playing = !V.playing; V.last = 0; syncPlay(); redraw(); }
      else if (t.matches('.ss-paths')) { V.allPaths = !V.allPaths; t.classList.toggle('on', V.allPaths); t.setAttribute('aria-pressed', V.allPaths ? 'true' : 'false'); redraw(); }
      else if (t.matches('.ss-squeeze')) { V.squeeze = !V.squeeze; t.classList.toggle('on', V.squeeze); t.setAttribute('aria-pressed', V.squeeze ? 'true' : 'false'); V.follow = null; V.c = [0, 0, 0]; setRadius(V.squeeze ? 185 : 6.5); redraw(); }
    });
    el.querySelector('.ss-time').addEventListener('input', e => { V.dt = +e.target.value; V.playing = false; syncPlay(); redraw(); });
    el.querySelectorAll('.ss-grp').forEach(c => c.addEventListener('change', () => { V.hidden[c.dataset.g] = !c.checked; LS.set('sl_ss_hidden', V.hidden); redraw(); }));
    if (!wire.timer) wire.timer = setInterval(() => { if (V && V.el.isConnected && V.el.closest('.view.on') && !V.playing && document.visibilityState !== 'hidden') redraw(); }, 1000);
  }

  async function load() {
    if (G.ready || G.loading) return G.loading;
    const so = SO(), ver = so.made ? '?v=' + Math.round(so.made) : '';
    G.loading = (async () => {
      const [txt, buf] = await Promise.all([S.load(so.json + ver), S.loadBytes(so.bin + ver)]);
      const meta = JSON.parse(txt), bin = parseBin(buf);
      if (Math.abs(bin.t0 - meta.t0) > 1e-6 || bin.f.length < meta.objects.reduce((a, o) => Math.max(a, (o.off + o.n) * 7), 0)) throw new Error('the two files do not match; they are rebuilt once a day');
      Object.assign(G, { meta, f: bin.f, t0: bin.t0, byKey: {} });
      meta.objects.forEach(o => { G.byKey[o.key] = o; });
      G.ready = true;
    })();
    return G.loading;
  }

  function render(el) {
    const so = SO();
    if (!so || !so.bin) { el.innerHTML = '<div class="card"><h3>🪐 Solar system, live</h3><p class="muted">The map is built on the next daily update.</p></div>'; return; }
    if (V && V.el === el && G.ready) { redraw(); return; }
    el.innerHTML = `<div class="card ss-top"><div class="row"><h3>🪐 The solar system, live</h3><span class="spacer"></span><span class="small muted">${Number(so.n_craft || 0)} spacecraft and ${Number((so.n || 0) - (so.n_craft || 0))} worlds · positions from NASA/JPL</span></div>
        <p class="small">Where the planets and our spacecraft are at this moment. Drag to turn and tilt, scroll or pinch to zoom, tap anything to learn what it is and what it does. Each spacecraft shows its last few months of travel. Tap one to see its whole journey: solid where it has been since launch, dashed where it is going.</p>
        <div class="row ss-bar"><button class="b ss-zoom" data-r="2.2">Inner planets</button><button class="b ss-zoom" data-r="6.5">Out to Jupiter</button><button class="b ss-zoom" data-r="33">Out to Neptune</button><button class="b ss-zoom" data-r="185">The Voyagers</button>
          <button class="b ss-squeeze" aria-pressed="false" title="Draws distances squeezed, so everything fits in one picture">Fit everything</button><button class="b ss-paths" aria-pressed="false" title="Shows every spacecraft's whole journey since launch at once">All journeys</button><span class="spacer"></span><button class="b primary ss-full">⛶ Full screen</button></div></div>
      <div class="ss-wrap"><canvas class="ss-canvas" aria-label="Map of the solar system with planets and spacecraft. The list below the map has every object as a button."></canvas>
        <div class="ss-timebar"><button class="b ss-now">Now</button><button class="b ss-play">▶ Play (1 week per second)</button><input type="range" class="ss-time" min="-30" max="400" step="0.25" value="0" aria-label="Days from now"><span class="small mono ss-when"></span></div>
        <div class="ss-cardbox"></div><div class="ss-loading small">Loading the positions…</div></div>
      <div class="card small ss-list"></div><div class="ss-earth"></div>`;
    V = { el, canvas: el.querySelector('.ss-canvas'), c: [0, 0, 0], yaw: 0, tilt: 0.85, scale: 50, dt: 0, rate: 7, playing: false, live: true, jd: nowJd(), sel: null, follow: null, squeeze: false, hidden: LS.get('sl_ss_hidden', {}) || {}, w: 800, h: 500 };
    if (window.SpaceLabEarth) window.SpaceLabEarth.mount(el.querySelector('.ss-earth'));     // around Earth (web/earthview.js)
    load().then(() => {
      el.querySelector('.ss-loading').remove();
      V.w = V.canvas.clientWidth || 800; V.h = V.canvas.clientHeight || 500;
      setRadius(6.5);
      const m = G.meta;
      el.querySelector('.ss-list').innerHTML = `<b>Who is out there</b> <span class="muted">tap a name to fly to it; untick a group to hide it</span>
        ${(m.groups || []).map(([g, name]) => { const items = m.objects.filter(o => o.group === g); return items.length ? `<div class="ss-group"><label><input type="checkbox" class="ss-grp" data-g="${esc(g)}" ${V.hidden[g] ? '' : 'checked'}> <b>${esc(name)}</b></label> ${items.map(o => `<button class="b ss-pick" data-k="${esc(o.key)}"><span class="ss-dot" style="background:${esc(o.color)}"></span>${esc(o.name)}</button>`).join('')}</div>` : ''; }).join('')}
        <p class="muted">${esc(m.source || '')} Positions are asked from JPL once a day (last: ${new Date(m.made * 1000).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' })}) and worked out for every second in between. The time slider covers ${fmtDate(m.t0 - m.back)} to ${fmtDate(m.t0 + m.ahead)}.
          Spacecraft that sit at a planet (Mars has eight) would all fall on one dot, so they are listed on the planet's card.${(m.skipped || []).length ? ` Not shown today because JPL gave no position: ${esc(m.skipped.join(', '))}.` : ''}</p>`;
      wire(el);
      if (window.SpaceLabFull) {
        const wrap = el.querySelector('.ss-wrap'), bar = el.querySelector('.ss-bar'), home = bar.parentNode;
        window.SpaceLabFull(wrap, el.querySelector('.ss-full'), on => { if (on) { wrap.appendChild(bar); S.track('solar/fullscreen', true); } else home.appendChild(bar); redraw(); });
      } else el.querySelector('.ss-full').hidden = true;
      const want = new URLSearchParams(location.search).get('obj');
      if (want && obj(want)) select(want, true);
      redraw();
    }).catch(e => { const l = el.querySelector('.ss-loading'); if (l) l.textContent = 'The positions could not be loaded: ' + e.message; });
    S.track('solar/open', true);
  }

  S.addTab('solar', 'Solar system', render);
  window.SpaceLabSolar = { parseBin, state, orbit, facts, lightTime, km, data: G, load, select: (k, c) => select(k, c), view: () => V };
})();
