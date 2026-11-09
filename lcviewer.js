'use strict';
/* Interactive light-curve viewer: time view or folded at a period, zoom (wheel), pan (drag), double-click to reset,
   adjustable period, binning, and odd/even colouring. data = {t:[BTJD], f:[ppt], cands:[{kind,period,t0,duration,depth}]} */
const LCViewer = (() => {
  const css = 'background:var(--panel-2,#fff);border:1px solid var(--line,#ddd);border-radius:8px;width:100%;height:420px;cursor:grab;touch-action:none';
  function binned(xs, ys, w) {
    if (!w) return null;
    const m = new Map();
    for (let i = 0; i < xs.length; i++) { const k = Math.floor(xs[i] / w); const a = m.get(k) || [0, 0, 0]; a[0] += xs[i]; a[1] += ys[i]; a[2]++; m.set(k, a); }
    return [...m.values()].filter(a => a[2] >= 2).map(a => [a[0] / a[2], a[1] / a[2]]).sort((p, q) => p[0] - q[0]);
  }
  function pct(arr, p) { if (!arr.length) return 0; const s = arr.slice().sort((a, b) => a - b); return s[Math.min(s.length - 1, Math.max(0, Math.floor(p * s.length)))]; }

  function open(host, data, cand) {
    const c = cand || (data.cands || [])[0] || {};
    const st = { mode: c.period ? 'fold' : 'time', P: c.period || 0, t0: c.t0 || data.t[0], dur: c.duration || 0.1, depth: c.depth || 0, bin: 30, oe: false, view: null };
    host.innerHTML = `<div class="row lcv-bar" style="margin-bottom:8px;flex-wrap:wrap;gap:6px">
      <select class="lcv-mode"><option value="time">Brightness over time</option><option value="fold">Folded at the period</option></select>
      <label class="small">Period (d) <input class="lcv-p mono" type="number" step="0.0001" style="width:110px"></label>
      <button class="b lcv-m" title="shorter period">−</button><button class="b lcv-pl" title="longer period">+</button>
      <label class="small">Bin <select class="lcv-bin"><option value="0">none</option><option value="10">10 min</option><option value="30">30 min</option><option value="60">60 min</option></select></label>
      <label class="small"><input type="checkbox" class="lcv-oe"> odd / even dips</label>
      <button class="b lcv-reset">Reset zoom</button><span class="small muted lcv-info"></span></div>
      <canvas style="${css}"></canvas><div class="small muted" style="margin-top:4px">Scroll to zoom · drag to move · double-click to reset. A planet gives the same box-shaped dip every time; odd and even dips should match.</div>`;
    const cv = host.querySelector('canvas'), ctx = cv.getContext('2d');
    const $q = s => host.querySelector(s);
    $q('.lcv-mode').value = st.mode; $q('.lcv-p').value = st.P ? st.P.toFixed(5) : ''; $q('.lcv-bin').value = String(st.bin);
    if (!st.P) { $q('.lcv-mode').querySelector('[value=fold]').disabled = true; }

    function points() {
      const xs = [], ys = [], par = [];
      for (let i = 0; i < data.t.length; i++) {
        if (st.mode === 'fold' && st.P) {
          let ph = ((data.t[i] - st.t0) / st.P + 0.5) % 1; if (ph < 0) ph += 1; ph -= 0.5;
          xs.push(ph * st.P * 24); par.push(Math.round((data.t[i] - st.t0) / st.P) & 1);
        } else { xs.push(data.t[i]); par.push(0); }
        ys.push(data.f[i]);
      }
      return { xs, ys, par };
    }
    function defaultView(p) {
      if (st.mode === 'fold') { const h = Math.max(3 * st.dur * 24, 2); return [-h, h]; }
      return [Math.min(...p.xs), Math.max(...p.xs)];
    }
    function draw() {
      const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      const p = points();
      if (!st.view) st.view = defaultView(p);
      const [x0, x1] = st.view;
      const vis = []; for (let i = 0; i < p.xs.length; i++) if (p.xs[i] >= x0 && p.xs[i] <= x1) vis.push(i);
      const vy = vis.map(i => p.ys[i]);
      let y0 = pct(vy, 0.005), y1 = pct(vy, 0.995); const pad = (y1 - y0) * 0.12 || 1; y0 -= pad; y1 += pad;
      const L = 66, R = 12, T = 12, B = 34, pw = W - L - R, ph = H - T - B;
      const X = x => L + (x - x0) / (x1 - x0) * pw, Y = y => T + (1 - (y - y0) / (y1 - y0)) * ph;
      const cs = getComputedStyle(document.body);
      const ink = cs.getPropertyValue('--ink').trim() || '#132030', muted = cs.getPropertyValue('--muted').trim() || '#888';
      ctx.clearRect(0, 0, W, H); ctx.font = '11px system-ui'; ctx.fillStyle = muted; ctx.strokeStyle = 'rgba(128,128,128,.25)';
      for (let k = 0; k <= 5; k++) {
        const yv = y0 + (y1 - y0) * k / 5, xv = x0 + (x1 - x0) * k / 5;
        ctx.beginPath(); ctx.moveTo(L, Y(yv)); ctx.lineTo(W - R, Y(yv)); ctx.stroke();
        ctx.fillText(yv.toFixed(2), 22, Y(yv) + 4); ctx.fillText(st.mode === 'fold' ? xv.toFixed(1) + ' h' : xv.toFixed(2), X(xv) - 18, H - 14);
      }
      ctx.fillText(st.mode === 'fold' ? 'hours from the middle of the dip' : 'time (days, BTJD)', L + pw / 2 - 60, H - 2);
      ctx.save(); ctx.translate(11, T + ph / 2 + 60); ctx.rotate(-Math.PI / 2); ctx.fillText('brightness change (ppt)', 0, 0); ctx.restore();
      if (st.mode === 'time' && st.P) {
        ctx.strokeStyle = 'rgba(208,59,59,.35)';
        for (let n = Math.ceil((x0 - st.t0) / st.P); st.t0 + n * st.P <= x1; n++) { const x = X(st.t0 + n * st.P); ctx.beginPath(); ctx.moveTo(x, T); ctx.lineTo(x, T + ph); ctx.stroke(); }
      }
      const step = Math.max(1, Math.floor(vis.length / 40000));
      for (let j = 0; j < vis.length; j += step) {
        const i = vis[j];
        ctx.fillStyle = st.oe && st.mode === 'fold' ? (p.par[i] ? 'rgba(235,104,52,.45)' : 'rgba(42,120,214,.45)') : 'rgba(128,128,128,.45)';
        ctx.fillRect(X(p.xs[i]) - 1, Y(p.ys[i]) - 1, 2, 2);
      }
      const bw = st.bin ? (st.mode === 'fold' ? st.bin / 60 : st.bin / 1440) : 0;
      const groups = st.oe && st.mode === 'fold' ? [[0, '#2a78d6'], [1, '#eb6834']] : [[null, ink]];
      for (const [par, col] of groups) {
        const ix = vis.filter(i => par === null || p.par[i] === par);
        const bb = binned(ix.map(i => p.xs[i]), ix.map(i => p.ys[i]), bw);
        if (!bb) continue;
        ctx.fillStyle = col; for (const [x, y] of bb) { ctx.beginPath(); ctx.arc(X(x), Y(y), 2.6, 0, 7); ctx.fill(); }
      }
      if (st.mode === 'fold' && st.depth) {
        const d = -st.depth * 1e3, h = st.dur * 12; ctx.strokeStyle = '#d03b3b'; ctx.lineWidth = 1.5; ctx.beginPath();
        ctx.moveTo(X(x0), Y(0)); ctx.lineTo(X(-h), Y(0)); ctx.lineTo(X(-h), Y(d)); ctx.lineTo(X(h), Y(d)); ctx.lineTo(X(h), Y(0)); ctx.lineTo(X(x1), Y(0)); ctx.stroke(); ctx.lineWidth = 1;
      }
      $q('.lcv-info').textContent = `${vis.length.toLocaleString()} points shown` + (data.kind ? ` · ${data.kind}` : '') + (data.sectors ? ` · sectors ${data.sectors.join(', ')}` : '');
      cv._map = { L, pw };
    }
    $q('.lcv-mode').onchange = e => { st.mode = e.target.value; st.view = null; draw(); };
    $q('.lcv-p').onchange = e => { const v = parseFloat(e.target.value); if (v > 0) { st.P = v; st.view = null; draw(); } };
    $q('.lcv-m').onclick = () => { st.P *= 1 - 2e-4; $q('.lcv-p').value = st.P.toFixed(5); draw(); };
    $q('.lcv-pl').onclick = () => { st.P *= 1 + 2e-4; $q('.lcv-p').value = st.P.toFixed(5); draw(); };
    $q('.lcv-bin').onchange = e => { st.bin = +e.target.value; draw(); };
    $q('.lcv-oe').onchange = e => { st.oe = e.target.checked; draw(); };
    $q('.lcv-reset').onclick = () => { st.view = null; draw(); };
    cv.addEventListener('dblclick', () => { st.view = null; draw(); });
    cv.addEventListener('wheel', e => {
      e.preventDefault(); const m = cv._map; if (!m || !st.view) return;
      const r = cv.getBoundingClientRect(), fx = Math.min(1, Math.max(0, (e.clientX - r.left - m.L) / m.pw));
      const [a, b] = st.view, xm = a + (b - a) * fx, k = e.deltaY > 0 ? 1.25 : 0.8;
      st.view = [xm - (xm - a) * k, xm + (b - xm) * k]; draw();
    }, { passive: false });
    let drag = null;
    cv.addEventListener('pointerdown', e => { drag = { x: e.clientX, v: st.view.slice() }; cv.setPointerCapture(e.pointerId); cv.style.cursor = 'grabbing'; });
    cv.addEventListener('pointermove', e => { if (!drag) return; const m = cv._map, dx = (e.clientX - drag.x) / m.pw * (drag.v[1] - drag.v[0]); st.view = [drag.v[0] - dx, drag.v[1] - dx]; draw(); });
    cv.addEventListener('pointerup', () => { drag = null; cv.style.cursor = 'grab'; });
    new ResizeObserver(() => draw()).observe(cv);
    draw();
  }
  return { open };
})();
