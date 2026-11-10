// Space Lab 3D universe: the local universe from about 43,500 real galaxies of the 2MASS Redshift Survey, out to about a
// billion light-years. Orbit around the Milky Way or fly through it, tap any galaxy for its identity card (2MASS ID, distance,
// void / sheet / filament / knot, J2000 coordinates to paste into Stellarium, NED and SIMBAD), search by name, Messier or
// NGC number, landmark or 2MASS ID, and share a link to a galaxy or a view.
// Plain WebGL, no libraries; every file comes from this same site (built by spacelab/universe.py). Runs on the public page
// and inside Meridian through window.SpaceLab.
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const D = () => S.data || {};
  const U = () => ((D().beyond || {}).universe) || null;
  const MLY = 3.26156;                                           // million light-years per megaparsec
  const ENV = [['void', 'in a void', 'an emptier-than-average region of space', '#4ee0b5'],
               ['sheet', 'on a sheet', 'a thin wall of galaxies', '#7fb3ff'],
               ['filament', 'in a filament', 'a thread of galaxies joining clusters', '#f2efe6'],
               ['knot', 'in a knot', 'a cluster, where filaments meet', '#ffb454'],
               ['unknown', 'too far to classify', 'too few galaxies are seen that far away to tell', '#8a8f98']];
  const MORPH = [['type not known', '#9aa0a6'], ['elliptical', '#ff6b5e'], ['lenticular (S0)', '#ffa057'], ['early spiral (Sa–Sb)', '#ffd84d'],
                 ['late spiral (Sc–Sd)', '#6fa8ff'], ['irregular', '#57e3e3']];
  const LS = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } } };

  // ------------------------------------------------------------------ data
  const G = { ready: false };
  function parseBin(buf) {
    const dv = new DataView(buf);
    if (dv.getUint8(0) !== 83 || dv.getUint8(1) !== 76 || dv.getUint8(2) !== 85) throw new Error('not a Space Lab galaxy file');
    const n = dv.getUint32(4, true), o = 16;
    return { n, xyz: new Float32Array(buf, o, 3 * n), env: new Uint8Array(buf, o + 12 * n, n), kb: new Uint8Array(buf, o + 13 * n, n),
             mt: new Uint8Array(buf, o + 14 * n, n), nmb: new Uint8Array(buf, o + 15 * n, n), cz: new Int32Array(buf, o + 16 * n, n),
             nest: new Uint32Array(buf, o + 20 * n, n) };
  }
  function parseTxt(t) {
    const ids = [], names = [];
    for (const ln of String(t || '').split('\n')) { if (!ln) continue; const k = ln.indexOf('\t'); ids.push(k < 0 ? ln : ln.slice(0, k)); names.push(k < 0 ? '' : ln.slice(k + 1)); }
    return { ids, names };
  }
  const kmag = i => 3 + G.kb[i] / 20;
  const dist = i => Math.hypot(G.xyz[3 * i], G.xyz[3 * i + 1], G.xyz[3 * i + 2]);
  const famousOf = i => (G.famousByI || {})[i] || null;
  const nameOf = i => { const f = famousOf(i); return f ? f.name : (G.names[i] || ('2MASX J' + G.ids[i])); };

  // J2000 position straight from the 2MASS ID (it encodes RA hhmmss.ss and Dec ddmmss.s)
  function idRaDec(id) {
    const m = /^(\d{2})(\d{2})(\d{2})(\d{2})([+-])(\d{2})(\d{2})(\d{2})(\d)$/.exec(String(id || ''));
    if (!m) return null;
    const rah = +m[1], ram = +m[2], ras = +(m[3] + '.' + m[4]), sg = m[5], dd = +m[6], dm = +m[7], ds = +(m[8] + '.' + m[9]);
    return { ra: (rah + ram / 60 + ras / 3600) * 15, dec: (sg === '-' ? -1 : 1) * (dd + dm / 60 + ds / 3600),
             hms: `${m[1]}h${m[2]}m${m[3]}.${m[4]}s`, dms: `${sg}${m[6]}°${m[7]}′${m[8]}.${m[9]}″`,
             stellarium: `${m[1]}h${m[2]}m${m[3]}.${m[4]}s ${sg}${m[6]}d${m[7]}m${m[8]}.${m[9]}s` };
  }
  function vecRaDec(v) {
    const r = Math.hypot(v[0], v[1], v[2]) || 1;
    let ra = Math.atan2(v[1], v[0]) * 180 / Math.PI; if (ra < 0) ra += 360;
    return { ra, dec: Math.asin(Math.max(-1, Math.min(1, v[2] / r))) * 180 / Math.PI };
  }
  const fmtRa = ra => { const h = ra / 15, hh = Math.floor(h), mm = Math.floor((h - hh) * 60); return `${hh}h ${String(mm).padStart(2, '0')}m`; };
  const fmtDec = d => `${d < 0 ? '−' : '+'}${Math.abs(d).toFixed(1)}°`;
  const fmtDist = d => d < 1 ? `${num(d * 1000, 0)} thousand parsecs` : `${num(d, d < 10 ? 1 : 0)} Mpc (about ${d * MLY < 1000 ? num(d * MLY, d * MLY < 10 ? 1 : 0) + ' million' : num(d * MLY / 1000, 2) + ' billion'} light-years)`;

  // ------------------------------------------------------------------ search
  const norm = s => String(s || '').toLowerCase().replace(/[\s_]+/g, ' ').replace(/^messier /, 'm').trim();
  function search(q) {
    const u = U() || {}, s = norm(q), out = [];
    if (!s) return out;
    const sq = s.replace(/\s/g, '');
    const mm = /^m ?(\d{1,3})$/.exec(s);
    const alias = mm ? norm((u.messier || {})['M' + mm[1]] || '') : '';
    const np = (u.blueshift || []).find(b => b.name && (norm(b.name) === s || norm(b.cat) === s || (alias && norm(b.cat) === alias) || (s.length >= 4 && norm(b.name).includes(s))));
    const home = /milky ?way|local group|magellanic/.test(s);
    if (home || np || /andromeda|triangulum|^m ?(31|32|33|110)$/.test(s)) out.push({ kind: 'note', label: home ? 'The Milky Way and its neighbours' : ((np ? np.name : q.trim()) + ': not on this map'),
      text: home ? 'Home is the bright marker in the middle. The Milky Way, its satellites and the rest of the Local Group are all within about 1 Mpc, too close for this map to separate.'
        : np && np.why === 'near' ? (u.near_note || 'Too close for a redshift distance.') : (u.blueshift_note || 'It moves towards us, so it cannot be placed on a redshift map.') });
    (u.landmarks || []).forEach((l, k) => { if (norm(l.name).includes(s)) out.push({ kind: 'landmark', k, label: l.name, sub: l.kind }); });
    (u.famous || []).forEach(f => { if (norm(f.name).includes(s) || norm(f.cat) === s || (alias && norm(f.cat) === alias)) out.push({ kind: 'galaxy', i: f.i, label: f.name, sub: f.cat }); });
    if (G.ready) {
      const want = alias || s;
      for (let i = 0; i < G.n && out.length < 12; i++) {
        const nm = norm(G.names[i]);
        if ((nm && (nm === want || nm.replace(/\s/g, '') === want.replace(/\s/g, ''))) || (sq.replace(/^2masxj?/, '').length >= 6 && G.ids[i].startsWith(sq.replace(/^2masxj?/, '')))) {
          if (!out.some(o => o.i === i)) out.push({ kind: 'galaxy', i, label: nameOf(i), sub: G.names[i] && nameOf(i) !== G.names[i] ? G.names[i] : '2MASX J' + G.ids[i] });
        }
      }
      if (out.filter(o => o.kind !== 'note').length < 3 && s.length >= 3) {
        for (let i = 0; i < G.n && out.length < 12; i++) if (norm(G.names[i]).startsWith(s) && !out.some(o => o.i === i)) out.push({ kind: 'galaxy', i, label: nameOf(i), sub: G.names[i] });
      }
    }
    return out.slice(0, 12);
  }

  // ------------------------------------------------------------------ cards
  function galaxyCard(i) {
    const id = G.ids[i], p = idRaDec(id), d = dist(i), e = Math.min(G.env[i], 4), f = famousOf(i), n = G.nmb[i];
    const grp = n >= 30 ? `a member of a rich cluster (${n} galaxies of this survey)` : n >= 2 ? `a member of a group of ${n} galaxies of this survey` : 'not in any group of this survey';
    const ned = `https://ned.ipac.caltech.edu/byname?objname=${encodeURIComponent('2MASX J' + id)}`;
    const sim = `https://simbad.cds.unistra.fr/simbad/sim-id?Ident=${encodeURIComponent('2MASX J' + id)}`;
    return `<div class="uv-card"><div class="row"><h3>${esc(nameOf(i))}</h3><span class="spacer"></span><button class="b uv-x" aria-label="Close">✕</button></div>
      ${f && f.note ? `<p class="small">${esc(f.note)}</p>` : ''}
      <table class="small uv-facts"><tr><th>2MASS ID</th><td class="mono">2MASX J${esc(id)}${G.names[i] ? ` · ${esc(G.names[i])}` : ''}</td></tr>
        <tr><th>Distance</th><td>${fmtDist(d)}${d < 6 ? '<br><span class="muted">So close that the galaxy\'s own motion matters more than the expansion of the universe: this distance is rough.</span>' : ''}</td></tr>
        <tr><th>Moving away</th><td>${G.cz[i].toLocaleString()} km/s (redshift ${num(G.cz[i] / 299792.458, 4)})</td></tr>
        <tr><th>Where it lives</th><td><span class="uv-dot" style="background:${ENV[e][3]}"></span> <b>${ENV[e][1]}</b>: ${ENV[e][2]}; ${grp}</td></tr>
        <tr><th>Type</th><td>${MORPH[G.mt[i]] ? MORPH[G.mt[i]][0] : '—'} · infrared brightness Ks ${num(kmag(i), 2)}</td></tr>
        ${p ? `<tr><th>J2000</th><td class="mono">RA ${p.hms} · Dec ${p.dms}</td></tr>` : ''}</table>
      <div class="row uv-acts">${p ? `<button class="b uv-copy" data-c="${esc(p.stellarium)}">Copy for Stellarium</button>` : ''}
        <a href="${ned}" target="_blank" rel="noopener">NED ↗</a><a href="${sim}" target="_blank" rel="noopener">SIMBAD ↗</a>
        <button class="b uv-go" data-i="${i}">Fly here</button><button class="b uv-share" data-sel="${esc(id)}">Share link</button></div></div>`;
  }
  function landmarkCard(k) {
    const l = (U().landmarks || [])[k];
    return `<div class="uv-card"><div class="row"><h3>${esc(l.name)}</h3><span class="spacer"></span><button class="b uv-x" aria-label="Close">✕</button></div>
      <p class="small">${esc(l.note)}</p><p class="small muted">${fmtDist(l.dist)} · RA ${fmtRa(l.ra)} · Dec ${fmtDec(l.dec)} · ${l.measured ? 'centre of the matching galaxy group' : 'approximate centre from the literature'}</p>
      <div class="row uv-acts"><button class="b uv-golm" data-k="${k}">Fly here</button></div></div>`;
  }

  // ------------------------------------------------------------------ small linear algebra
  const qmul = (a, b) => [a[3] * b[0] + a[0] * b[3] + a[1] * b[2] - a[2] * b[1], a[3] * b[1] - a[0] * b[2] + a[1] * b[3] + a[2] * b[0],
                          a[3] * b[2] + a[0] * b[1] - a[1] * b[0] + a[2] * b[3], a[3] * b[3] - a[0] * b[0] - a[1] * b[1] - a[2] * b[2]];
  const qaxis = (ax, ang) => { const s = Math.sin(ang / 2), n = Math.hypot(ax[0], ax[1], ax[2]) || 1; return [ax[0] / n * s, ax[1] / n * s, ax[2] / n * s, Math.cos(ang / 2)]; };
  const qnorm = q => { const n = Math.hypot(q[0], q[1], q[2], q[3]) || 1; return q.map(x => x / n); };
  function qrot(q, v) {
    const [x, y, z, w] = q, ix = w * v[0] + y * v[2] - z * v[1], iy = w * v[1] + z * v[0] - x * v[2], iz = w * v[2] + x * v[1] - y * v[0], iw = -x * v[0] - y * v[1] - z * v[2];
    return [ix * w + iw * -x + iy * -z - iz * -y, iy * w + iw * -y + iz * -x - ix * -z, iz * w + iw * -z + ix * -y - iy * -x];
  }
  function qfromBasis(r, u, b) {                                 // columns: right, up, back
    const m00 = r[0], m11 = u[1], m22 = b[2], t = m00 + m11 + m22;
    let q;
    if (t > 0) { const s = Math.sqrt(t + 1) * 2; q = [(u[2] - b[1]) / s, (b[0] - r[2]) / s, (r[1] - u[0]) / s, s / 4]; }
    else if (m00 > m11 && m00 > m22) { const s = Math.sqrt(1 + m00 - m11 - m22) * 2; q = [s / 4, (u[0] + r[1]) / s, (b[0] + r[2]) / s, (u[2] - b[1]) / s]; }
    else if (m11 > m22) { const s = Math.sqrt(1 + m11 - m00 - m22) * 2; q = [(u[0] + r[1]) / s, s / 4, (b[1] + u[2]) / s, (b[0] - r[2]) / s]; }
    else { const s = Math.sqrt(1 + m22 - m00 - m11) * 2; q = [(b[0] + r[2]) / s, (b[1] + u[2]) / s, s / 4, (r[1] - u[0]) / s]; }
    return qnorm(q);
  }
  const cross = (a, b) => [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]];
  const unit3 = v => { const n = Math.hypot(v[0], v[1], v[2]) || 1; return [v[0] / n, v[1] / n, v[2] / n]; };
  function viewProj(cam, aspect) {
    const eye = cam.mode === 'fly' ? cam.eye : add3(cam.t, qrot(cam.q, [0, 0, cam.d]));
    const r = qrot(cam.q, [1, 0, 0]), u = qrot(cam.q, [0, 1, 0]), b = qrot(cam.q, [0, 0, 1]);
    const V = [r[0], u[0], b[0], 0, r[1], u[1], b[1], 0, r[2], u[2], b[2], 0,
               -(r[0] * eye[0] + r[1] * eye[1] + r[2] * eye[2]), -(u[0] * eye[0] + u[1] * eye[1] + u[2] * eye[2]), -(b[0] * eye[0] + b[1] * eye[1] + b[2] * eye[2]), 1];
    const f = 1 / Math.tan(cam.fov / 2), near = 0.05, far = 8000;
    const P = [f / aspect, 0, 0, 0, 0, f, 0, 0, 0, 0, (far + near) / (near - far), -1, 0, 0, 2 * far * near / (near - far), 0];
    const M = new Float32Array(16);
    for (let c = 0; c < 4; c++) for (let rr = 0; rr < 4; rr++) { let s = 0; for (let k = 0; k < 4; k++) s += P[k * 4 + rr] * V[c * 4 + k]; M[c * 4 + rr] = s; }
    return { M, eye, fwd: b.map(x => -x), right: r, up: u };
  }
  const add3 = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  function project(M, p, w, h) {
    const x = M[0] * p[0] + M[4] * p[1] + M[8] * p[2] + M[12], y = M[1] * p[0] + M[5] * p[1] + M[9] * p[2] + M[13], ww = M[3] * p[0] + M[7] * p[1] + M[11] * p[2] + M[15];
    if (ww <= 0.01) return null;
    return [(x / ww * 0.5 + 0.5) * w, (1 - (y / ww * 0.5 + 0.5)) * h, ww];
  }

  // ------------------------------------------------------------------ the viewer
  let V = null;                          // { el, gl, cam, ... } while the tab is open
  function defaultCam() {
    const m = (U() || {}).eq_to_sg || [[1, 0, 0], [0, 1, 0], [0, 0, 1]];
    const sgy = m[1], sgz = m[2];
    const back = unit3([-0.8 * sgy[0] + 0.6 * sgz[0], -0.8 * sgy[1] + 0.6 * sgz[1], -0.8 * sgy[2] + 0.6 * sgz[2]]);
    const right = unit3(cross(sgz, back)), up = cross(back, right);
    return { mode: 'orbit', t: [0, 0, 0], d: 290, q: qfromBasis(right, up, back), fov: 55 * Math.PI / 180, eye: [0, 0, 0] };
  }
  function colorsFor(mode) {
    const c = new Float32Array(G.n * 3);
    const hex = h => [parseInt(h.slice(1, 3), 16) / 255, parseInt(h.slice(3, 5), 16) / 255, parseInt(h.slice(5, 7), 16) / 255];
    const envC = ENV.map(e => hex(e[3])), mC = MORPH.map(m => hex(m[1]));
    for (let i = 0; i < G.n; i++) {
      let col;
      if (mode === 'type') col = mC[G.mt[i]] || mC[0];
      else if (mode === 'dist') { const t = Math.min(1, dist(i) / 300); col = [0.35 + 0.65 * t, 0.75 - 0.35 * t, 1 - 0.75 * t]; }
      else col = envC[Math.min(G.env[i], 4)];
      c[3 * i] = col[0]; c[3 * i + 1] = col[1]; c[3 * i + 2] = col[2];
    }
    return c;
  }
  function initGL(canvas) {
    const gl = canvas.getContext('webgl', { antialias: true, alpha: false }) || canvas.getContext('experimental-webgl');
    if (!gl) return null;
    const sh = (type, src) => { const s = gl.createShader(type); gl.shaderSource(s, src); gl.compileShader(s); if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) throw new Error(gl.getShaderInfoLog(s)); return s; };
    const prog = (vs, fs) => { const p = gl.createProgram(); gl.attachShader(p, sh(gl.VERTEX_SHADER, vs)); gl.attachShader(p, sh(gl.FRAGMENT_SHADER, fs)); gl.linkProgram(p); return p; };
    const pts = prog(`attribute vec3 p; attribute vec3 c; attribute float s; uniform mat4 M; uniform vec3 eye; uniform float k; uniform float dpr; varying vec3 vc; varying float va;
      void main(){ gl_Position = M * vec4(p,1.0); float d = max(distance(p, eye), 0.5); float px = s * k / d; gl_PointSize = clamp(px, 2.2 * dpr, 26.0 * dpr); vc = c; va = clamp(px / (3.0 * dpr), 0.55, 1.0); }`,
      `precision mediump float; varying vec3 vc; varying float va; void main(){ vec2 q = gl_PointCoord - 0.5; float r = dot(q,q) * 4.0; if (r > 1.0) discard; gl_FragColor = vec4(vc * va * (0.25 + 1.1 * exp(-r * 2.5)), 1.0); }`);
    const lines = prog(`attribute vec3 p; uniform mat4 M; void main(){ gl_Position = M * vec4(p,1.0); }`,
      `precision mediump float; uniform vec4 col; void main(){ gl_FragColor = col; }`);
    const buf = data => { const b = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.bufferData(gl.ARRAY_BUFFER, data, gl.STATIC_DRAW); return b; };
    const size = new Float32Array(G.n);
    for (let i = 0; i < G.n; i++) size[i] = 0.25 + Math.max(0, 11.9 - kmag(i)) * 0.11;
    // distance rings in the supergalactic plane, every 100 Mpc, and the Milky Way's plane at home
    const m = (U() || {}).eq_to_sg || [[1, 0, 0], [0, 1, 0], [0, 0, 1]], ring = [];
    for (const R of [100, 200, 300]) for (let a = 0; a < 128; a++) for (const t of [a, a + 1]) { const ang = t / 128 * 2 * Math.PI; for (let j = 0; j < 3; j++) ring.push(R * (Math.cos(ang) * m[0][j] + Math.sin(ang) * m[1][j])); }
    return { gl, pts, lines, pos: buf(G.xyz), col: buf(colorsFor(LS.get('sl_uv_color', 'env'))), size: buf(size), ring: buf(new Float32Array(ring)), nring: ring.length / 3 };
  }
  function draw() {
    if (!V || !V.glx) return;
    const { gl, pts, lines } = V.glx, cv = V.canvas, dpr = Math.min(2, window.devicePixelRatio || 1);
    const w = cv.clientWidth, h = cv.clientHeight;
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) { cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr); }
    gl.viewport(0, 0, cv.width, cv.height);
    gl.clearColor(0.012, 0.016, 0.03, 1); gl.clear(gl.COLOR_BUFFER_BIT);
    const vp = viewProj(V.cam, w / Math.max(h, 1));
    V.vp = vp;
    gl.useProgram(lines);
    gl.uniformMatrix4fv(gl.getUniformLocation(lines, 'M'), false, vp.M);
    gl.uniform4f(gl.getUniformLocation(lines, 'col'), 0.25, 0.3, 0.4, 1);
    gl.bindBuffer(gl.ARRAY_BUFFER, V.glx.ring); let a = gl.getAttribLocation(lines, 'p'); gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, 3, gl.FLOAT, false, 0, 0);
    if (LS.get('sl_uv_rings', true)) gl.drawArrays(gl.LINES, 0, V.glx.nring);
    gl.disableVertexAttribArray(a);
    gl.useProgram(pts);
    gl.enable(gl.BLEND); gl.blendFunc(gl.ONE, gl.ONE);
    gl.uniformMatrix4fv(gl.getUniformLocation(pts, 'M'), false, vp.M);
    gl.uniform3fv(gl.getUniformLocation(pts, 'eye'), vp.eye);
    gl.uniform1f(gl.getUniformLocation(pts, 'k'), cv.height / 2 / Math.tan(V.cam.fov / 2));
    gl.uniform1f(gl.getUniformLocation(pts, 'dpr'), dpr);
    for (const [nm, b, sz] of [['p', V.glx.pos, 3], ['c', V.glx.col, 3], ['s', V.glx.size, 1]]) { a = gl.getAttribLocation(pts, nm); gl.bindBuffer(gl.ARRAY_BUFFER, b); gl.enableVertexAttribArray(a); gl.vertexAttribPointer(a, sz, gl.FLOAT, false, 0, 0); }
    gl.drawArrays(gl.POINTS, 0, G.n);
    gl.disable(gl.BLEND);
    overlay();
  }
  let pending = false;
  const redraw = () => { if (!pending && V) { pending = true; requestAnimationFrame(() => { pending = false; step(); draw(); }); } };

  function overlay() {
    const box = V.el.querySelector('.uv-labels'), w = V.canvas.clientWidth, h = V.canvas.clientHeight, M = V.vp.M;
    const on = LS.get('sl_uv_labels', true);
    let html = '';
    const home = project(M, [0, 0, 0], w, h);
    if (home) html += `<div class="uv-home" style="transform:translate(${home[0].toFixed(0)}px,${home[1].toFixed(0)}px)" title="The Milky Way">✦<span>Milky Way</span></div>`;
    if (on) {                                         // nearest first; a label that would overlap one already shown is left out
      const placed = [];
      (U().landmarks || []).map((l, k) => ({ l, k, p: project(M, l.xyz, w, h) })).filter(o => o.p).sort((a, b) => a.p[2] - b.p[2]).forEach(({ l, k, p }) => {
        if (!(p[0] > -40 && p[0] < w + 40 && p[1] > -10 && p[1] < h + 10)) return;
        const wd = 7 * l.name.length + 14;
        if (placed.some(q => Math.abs(q[1] - p[1]) < 16 && p[0] < q[0] + q[2] && q[0] < p[0] + wd)) return;
        placed.push([p[0], p[1], wd]);
        html += `<button class="uv-lm uv-${l.kind}" data-k="${k}" style="transform:translate(${p[0].toFixed(0)}px,${p[1].toFixed(0)}px)">${esc(l.name)}</button>`;
      });
    }
    if (V.sel !== null && V.sel !== undefined) {
      const i = V.sel, p = project(M, [G.xyz[3 * i], G.xyz[3 * i + 1], G.xyz[3 * i + 2]], w, h);
      if (p) html += `<div class="uv-sel" style="transform:translate(${p[0].toFixed(0)}px,${p[1].toFixed(0)}px)"></div>`;
    }
    box.innerHTML = html;
    const rd = vecRaDec(V.vp.fwd), from = Math.hypot(V.vp.eye[0], V.vp.eye[1], V.vp.eye[2]);
    V.el.querySelector('.uv-read').textContent = `Looking towards RA ${fmtRa(rd.ra)} · Dec ${fmtDec(rd.dec)} · you are ${num(from, from < 10 ? 1 : 0)} Mpc (${num(from * MLY, 0)} million light-years) from home · ${V.cam.mode === 'fly' ? 'fly' : 'orbit'} mode`;
  }

  // camera motion: animation towards a goal, and keyboard flying
  function step() {
    if (!V) return;
    const now = performance.now(), dt = Math.min(0.05, (now - (V.last || now)) / 1000);
    V.last = now;
    let more = false;
    if (V.anim) {
      const a = V.anim, f = Math.min(1, (now - a.t0) / a.ms), e = f < 0.5 ? 2 * f * f : 1 - Math.pow(-2 * f + 2, 2) / 2;
      V.cam.t = [0, 1, 2].map(k => a.t[k] + (a.t2[k] - a.t[k]) * e);
      V.cam.d = Math.exp(Math.log(a.d) + (Math.log(a.d2) - Math.log(a.d)) * e);
      if (f >= 1) V.anim = null; else more = true;
    }
    if (V.cam.mode === 'fly') {
      const k = V.keys, sp = (k.shift ? 4 : 1) * Math.max(8, Math.hypot(...V.cam.eye) * 0.35) * dt;
      const j = V.joy || [0, 0];
      const fw = (k.w || k.arrowup ? 1 : 0) - (k.s || k.arrowdown ? 1 : 0) - j[1], st = (k.d || k.arrowright ? 1 : 0) - (k.a || k.arrowleft ? 1 : 0) + j[0], upd = (k.e ? 1 : 0) - (k.q ? 1 : 0);
      if (fw || st || upd) {
        const f = qrot(V.cam.q, [0, 0, -1]), r = qrot(V.cam.q, [1, 0, 0]), u = qrot(V.cam.q, [0, 1, 0]);
        V.cam.eye = [0, 1, 2].map(i => V.cam.eye[i] + (f[i] * fw + r[i] * st + u[i] * upd) * sp);
        more = true;
      }
    }
    if (more) redraw();
    if (!more && V) V.last = 0;
  }
  function flyTo(p, d) {
    if (V.cam.mode === 'fly') toOrbit();
    V.anim = { t0: performance.now(), ms: 1300, t: V.cam.t.slice(), t2: p.slice(), d: V.cam.d, d2: d };
    redraw();
  }
  function toOrbit() {
    const c = V.cam, f = qrot(c.q, [0, 0, -1]), dd = Math.max(5, Math.min(60, Math.hypot(...c.eye) * 0.3));
    c.t = add3(c.eye, f.map(x => x * dd)); c.d = dd; c.mode = 'orbit';
  }
  function toFly() { const c = V.cam; c.eye = add3(c.t, qrot(c.q, [0, 0, c.d])); c.mode = 'fly'; }
  function setMode(m) {
    if (m === V.cam.mode) return;
    if (m === 'fly') toFly(); else toOrbit();
    V.el.querySelector('.uv-mode').textContent = V.cam.mode === 'fly' ? 'Fly mode (F)' : 'Orbit mode (F)';
    V.el.querySelector('.uv-joy').hidden = V.cam.mode !== 'fly' || !V.touch;
    redraw();
  }

  function pick(x, y, radius) {
    if (!V.vp) return null;
    const w = V.canvas.clientWidth, h = V.canvas.clientHeight, M = V.vp.M, xyz = G.xyz;
    let best = null, bd = radius * radius;
    for (let i = 0; i < G.n; i++) {
      const px = xyz[3 * i], py = xyz[3 * i + 1], pz = xyz[3 * i + 2];
      const ww = M[3] * px + M[7] * py + M[11] * pz + M[15];
      if (ww <= 0.05) continue;
      const sx = ((M[0] * px + M[4] * py + M[8] * pz + M[12]) / ww * 0.5 + 0.5) * w, sy = (1 - ((M[1] * px + M[5] * py + M[9] * pz + M[13]) / ww * 0.5 + 0.5)) * h;
      const dd = (sx - x) * (sx - x) + (sy - y) * (sy - y);
      if (dd < bd || (best !== null && Math.abs(dd - bd) < 4 && ww < best.ww)) { bd = dd; best = { i, ww }; }
    }
    return best ? best.i : null;
  }
  function select(i, fly) {
    V.sel = i;
    V.el.querySelector('.uv-cardbox').innerHTML = galaxyCard(i);
    wireCard();
    if (fly) flyTo([G.xyz[3 * i], G.xyz[3 * i + 1], G.xyz[3 * i + 2]], Math.max(4, Math.min(40, dist(i) * 0.25)));
    redraw();
    S.track('universe/galaxy', true);
  }
  function showLandmark(k, fly) {
    const l = U().landmarks[k];
    V.el.querySelector('.uv-cardbox').innerHTML = landmarkCard(k);
    wireCard();
    if (fly) flyTo(l.xyz, l.kind === 'void' ? 70 : l.kind === 'region' ? 90 : 25);
  }
  function shareUrl(params) {
    const base = ((D().site || {}).url) || (location.origin + location.pathname);
    return base + '?' + params + '#universe';
  }
  function copy(text, btn) {
    const done = () => { if (btn) { const t = btn.textContent; btn.textContent = 'Copied ✓'; setTimeout(() => { btn.textContent = t; }, 1600); } };
    try { navigator.clipboard.writeText(text).then(done, () => prompt_(text)); } catch (_) { prompt_(text); }
  }
  function prompt_(text) {
    const box = V.el.querySelector('.uv-cardbox');
    box.insertAdjacentHTML('beforeend', `<p class="small">Copy this: <input class="uv-copyfield" value="${esc(text)}" readonly></p>`);
    const f = box.querySelector('.uv-copyfield:last-of-type'); if (f) { f.focus(); f.select(); }
  }
  function wireCard() {
    const box = V.el.querySelector('.uv-cardbox');
    box.querySelectorAll('.uv-x').forEach(b => b.addEventListener('click', () => { box.innerHTML = ''; V.sel = null; redraw(); }));
    box.querySelectorAll('.uv-copy').forEach(b => b.addEventListener('click', () => copy(b.dataset.c, b)));
    box.querySelectorAll('.uv-go').forEach(b => b.addEventListener('click', () => select(+b.dataset.i, true)));
    box.querySelectorAll('.uv-golm').forEach(b => b.addEventListener('click', () => showLandmark(+b.dataset.k, true)));
    box.querySelectorAll('.uv-share').forEach(b => b.addEventListener('click', () => copy(shareUrl('sel=' + encodeURIComponent(b.dataset.sel)), b)));
  }
  function camParam() {
    const c = V.cam.mode === 'fly' ? (toOrbit(), V.cam) : V.cam;
    return 'cam=' + [...c.t.map(x => x.toFixed(2)), c.d.toFixed(2), ...c.q.map(x => x.toFixed(5))].join(',');
  }
  function readCam(s) {
    const v = String(s || '').split(',').map(Number);
    if (v.length !== 8 || v.some(x => !isFinite(x))) return null;
    return { mode: 'orbit', t: v.slice(0, 3), d: Math.max(0.5, v[3]), q: qnorm(v.slice(4)), fov: 55 * Math.PI / 180, eye: [0, 0, 0] };
  }

  function wireInput() {
    const cv = V.canvas, ptr = new Map();
    let start = null, pinch = 0, moved = 0;
    cv.addEventListener('pointerdown', e => { ptr.set(e.pointerId, [e.clientX, e.clientY]); try { cv.setPointerCapture(e.pointerId); } catch (_) { /* fine */ }
      V.touch = V.touch || e.pointerType === 'touch'; moved = 0; start = { x: e.clientX, y: e.clientY, t: performance.now(), btn: e.button, shift: e.shiftKey };
      if (ptr.size === 2) { const [a, b] = [...ptr.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); } });
    cv.addEventListener('pointermove', e => {
      const p = ptr.get(e.pointerId); if (!p) return;
      const dx = e.clientX - p[0], dy = e.clientY - p[1];
      ptr.set(e.pointerId, [e.clientX, e.clientY]);
      moved += Math.abs(dx) + Math.abs(dy);
      const c = V.cam, h = cv.clientHeight || 400;
      if (ptr.size === 2) {
        const [a, b] = [...ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]);
        if (pinch) { if (c.mode === 'orbit') c.d = Math.max(0.5, Math.min(1500, c.d * pinch / d)); else { const f = qrot(c.q, [0, 0, -1]); c.eye = add3(c.eye, f.map(x => x * (d - pinch) * 0.3)); } }
        pinch = d;
        if (c.mode === 'orbit') { const r = qrot(c.q, [1, 0, 0]), u = qrot(c.q, [0, 1, 0]), s = c.d / h; c.t = [0, 1, 2].map(i => c.t[i] - (r[i] * dx - u[i] * dy) * s * 0.5); }
      } else if (c.mode === 'orbit' && (start.btn === 2 || start.shift)) {
        const r = qrot(c.q, [1, 0, 0]), u = qrot(c.q, [0, 1, 0]), s = c.d / h; c.t = [0, 1, 2].map(i => c.t[i] - (r[i] * dx - u[i] * dy) * s);
      } else if (c.mode === 'orbit') {
        const ang = Math.hypot(dx, dy) * 0.006; if (ang) c.q = qnorm(qmul(c.q, qaxis([dy, dx, 0], -ang)));
      } else {
        c.q = qnorm(qmul(qmul(c.q, qaxis([0, 1, 0], -dx * 0.004)), qaxis([1, 0, 0], -dy * 0.004)));
      }
      V.anim = null; redraw();
    });
    const up = e => {
      const was = ptr.size;
      ptr.delete(e.pointerId); pinch = 0;
      if (start && was === 1 && moved < 6 && performance.now() - start.t < 500 && start.btn !== 2) {
        const r = cv.getBoundingClientRect(), i = pick(e.clientX - r.left, e.clientY - r.top, e.pointerType === 'touch' ? 22 : 12);
        if (i !== null) select(i, false);
      }
    };
    cv.addEventListener('pointerup', up); cv.addEventListener('pointercancel', up);
    cv.addEventListener('contextmenu', e => e.preventDefault());
    cv.addEventListener('wheel', e => { e.preventDefault(); const c = V.cam;
      if (c.mode === 'orbit') c.d = Math.max(0.5, Math.min(1500, c.d * Math.exp(e.deltaY * 0.0012)));
      else { const f = qrot(c.q, [0, 0, -1]), s = -e.deltaY * 0.002 * Math.max(8, Math.hypot(...c.eye) * 0.3); c.eye = add3(c.eye, f.map(x => x * s)); }
      V.anim = null; redraw(); }, { passive: false });
    // touch joystick for fly mode
    const joy = V.el.querySelector('.uv-joy'), knob = joy.querySelector('span');
    joy.addEventListener('pointerdown', e => { e.stopPropagation(); joy.setPointerCapture(e.pointerId); V.joyId = e.pointerId; });
    joy.addEventListener('pointermove', e => { if (V.joyId !== e.pointerId) return; const r = joy.getBoundingClientRect(), x = (e.clientX - r.left) / r.width * 2 - 1, y = (e.clientY - r.top) / r.height * 2 - 1, n = Math.max(1, Math.hypot(x, y));
      V.joy = [x / n, y / n]; knob.style.transform = `translate(${(x / n * 28).toFixed(0)}px,${(y / n * 28).toFixed(0)}px)`; redraw(); });
    const jend = () => { V.joy = [0, 0]; V.joyId = null; knob.style.transform = ''; };
    joy.addEventListener('pointerup', jend); joy.addEventListener('pointercancel', jend);
    // keyboard: only while this tab is showing and not typing in a box
    V.onKey = e => {
      if (!V || !V.el.isConnected || !V.el.closest('.view.on')) return;
      const typing = /input|textarea|select/i.test((e.target && e.target.tagName) || '');
      const k = e.key.toLowerCase();
      if (e.type === 'keydown' && k === '/' && !typing) { e.preventDefault(); V.el.querySelector('.uv-q').focus(); return; }
      if (typing) return;
      if (e.type === 'keydown' && k === 'f') setMode(V.cam.mode === 'fly' ? 'orbit' : 'fly');
      else if (e.type === 'keydown' && k === 'h') { V.cam = defaultCam(); redraw(); }
      else if (e.type === 'keydown' && k === 'l') { LS.set('sl_uv_labels', !LS.get('sl_uv_labels', true)); redraw(); }
      if (['w', 'a', 's', 'd', 'q', 'e', 'shift', 'arrowup', 'arrowdown', 'arrowleft', 'arrowright'].includes(k)) {
        V.keys[k] = e.type === 'keydown'; if (V.cam.mode === 'fly' && /^arrow/.test(k)) e.preventDefault(); redraw();
      }
    };
    if (!wireInput.done) { window.addEventListener('keydown', e => V && V.onKey && V.onKey(e)); window.addEventListener('keyup', e => V && V.onKey && V.onKey(e)); wireInput.done = true; }
    if (window.ResizeObserver) new ResizeObserver(() => redraw()).observe(cv);
  }

  function wireSearch() {
    const q = V.el.querySelector('.uv-q'), list = V.el.querySelector('.uv-results');
    const show = () => {
      const r = search(q.value);
      list.innerHTML = r.map((x, k) => `<li><button class="uv-res" data-k="${k}"><b>${esc(x.label)}</b>${x.sub ? ` <span class="muted small">${esc(x.sub)}</span>` : ''}</button>${x.kind === 'note' ? `<p class="small">${esc(x.text)}</p>` : ''}</li>`).join('')
        || (q.value.trim() ? '<li class="small muted">Nothing found. Try a name (Whirlpool), M or NGC number (M87, NGC 4993), landmark (Coma) or 2MASS ID.</li>' : '');
      list.querySelectorAll('.uv-res').forEach(b => b.addEventListener('click', () => {
        const x = r[+b.dataset.k];
        if (x.kind === 'galaxy' && !G.ready) return;
        if (x.kind === 'galaxy') select(x.i, true); else if (x.kind === 'landmark') showLandmark(x.k, true);
        if (x.kind !== 'note') { list.innerHTML = ''; q.blur(); }
      }));
      S.track('universe/search', true);
    };
    let tm = null;
    q.addEventListener('input', () => { clearTimeout(tm); tm = setTimeout(show, 150); });
    q.addEventListener('keydown', e => { if (e.key === 'Enter') { const b = list.querySelector('.uv-res'); if (b) b.click(); else show(); } if (e.key === 'Escape') { q.value = ''; list.innerHTML = ''; q.blur(); } });
  }

  async function load() {
    if (G.ready || G.loading) return G.loading;
    const u = U();
    G.loading = (async () => {
      const [buf, txt] = await Promise.all([S.loadBytes(u.bin), S.load(u.txt)]);
      Object.assign(G, parseBin(buf), parseTxt(txt));
      if (G.ids.length !== G.n) throw new Error('the galaxy files do not match; they will be rebuilt');
      G.famousByI = {}; (u.famous || []).forEach(f => { G.famousByI[f.i] = f; });
      G.ready = true;
    })();
    return G.loading;
  }

  function render(el) {
    const u = U();
    if (!u || !u.bin) { el.innerHTML = '<div class="card"><h3>🌌 3D universe</h3><p class="muted">The galaxy map is built on the next monthly update.</p></div>'; return; }
    if (V && V.el === el && V.glx) { redraw(); return; }
    const c = u.env_counts || {};
    el.innerHTML = `<div class="card uv-top"><div class="row"><h3>🌌 The local universe in 3D</h3><span class="spacer"></span><span class="small muted">${Number(u.n).toLocaleString()} real galaxies · out to ${num(u.max_dist * MLY / 1000, 1)} billion light-years</span></div>
        <p class="small">Every dot is a real galaxy from the 2MASS Redshift Survey, placed by its distance. Drag to turn, scroll or pinch to zoom, tap a galaxy for its identity card. Press <b>/</b> to search, <b>F</b> to fly (W A S D, Q E), <b>H</b> for home, <b>L</b> for labels.</p>
        <div class="row uv-bar"><div class="uv-search"><input type="search" class="uv-q" placeholder="Search: Whirlpool, M87, NGC 4993, Coma, Boötes Void, 2MASS ID" aria-label="Search galaxies, clusters and voids"><ul class="uv-results"></ul></div>
          <button class="b uv-mode">Orbit mode (F)</button>
          <select class="uv-color" aria-label="Colour the galaxies by"><option value="env">Colour: cosmic web</option><option value="dist">Colour: distance</option><option value="type">Colour: galaxy type</option></select>
          <button class="b uv-home-btn">Home (H)</button><button class="b uv-view">Share this view</button></div></div>
      <div class="uv-wrap"><canvas class="uv-canvas" aria-label="3D map of about 43,500 galaxies. Use search to find one."></canvas><div class="uv-labels"></div>
        <div class="uv-read small mono"></div><div class="uv-joy" hidden><span></span></div><div class="uv-cardbox"></div>
        <div class="uv-loading small">Loading ${Number(u.n).toLocaleString()} galaxies…</div></div>
      <div class="card small uv-legend"><b>Colours (cosmic web):</b> ${ENV.map(e => `<span class="uv-dot" style="background:${e[3]}"></span>${e[0]} (${Number(c[e[0]] || 0).toLocaleString()})`).join(' · ')}.
        <span class="muted">Rings: 100, 200 and 300 Mpc (about 330, 650 and 980 million light-years) in the supergalactic plane.</span>
        <details><summary>How the galaxies are placed, and what is missing</summary><p>Positions use each galaxy's redshift measured against the cosmic microwave background, with members of a group or cluster placed at their group's speed (from the Tully 2015 group catalogue). This removes the "Fingers of God", clusters stretched along the line of sight by their galaxies' orbits. Distance = speed ÷ ${num(u.h0, 0)} km/s per Mpc.</p>
          <p>Not done yet: removing the extra pull of the biggest clusters, so Virgo sits near ${num(((u.landmarks || []).find(l => l.name === 'Virgo Cluster') || {}).dist || 21, 0)} Mpc instead of its measured 16.5 Mpc. The empty band is the plane of our own Milky Way, whose dust and stars hide the galaxies behind it. Galaxies that move towards us, like Andromeda, cannot be placed by redshift and are left out. Cosmic-web classes come from the tidal field of the smoothed galaxy density (T-web), and are less certain far away.</p>
          <p class="muted">Data: 2MASS Redshift Survey (Huchra et al. 2012); 2MASS group catalogue (Tully 2015), both through VizieR. Inspired by Know the Universe (knowtheuniverse.com).</p></details></div>`;
    V = { el, canvas: el.querySelector('.uv-canvas'), cam: defaultCam(), keys: {}, sel: null, touch: !!(window.matchMedia && window.matchMedia('(pointer: coarse)').matches) };
    el.querySelector('.uv-color').value = LS.get('sl_uv_color', 'env');
    wireSearch();
    load().then(() => {
      el.querySelector('.uv-loading').remove();
      try { V.glx = initGL(V.canvas); } catch (_) { V.glx = null; }
      if (!V.glx) { el.querySelector('.uv-wrap').insertAdjacentHTML('afterbegin', '<p class="card small">This browser cannot draw 3D (WebGL is off or missing). Search and the galaxy cards still work.</p>'); }
      else wireInput();
      const qs = new URLSearchParams(location.search), sel = (qs.get('sel') || '').replace(/ /g, '+'), cam = readCam(qs.get('cam'));
      if (cam) V.cam = cam;
      if (sel) { const i = G.ids.indexOf(sel.replace(/^2MASX ?J/i, '')); if (i >= 0) select(i, !cam); }
      el.querySelector('.uv-mode').addEventListener('click', () => setMode(V.cam.mode === 'fly' ? 'orbit' : 'fly'));
      el.querySelector('.uv-home-btn').addEventListener('click', () => { V.cam = defaultCam(); redraw(); });
      el.querySelector('.uv-view').addEventListener('click', e => copy(shareUrl(camParam()), e.currentTarget));
      el.querySelector('.uv-color').addEventListener('change', e => { LS.set('sl_uv_color', e.target.value); if (V.glx) { const gl = V.glx.gl; gl.bindBuffer(gl.ARRAY_BUFFER, V.glx.col); gl.bufferData(gl.ARRAY_BUFFER, colorsFor(e.target.value), gl.STATIC_DRAW); } redraw(); });
      el.querySelector('.uv-labels').addEventListener('click', e => { const b = e.target.closest('.uv-lm'); if (b) showLandmark(+b.dataset.k, true); });
      redraw();
    }).catch(e => { const l = el.querySelector('.uv-loading'); if (l) l.textContent = 'The galaxy files could not be loaded: ' + e.message; });
    S.track('universe/open', true);
  }

  S.addTab('universe', '3D universe', render);
  window.SpaceLabUniverse = { parseBin, parseTxt, idRaDec, vecRaDec, search, galaxyCard: i => galaxyCard(i), load, data: G, qfromBasis, qrot, readCam };
})();
