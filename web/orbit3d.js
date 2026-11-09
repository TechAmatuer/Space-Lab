'use strict';
/* 3D view of an asteroid's orbit among the inner planets. Drag to turn, scroll to zoom, slider to move in time.
   obj = {name, a, e, i, om, w, ma, epoch (JD)} from JPL's Small-Body Database. */
const Orbit3D = (() => {
  const D = Math.PI / 180, J2000 = 2451545.0;
  const PLANETS = [   // a, e, i, node, long. of perihelion, mean longitude at J2000
    ['Mercury', 0.38710, 0.20563, 7.005, 48.331, 77.456, 252.251, '#9a9a9a'],
    ['Venus', 0.72333, 0.00677, 3.395, 76.680, 131.533, 181.980, '#d8b26e'],
    ['Earth', 1.00000, 0.01671, 0.000, 0.000, 102.947, 100.464, '#3d8ee6'],
    ['Mars', 1.52368, 0.09340, 1.850, 49.558, 336.060, 355.453, '#d0583b'],
    ['Jupiter', 5.20260, 0.04849, 1.303, 100.464, 14.331, 34.351, '#c99a63'],
  ].map(([name, a, e, i, om, lp, L, col]) => ({ name, a, e, i, om, w: lp - om, ma: L - lp, epoch: J2000, col }));

  function pos(o, jd, E) {
    if (E === undefined) {
      const n = o.n || 0.9856076686 / Math.pow(o.a, 1.5);
      const M = ((o.ma + n * (jd - o.epoch)) % 360) * D;
      E = M; for (let k = 0; k < 30; k++) E = E - (E - o.e * Math.sin(E) - M) / (1 - o.e * Math.cos(E));
    }
    const xp = o.a * (Math.cos(E) - o.e), yp = o.a * Math.sqrt(1 - o.e * o.e) * Math.sin(E);
    const cw = Math.cos(o.w * D), sw = Math.sin(o.w * D), co = Math.cos(o.om * D), so = Math.sin(o.om * D), ci = Math.cos(o.i * D), si = Math.sin(o.i * D);
    return [(cw * co - sw * so * ci) * xp + (-sw * co - cw * so * ci) * yp, (cw * so + sw * co * ci) * xp + (-sw * so + cw * co * ci) * yp, (sw * si) * xp + (cw * si) * yp];
  }

  function open(host, obj) {
    const jd0 = Date.now() / 86400000 + 2440587.5;
    const st = { yaw: -0.6, pitch: 0.9, zoom: Math.min(6, 1.1 * Math.max(1.25, obj.a * (1 + obj.e))), dt: 0 };
    host.innerHTML = `<canvas style="width:100%;height:440px;background:#05070d;border-radius:8px;cursor:grab;touch-action:none"></canvas>
      <div class="row small" style="margin-top:6px"><span>Date</span><input type="range" min="-730" max="730" value="0" style="flex:1"><span class="o3-date mono"></span></div>
      <div class="small muted">Drag to turn · scroll to zoom. Sizes are not to scale. Orbit from JPL's Small-Body Database.</div>`;
    const cv = host.querySelector('canvas'), ctx = cv.getContext('2d'), slider = host.querySelector('input');
    const ast = { ...obj, col: '#7dff9a', name: obj.name || obj.des };
    const objs = PLANETS.filter(p => p.a < Math.max(2.2, ast.a * (1 + ast.e) * 1.2)).concat([ast]);
    function project(v, W, H) {
      const [x, y, z] = v, cy = Math.cos(st.yaw), sy = Math.sin(st.yaw), cp = Math.cos(st.pitch), sp = Math.sin(st.pitch);
      const x1 = cy * x - sy * y, y1 = sy * x + cy * y;
      const y2 = cp * y1 - sp * z, z2 = sp * y1 + cp * z;
      const s = Math.min(W, H) / 2 / st.zoom, f = 1 / (1 + y2 * 0.04);
      return [W / 2 + x1 * s * f, H / 2 - z2 * s * f, y2];
    }
    function draw() {
      const dpr = window.devicePixelRatio || 1, W = cv.clientWidth, H = cv.clientHeight;
      cv.width = W * dpr; cv.height = H * dpr; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, W, H);
      const jd = jd0 + st.dt;
      host.querySelector('.o3-date').textContent = new Date((jd - 2440587.5) * 86400000).toISOString().slice(0, 10);
      ctx.fillStyle = '#ffd34d'; const s0 = project([0, 0, 0], W, H); ctx.beginPath(); ctx.arc(s0[0], s0[1], 6, 0, 7); ctx.fill();
      ctx.font = '12px system-ui';
      for (const o of objs) {
        ctx.strokeStyle = o === ast ? 'rgba(125,255,154,.9)' : 'rgba(170,190,220,.35)'; ctx.lineWidth = o === ast ? 1.8 : 1;
        ctx.beginPath();
        for (let k = 0; k <= 240; k++) { const p = project(pos(o, jd, k / 240 * 2 * Math.PI), W, H); k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); }
        ctx.stroke();
        if (o === ast) {   // part of the orbit below the planets' plane drawn dashed
          ctx.setLineDash([3, 4]); ctx.strokeStyle = 'rgba(125,255,154,.35)'; ctx.beginPath();
          for (let k = 0; k <= 240; k++) { const v = pos(o, jd, k / 240 * 2 * Math.PI), p = project([v[0], v[1], 0], W, H); k ? ctx.lineTo(p[0], p[1]) : ctx.moveTo(p[0], p[1]); }
          ctx.stroke(); ctx.setLineDash([]);
        }
        const p = project(pos(o, jd), W, H);
        ctx.fillStyle = o.col; ctx.beginPath(); ctx.arc(p[0], p[1], o === ast ? 5 : 4, 0, 7); ctx.fill();
        ctx.fillStyle = 'rgba(230,236,245,.85)'; ctx.fillText(o.name, p[0] + 7, p[1] - 6);
      }
      const ep = pos(PLANETS[2], jd), ap = pos(ast, jd);
      const dist = Math.hypot(ep[0] - ap[0], ep[1] - ap[1], ep[2] - ap[2]);
      ctx.fillStyle = 'rgba(230,236,245,.8)';
      ctx.fillText(`${ast.name}: a ${(+ast.a).toFixed(2)} AU · e ${(+ast.e).toFixed(2)} · i ${(+ast.i).toFixed(1)}° · ${dist.toFixed(2)} AU from Earth on this date`, 10, H - 12);
    }
    let drag = null;
    cv.addEventListener('pointerdown', e => { drag = { x: e.clientX, y: e.clientY, yaw: st.yaw, pitch: st.pitch }; cv.setPointerCapture(e.pointerId); });
    cv.addEventListener('pointermove', e => { if (!drag) return; st.yaw = drag.yaw + (e.clientX - drag.x) * 0.01; st.pitch = Math.max(-1.55, Math.min(1.55, drag.pitch + (e.clientY - drag.y) * 0.01)); draw(); });
    cv.addEventListener('pointerup', () => { drag = null; });
    cv.addEventListener('wheel', e => { e.preventDefault(); st.zoom *= e.deltaY > 0 ? 1.15 : 0.87; draw(); }, { passive: false });
    slider.oninput = () => { st.dt = +slider.value; draw(); };
    new ResizeObserver(() => draw()).observe(cv);
    draw();
  }
  return { open, pos };
})();
