// Space Lab extras: games, creative tools and personal features. Runs on the public page AND inside Meridian
// (both provide window.SpaceLab, see EXTRAS.md). Everything personal stays in this browser (localStorage).
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const PUBLIC_URL = 'https://techamatuer.github.io/Space-Lab/';
  const ISSUES = 'https://github.com/TechAmatuer/Space-Lab/issues/new';
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* storage blocked: features still work for this visit */ } },
  };
  const D = () => S.data || {};
  const site = () => ((D().site || {}).url) || PUBLIC_URL;
  const cands = () => ((D().tess || {}).cands) || [];
  const periodic = () => cands().filter(c => c.kind === 'periodic' && c.period);
  const candBy = tic => cands().find(c => String(c.tic) === String(tic));
  const mk = (tag, cls, html) => { const e = document.createElement(tag); if (cls) e.className = cls; if (html !== undefined) e.innerHTML = html; return e; };
  const btn = (cls, label, title) => { const b = mk('button', 'b ' + cls, label); b.type = 'button'; if (title) b.title = title; return b; };
  const rand = seed => () => { seed = (seed * 1664525 + 1013904223) % 4294967296; return seed / 4294967296; };
  const gauss = r => Math.sqrt(-2 * Math.log(r() || 1e-9)) * Math.cos(2 * Math.PI * r());
  const cardsIn = el => [...el.querySelectorAll('.card[data-tic]')];
  const download = (canvas, name) => { const a = document.createElement('a'); a.download = name; a.href = canvas.toDataURL('image/png'); a.click(); };
  const reviewLink = (tic, verdict) => `${ISSUES}?template=candidate-review.yml&title=${encodeURIComponent('TIC ' + tic + ' review')}&tic=${encodeURIComponent(tic)}&verdict=${encodeURIComponent(verdict)}`;

  // ------------------------------------------------------------------ star colour from temperature (approximate blackbody)
  function starRGB(teff) {
    const t = Math.max(1000, Math.min(40000, teff || 5800)) / 100;
    let r, g, b;
    r = t <= 66 ? 255 : 329.7 * Math.pow(t - 60, -0.1332);
    g = t <= 66 ? 99.47 * Math.log(t) - 161.12 : 288.12 * Math.pow(t - 60, -0.0755);
    b = t >= 66 ? 255 : t <= 19 ? 0 : 138.52 * Math.log(t - 10) - 305.04;
    const c = v => Math.round(Math.max(0, Math.min(255, v)));
    return [c(r), c(g), c(b)];
  }
  const spectral = t => !t ? '?' : t >= 30000 ? 'O' : t >= 10000 ? 'B' : t >= 7500 ? 'A' : t >= 6000 ? 'F' : t >= 5200 ? 'G' : t >= 3700 ? 'K' : 'M';

  // ------------------------------------------------------------------ visits, streaks, badges
  const today = () => new Date().toISOString().slice(0, 10);
  function recordVisit() {
    const days = LS.get('sl_days', []);
    if (days[days.length - 1] !== today()) { days.push(today()); LS.set('sl_days', days.slice(-400)); }
  }
  function streak() {
    const days = LS.get('sl_days', []);
    let n = 0, d = new Date(today());
    for (let i = days.length - 1; i >= 0; i--) {
      if (days[i] === d.toISOString().slice(0, 10)) { n++; d.setDate(d.getDate() - 1); } else break;
    }
    return n;
  }
  const BADGES = [
    ['first_review', '🔍 First review', s => s.reviews >= 1], ['ten_reviews', '🏅 10 reviews', s => s.reviews >= 10],
    ['asteroid_eye', '☄️ Asteroid eye', s => s.ast >= 1], ['period_finder', '⏱ Period finder', s => s.periodWins >= 1],
    ['sharp_eyes', '👁 Sharp eyes (blink)', s => s.blinkWins >= 3], ['dip_spotter', '📉 Dip spotter', s => s.dipBest >= 4],
    ['detective', '🕵️ Case closed', s => s.mystery >= 1], ['collector', '⭐ Collector (3 favorites)', s => s.favs >= 3],
    ['observer', '🔭 Observer (log entry)', s => s.log >= 1], ['streak3', '🔥 3-day streak', s => s.streak >= 3],
    ['artist', '🎨 Artist', s => s.art >= 1], ['daily3', '📅 3 daily votes in a row', s => s.daily >= 3]];
  function stats() {
    return { reviews: LS.get('sl_reviews', []).length, ast: LS.get('sl_ast_reviews', []).length, periodWins: LS.get('sl_period_wins', 0),
             blinkWins: LS.get('sl_blink_wins', 0), dipBest: LS.get('sl_dip_best', 0), mystery: LS.get('sl_mystery', 0),
             favs: LS.get('sl_favs', []).length, log: LS.get('sl_log', []).length, streak: streak(), art: LS.get('sl_art', 0),
             daily: window.SpaceLabHome ? window.SpaceLabHome.streak() : 0 };
  }
  function badges() {
    const b = LS.get('sl_badges', {}), s = stats();
    let changed = false;
    for (const [k, , test] of BADGES) if (!b[k] && test(s)) { b[k] = Date.now(); changed = true; }
    if (changed) LS.set('sl_badges', b);
    return b;
  }
  const badgeHTML = () => {
    const b = badges();
    return `<div class="sl-badges">${BADGES.map(([k, label]) => `<span class="sl-badge${b[k] ? ' earned' : ''}" title="${b[k] ? 'earned ' + new Date(b[k]).toLocaleDateString() : 'not yet'}">${esc(label)}</span>`).join('')}</div>`;
  };
  const bump = (k, by = 1) => { LS.set(k, LS.get(k, 0) + by); badges(); };

  // ------------------------------------------------------------------ TESS cards: size, sound, favourite, postcard, share, sky, colour, consensus
  function sizeSVG(rp) {
    const bodies = [['earth', 1, '#4a90d9', 'Earth'], ['candidate', rp, '#f0a040', 'candidate'], ['neptune', 3.9, '#7fb3d5', 'Neptune'], ['jupiter', 11.2, '#c9a27e', 'Jupiter']];
    const k = 48 / Math.max(rp, 11.2);
    let x = 4;
    const parts = bodies.map(([n, r, col, label]) => {
      const rr = Math.max(r * k, 0.6);
      const cx = x + rr; x += 2 * rr + 18;
      return `<circle data-body="${n}" cx="${cx.toFixed(2)}" cy="55" r="${rr.toFixed(3)}" fill="${n === 'candidate' ? col : 'none'}" stroke="${col}" stroke-width="1.5"/>` +
             `<text x="${cx.toFixed(1)}" y="${(55 + Math.max(rr, 6) + 14).toFixed(1)}" text-anchor="middle" font-size="10" fill="currentColor">${label}</text>`;
    });
    return `<div class="sl-size"><svg class="sl-size" viewBox="0 0 ${(x - 14).toFixed(1)} 128" height="110" role="img" aria-label="size compared with Earth, Neptune and Jupiter">${parts.join('')}</svg>` +
           `<div class="small muted">About ${num(rp, 1)}× Earth's width if it is a planet (to scale with Earth, Neptune and Jupiter).</div></div>`;
  }
  let sound = null;
  function stopSound() { if (!sound) return; sound.btn.classList.remove('playing'); sound.btn.textContent = sound.label; try { sound.ctx.close(); } catch (_) {} clearTimeout(sound.timer); sound = null; }
  function playTransit(c, b) {
    const was = sound && sound.btn === b; stopSound(); if (was) return;
    const AC = window.AudioContext || window.webkitAudioContext; if (!AC) return;
    const ctx = new AC(), osc = ctx.createOscillator(), g = ctx.createGain();
    osc.frequency.value = 330; osc.connect(g); g.connect(ctx.destination);
    const orbit = 1, dip = Math.min(0.8, (c.depth || 0.001) * 50), w = Math.max(0.03, Math.min(0.4, c.duration / c.period)) * orbit;
    for (let t = 0; t < 8; t += orbit) {
      g.gain.setValueAtTime(0.25, t + 0.001); g.gain.setValueAtTime(0.25 * (1 - dip), t + orbit / 2 - w / 2); g.gain.setValueAtTime(0.25, t + orbit / 2 + w / 2);
    }
    g.gain.setValueAtTime(0, 8);
    osc.start();
    sound = { ctx, btn: b, label: b.textContent, timer: setTimeout(stopSound, 8200) };
    b.classList.add('playing'); b.textContent = '⏹ Stop';
  }
  // "Would I see this from my backyard?" Tmag is close to the visual magnitude for these cool stars (good enough here).
  function backyard(c) {
    const m = c.star && c.star.tmag, ppt = (c.depth || 0) * 1000;
    if (!m) return '';
    const eye = m < 6 ? 'can be seen with the naked eye under dark skies' : m < 9 ? 'shows up in binoculars' : m < 12.5 ? 'shows up in a small backyard telescope' : m < 15 ? 'needs a large amateur telescope or a camera' : 'is too faint for most amateur telescopes';
    const dip = ppt >= 5 && m < 13 ? 'the dip is deep enough that a careful amateur with a camera could measure it' : ppt >= 2 && m < 12 ? 'measuring the dip would take a good camera and very steady conditions' : 'the dip itself is too shallow to measure from the ground with amateur gear: it needs TESS or a big telescope';
    return `<div class="small sl-backyard">🔭 <b>From your backyard:</b> the star (magnitude ${num(m, 1)}) ${eye}; ${dip} (${num(ppt, 1)} parts per thousand).</div>`;
  }
  function shareText(c) {
    return `🔭 Planet candidate TIC ${c.tic}: ${c.period ? 'a dip every ' + num(c.period, 2) + ' days, ' : ''}about ${num(c.rp, 1)}× Earth's size if it's real. ` +
           `Still a candidate: it needs checking. Help review it: ${site()}c/${c.tic}.html`;
  }
  function consensusHTML(tic) {
    const v = ((D().community || {}).tess || {})[String(tic)];
    if (!v) return '';
    const parts = Object.entries(v).sort((a, b) => b[1] - a[1]).map(([k, n]) => `${n}× ${esc(k)}`);
    return `<div class="sl-consensus note"><b>Community reviews</b> (from GitHub): ${parts.join(' · ')}</div>`;
  }
  function decorateTess(el) {
    const favs = LS.get('sl_favs', []).map(String), seen = LS.get('sl_seen_at', 0);
    for (const card of cardsIn(el)) {
      if (card.querySelector('.sl-tools')) continue;
      const c = candBy(card.dataset.tic);
      if (!c) continue;
      const box = mk('div', 'sl-tools');
      if (c.created && seen && c.created * 1000 > seen) box.appendChild(mk('span', 'sl-newtag chip warn', 'new since your last visit'));
      const myReview = LS.get('sl_reviews', []).filter(r => String(r.tic) === String(c.tic)).pop();
      if (myReview) box.appendChild(mk('span', 'sl-reviewed chip ok', `✓ you reviewed this on ${esc(new Date(myReview.t).toLocaleDateString())}: ${esc(myReview.answer)}`));
      if (c.star && c.star.teff) {
        const [r, g, b] = starRGB(c.star.teff);
        box.appendChild(mk('span', 'sl-starcolor', `<i style="background:rgb(${r},${g},${b})"></i>${spectral(c.star.teff)}-type star, ${Math.round(c.star.teff)} K${c.star.dist_pc ? ', ' + Math.round(c.star.dist_pc * 3.26) + ' light-years away' : ''}`));
      }
      if (c.rp) box.insertAdjacentHTML('beforeend', sizeSVG(c.rp));
      box.insertAdjacentHTML('beforeend', backyard(c));
      box.insertAdjacentHTML('beforeend', consensusHTML(c.tic));
      const row = mk('div', 'row sl-row');
      const fav = btn('sl-fav' + (favs.includes(String(c.tic)) ? ' on' : ''), favs.includes(String(c.tic)) ? '★ Saved' : '☆ Save', 'Save to My lab');
      fav.addEventListener('click', () => {
        let l = LS.get('sl_favs', []).map(String); const k = String(c.tic);
        l = l.includes(k) ? l.filter(x => x !== k) : l.concat(k); LS.set('sl_favs', l); badges();
        const sn = LS.get('sl_fav_snap', {}); sn[k] = favSig(c); LS.set('sl_fav_snap', sn);
        fav.classList.toggle('on', l.includes(k)); fav.textContent = l.includes(k) ? '★ Saved' : '☆ Save';
      });
      row.appendChild(fav);
      if (c.kind === 'periodic' && c.period) {
        const l = btn('sl-listen', '🔊 Listen', 'Hear the dips (exaggerated: one orbit = one second)');
        l.addEventListener('click', () => playTransit(c, l)); row.appendChild(l);
        const p = btn('sl-postcard', '🖼 Postcard', 'Download a postcard image of this candidate');
        p.addEventListener('click', () => { postcard(c); bump('sl_art'); }); row.appendChild(p);
      }
      const sh = btn('sl-share', '📣 Share text');
      sh.addEventListener('click', () => {
        let out = card.querySelector('.sl-sharebox');
        if (out) { out.remove(); return; }
        out = mk('div', 'sl-sharebox', `<textarea readonly rows="3">${esc(shareText(c))}</textarea><div class="small muted">Copy and paste into Discord, Reddit or anywhere. Embed this card on a website:</div><textarea readonly rows="2">${esc(`<iframe src="${site()}?card=${c.tic}" width="380" height="560" style="border:0" title="Space Lab candidate TIC ${c.tic}"></iframe>`)}</textarea>`);
        box.appendChild(out); out.querySelector('textarea').select();
        try { navigator.clipboard && navigator.clipboard.writeText(shareText(c)); } catch (_) {}
      });
      row.appendChild(sh);
      const sky = mk('a', 'sl-sky', '🌌 See it in the sky ↗');
      sky.href = `https://sky.esa.int/esasky/?target=${encodeURIComponent('TIC ' + c.tic)}&hips=DSS2+color&fov=0.25`;
      sky.target = '_blank'; sky.rel = 'noopener';
      row.appendChild(sky);
      box.appendChild(row);
      const anchor = card.querySelector('img.plot') || card.lastElementChild;
      anchor.parentNode.insertBefore(box, anchor);
    }
  }

  // ------------------------------------------------------------------ canvas art: postcard, generative art, wallpaper
  function foldedPoints(c, n = 400, seed = c.tic) {
    const r = rand(Number(seed) % 100000 + 7), pts = [];
    const w = Math.max(0.02, Math.min(0.25, c.duration / c.period)), dep = Math.max(c.depth, 0.0005);
    for (let i = 0; i < n; i++) {
      const ph = i / n - 0.5, inT = Math.abs(ph) < w / 2;
      const shape = inT ? 1 - Math.pow(Math.abs(ph) / (w / 2), 6) : 0;
      pts.push([ph, 1 - dep * shape + gauss(r) * dep * 0.12]);
    }
    return pts;
  }
  function drawRing(ctx, c, cx, cy, R, rgb) {
    const pts = foldedPoints(c, 720), dep = Math.max(c.depth, 0.0005);
    ctx.lineWidth = 2; ctx.strokeStyle = `rgb(${rgb.join(',')})`; ctx.beginPath();
    pts.forEach(([ph, f], i) => {
      const a = (ph + 0.5) * 2 * Math.PI - Math.PI / 2, rr = R * (1 - (1 - f) / dep * 0.25);
      const x = cx + rr * Math.cos(a), y = cy + rr * Math.sin(a);
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.closePath(); ctx.stroke();
  }
  function postcard(c) {
    const cv = document.createElement('canvas'); cv.width = cv.height = 1080;
    const x = cv.getContext('2d'); if (!x) return;
    const rgb = starRGB(c.star && c.star.teff);
    x.fillStyle = '#05080d'; x.fillRect(0, 0, 1080, 1080);
    const g = x.createRadialGradient(540, 470, 10, 540, 470, 260); g.addColorStop(0, `rgba(${rgb.join(',')},0.9)`); g.addColorStop(1, 'rgba(0,0,0,0)');
    x.fillStyle = g; x.fillRect(0, 0, 1080, 1080);
    drawRing(x, c, 540, 470, 330, rgb);
    x.fillStyle = '#e8eef4'; x.textAlign = 'center';
    x.font = 'bold 54px sans-serif'; x.fillText('Planet candidate TIC ' + c.tic, 540, 900);
    x.font = '34px sans-serif';
    x.fillText(`orbit every ${num(c.period, 2)} days · about ${num(c.rp, 1)}× Earth if real`, 540, 960);
    x.font = '26px sans-serif'; x.fillStyle = '#8a99a9'; x.fillText('Space Lab · citizen science with NASA TESS data · still a candidate', 540, 1030);
    download(cv, `tic-${c.tic}-postcard.png`);
  }
  function artPiece(c, w, h, name) {
    const cv = document.createElement('canvas'); cv.width = w; cv.height = h;
    const x = cv.getContext('2d'); if (!x) return;
    const r = rand(Number(c.tic) % 99991), rgb = starRGB(c.star && c.star.teff);
    x.fillStyle = '#03050a'; x.fillRect(0, 0, w, h);
    for (let i = 0; i < 400; i++) { x.fillStyle = `rgba(255,255,255,${r() * 0.6})`; x.fillRect(r() * w, r() * h, 1.5, 1.5); }
    const rings = 3 + Math.round(Math.min(9, 30 / Math.max(c.period, 1)));
    for (let k = 0; k < rings; k++) {
      x.globalAlpha = 0.25 + 0.7 * (1 - k / rings);
      drawRing(x, c, w / 2, h / 2, Math.min(w, h) * (0.12 + 0.36 * k / rings), rgb.map((v, i) => Math.round(v * (0.6 + 0.4 * ((k + i) % 3) / 2))));
    }
    x.globalAlpha = 1; x.fillStyle = '#8a99a9'; x.font = `${Math.round(w / 40)}px sans-serif`; x.textAlign = 'center';
    x.fillText(`TIC ${c.tic} · ${num(c.period, 2)}-day dips · Space Lab`, w / 2, h - w / 25);
    download(cv, name); bump('sl_art');
  }

  // ------------------------------------------------------------------ Play tab
  // must match the options in .github/ISSUE_TEMPLATE/candidate-review.yml so GitHub can prefill the form
  const ANS = ['Planet-like', 'Eclipsing binary (two stars)', 'Comes from a nearby star', 'Noise / instrument glitch', 'Not sure'];
  const AANS = ['A moving dot where predicted (real)', 'Nothing there', 'Star, galaxy or image defect', 'Not sure'];
  function progressBar() {
    const all = cands(), mine = new Set(LS.get('sl_reviews', []).map(r => String(r.tic))), n = all.filter(c => mine.has(String(c.tic))).length;
    return all.length ? `<div class="sl-progress"><div class="small">You've reviewed <b>${n}</b> of <b>${all.length}</b> candidates</div><div class="sl-bar"><i style="width:${Math.round(n / all.length * 100)}%"></i></div></div>` : '';
  }
  function allDone() {          // the "you've seen them all" moment: what the candidates told us
    const all = cands(), rv = LS.get('sl_reviews', []), byTic = {};
    rv.forEach(r => { byTic[String(r.tic)] = r.answer; });
    const ai = {}, agree = { yes: 0, n: 0 };
    all.forEach(c => {
      const v = c.ai ? c.ai.verdict : 'no AI verdict yet'; ai[v] = (ai[v] || 0) + 1;
      const mine = byTic[String(c.tic)];
      if (c.ai && mine) { agree.n++; if ((/planet/i.test(mine) && /planet/i.test(v)) || (/binary|two stars/i.test(mine) && /binary/i.test(v)) || (/noise/i.test(mine) && /noise/i.test(v))) agree.yes++; }
    });
    const flagged = all.filter(c => (c.flags || []).length).length;
    return `<h3>🎉 You've seen them all</h3><p>You've reviewed every one of the <b>${all.length}</b> candidates the planet search has found so far. Thank you: this is exactly how volunteers help find planets.</p>
      <table class="compact sl-summary"><tr><th>What the local AI thought</th><th>candidates</th></tr>${Object.entries(ai).sort((a, b) => b[1] - a[1]).map(([k, n]) => `<tr><td>${esc(k)}</td><td class="mono">${n}</td></tr>`).join('')}</table>
      <p class="small">${flagged} of them carry a warning from the automatic checks. ${agree.n ? `You agreed with the AI on <b>${agree.yes}</b> of ${agree.n}.` : ''} New candidates appear as the search runs: come back for more.</p>`;
  }
  function reviewSection(rev) {
    const done = LS.get('sl_reviews', []).map(r => String(r.tic));
    const c = cands().find(x => !done.includes(String(x.tic)));
    if (!c) { rev.innerHTML = cands().length ? allDone() + progressBar() + badgeHTML() : `<h3>Planet or not?</h3><p>No candidates to review yet. They appear as the planet search runs.</p>${badgeHTML()}`; return; }
    const intro = LS.get('sl_rev_intro', 0) ? '' : `<div class="note small">How to judge a plot: <b>1.</b> top left, a dip under every red line? <b>2.</b> bottom left, a flat-bottomed U (planet) or a sharp V (often two stars)? <b>3.</b> bottom right, odd and even dips equally deep?</div>`;
    LS.set('sl_rev_intro', 1);
    rev.innerHTML = `<h3>Planet or not?</h3>${progressBar()}${intro}<div class="sl-cand" data-tic="${esc(c.tic)}"><div class="small muted">TIC ${esc(c.tic)} · ${c.period ? 'dips every ' + num(c.period, 3) + ' days' : 'one lone dip'} · ${Math.round(c.depth * 1e6)} ppm deep</div>
      <img class="plot" src="${esc(c.plot || '')}" alt="light-curve plots for TIC ${esc(c.tic)}"></div><div class="row">${ANS.map(a => `<button type="button" class="b sl-answer">${esc(a)}</button>`).join('')}</div>`;
    rev.querySelectorAll('.sl-answer').forEach(b => b.addEventListener('click', () => {
      const r = LS.get('sl_reviews', []); r.push({ tic: c.tic, answer: b.textContent, t: Date.now() }); LS.set('sl_reviews', r);
      const said = [c.ai ? `the local AI said <b>${esc(c.ai.verdict)}</b>` : 'the AI has not looked yet',
                    (c.flags || []).length ? 'automatic checks flagged: ' + c.flags.map(esc).join(', ') : 'no automatic check raised a flag',
                    c.fpp ? 'statistical test: ' + esc(c.fpp.verdict) : ''].filter(Boolean).join('; ');
      rev.innerHTML = `<h3>Planet or not?</h3><div class="sl-cand" data-tic="${esc(c.tic)}"><p>You said <b>${esc(b.textContent)}</b>. For comparison, ${said}.</p></div>
        <p><a class="b sl-send" target="_blank" rel="noopener" href="${esc(reviewLink(c.tic, b.textContent))}">Send my review to the project</a> <span class="small muted">(opens GitHub; needs a free GitHub account)</span></p>
        <button type="button" class="b primary sl-next">Next candidate</button>${badgeHTML()}`;
      rev.querySelector('.sl-next').addEventListener('click', () => reviewSection(rev));
      rev.querySelector('.sl-send').addEventListener('click', () => bump('sl_sent'));
    }));
  }
  function asteroidSection(ast) {
    const list = ((D().asteroids || {}).cands) || [];
    const done = LS.get('sl_ast_reviews', []).map(String);
    const a = list.find(x => !done.includes(String(x.id))) || list[0];
    if (!a) { ast.innerHTML = '<h3>Is the asteroid there?</h3><p class="muted">No sightings waiting right now.</p>'; return; }
    ast.dataset.id = a.id;
    ast.innerHTML = `<h3>Is the asteroid there?</h3><p class="small">${esc(a.des)} in ${esc(a.archive)} images from ${esc(a.night)}. The asteroid should be a dot that moves between frames, near the centre.</p>
      <img src="${esc(a.gif || a.png || '')}" alt="survey frames of ${esc(a.des)}" class="plot"><div class="row">${AANS.map(x => `<button type="button" class="b sl-answer">${esc(x)}</button>`).join('')}</div>`;
    ast.querySelectorAll('.sl-answer').forEach(b => b.addEventListener('click', () => {
      LS.set('sl_ast_reviews', LS.get('sl_ast_reviews', []).concat(String(a.id))); badges();
      const u = `${ISSUES}?template=asteroid-review.yml&title=${encodeURIComponent('Asteroid sighting review: ' + a.des)}&candidate=${encodeURIComponent(`${a.des}, ${a.night}, ${a.archive} (#${a.id})`)}&verdict=${encodeURIComponent(b.textContent)}`;
      if (!ast.querySelector('.sl-send')) ast.insertAdjacentHTML('beforeend', `<p><a class="b sl-send" target="_blank" rel="noopener" href="${esc(u)}">Send my review</a> <span class="small muted">(GitHub account needed)</span></p>`);
    }));
  }
  function practiceGame(box) {
    const P = 1 + Math.random() * 7, r = rand(Math.floor(Math.random() * 1e6)), noise = 0.003 + Math.random() * 0.007, dep = 0.005 + Math.random() * 0.015, w = 0.04;
    const t = [], y = [];
    for (let i = 0; i < 27 * 48; i++) { const tt = i / 48, ph = ((tt / P) % 1 + 1) % 1; t.push(tt); y.push(1 + gauss(r) * noise - ((ph < w / 2 || ph > 1 - w / 2) ? dep : 0)); }
    box.innerHTML = `<div class="sl-practice" data-true-period="${P}"><h3>Find the period (practice with made-up data)</h3>
      <p class="small">A fake star with a hidden planet. Slide until all the dips stack up in the folded view.</p>
      <canvas width="640" height="220"></canvas><input type="range" min="0.5" max="10" step="any" value="0.5" aria-label="period in days">
      <div class="row"><span class="sl-pval mono">0.50 days</span><span class="spacer"></span><button type="button" class="b sl-new">New star</button></div><p class="sl-status"></p></div>`;
    const g = box.querySelector('.sl-practice'), cv = g.querySelector('canvas'), s = g.querySelector('input'), st = g.querySelector('.sl-status');
    let tries = 0;
    const draw = p => {
      const x = cv.getContext && cv.getContext('2d'); if (!x) return;
      x.clearRect(0, 0, cv.width, cv.height); x.fillStyle = getComputedStyle(document.body).color || '#ccc';
      const lo = 1 - dep * 1.6 - noise * 3, hi = 1 + noise * 3;
      for (let i = 0; i < t.length; i++) {
        const ph = ((t[i] / p) % 1 + 1.5) % 1;
        x.fillRect(ph * cv.width, (hi - y[i]) / (hi - lo) * cv.height, 2, 2);
      }
    };
    s.addEventListener('input', () => {
      const p = +s.value; tries++; g.querySelector('.sl-pval').textContent = p.toFixed(2) + ' days'; draw(p);
      if (Math.abs(p - P) / P < 0.01) { st.textContent = `You found it! Period = ${P.toFixed(3)} days (${tries} moves).`; if (!g.dataset.won) { g.dataset.won = 1; bump('sl_period_wins'); } }
      else st.textContent = '';
    });
    g.querySelector('.sl-new').addEventListener('click', () => practiceGame(box));
    draw(0.5);
  }
  function dipGame(box) {
    let round = 0, score = 0, cur = null;
    const next = () => {
      if (round === 5) {
        const best = Math.max(LS.get('sl_dip_best', 0), score); LS.set('sl_dip_best', best); badges();
        box.innerHTML = `<h3>Spot the dip</h3><p>Score: <b>${score}/5</b> (your best: ${best}). <button type="button" class="b sl-again">Play again</button></p>`;
        box.querySelector('.sl-again').addEventListener('click', () => dipGame(box)); return;
      }
      round++;
      const r = rand(Math.floor(Math.random() * 1e6)), has = Math.random() < 0.6, at = 0.15 + Math.random() * 0.7, n = 300, noise = 0.004, dep = has ? 0.006 + Math.random() * 0.006 : 0;
      const y = []; for (let i = 0; i < n; i++) y.push(1 + gauss(r) * noise - (Math.abs(i / n - at) < 0.02 ? dep : 0));
      cur = { has, at };
      box.innerHTML = `<h3>Spot the dip (practice, made-up data) · round ${round}/5</h3><p class="small">Click where the star dims, or press "No dip".</p>
        <canvas class="sl-dipcv" width="640" height="160" style="cursor:crosshair"></canvas><div class="row"><button type="button" class="b sl-nodip">No dip</button><span class="sl-dipmsg small"></span></div>`;
      const cv = box.querySelector('canvas'), x = cv.getContext && cv.getContext('2d');
      if (x) { x.fillStyle = getComputedStyle(document.body).color || '#ccc'; y.forEach((v, i) => x.fillRect(i / n * cv.width, (1 + 4 * noise - v) / (8 * noise + 0.012) * cv.height, 2, 2)); }
      const answer = (hit, clickAt) => {
        const ok = hit === null ? !cur.has : cur.has && Math.abs(clickAt - cur.at) < 0.05;
        if (ok) score++;
        box.querySelector('.sl-dipmsg').textContent = ok ? '✓ Right!' : cur.has ? `✗ The dip was at ${Math.round(cur.at * 100)}% across.` : '✗ There was no dip this time.';
        setTimeout(next, 900);
      };
      cv.addEventListener('click', e => { const rc = cv.getBoundingClientRect(); answer(true, (e.clientX - rc.left) / (rc.width || 1)); });
      box.querySelector('.sl-nodip').addEventListener('click', () => answer(null));
    };
    next();
  }
  function depthGame(box) {
    const DEPTHS = [0.001, 0.003, 0.01, 0.03], dep = DEPTHS[Math.floor(Math.random() * 4)], r = rand(Math.floor(Math.random() * 1e6)), n = 300, noise = dep * 0.15;
    const y = []; for (let i = 0; i < n; i++) y.push(1 + gauss(r) * noise - (Math.abs(i / n - 0.5) < 0.06 ? dep : 0));
    box.innerHTML = `<h3>How deep is the dip? (practice)</h3><p class="small">Astronomers turn the depth of a dip into the planet's size: a 1% dip on a Sun-like star means a Jupiter, 0.01% an Earth. Read the scale on the left.</p>
      <canvas width="640" height="180"></canvas><div class="row">${DEPTHS.map(d => `<button type="button" class="b sl-depth" data-d="${d}">${d * 100}%</button>`).join('')}<span class="sl-depthmsg small"></span></div>`;
    const cv = box.querySelector('canvas'), x = cv.getContext && cv.getContext('2d');
    if (x) {
      const lo = 1 - dep * 1.4, hi = 1 + dep * 0.4, Y = v => (hi - v) / (hi - lo) * (cv.height - 20) + 10;
      x.fillStyle = getComputedStyle(document.body).color || '#ccc'; x.font = '12px sans-serif';
      y.forEach((v, i) => x.fillRect(50 + i / n * (cv.width - 60), Y(v), 2, 2));
      [1, 1 - dep / 2, 1 - dep].forEach(v => x.fillText(((v - 1) * 100).toFixed(2) + '%', 2, Y(v) + 4));
    }
    box.querySelectorAll('.sl-depth').forEach(b => b.addEventListener('click', () => {
      const ok = +b.dataset.d === dep;
      box.querySelector('.sl-depthmsg').textContent = ok ? `✓ Right: ${dep * 100}%. Around a Sun-like star that's a planet about ${num(Math.sqrt(dep) * 109, 1)}× Earth's width.` : `✗ It was ${dep * 100}%. Look at the bottom of the dip against the scale.`;
      if (ok) bump('sl_depth_wins');
      setTimeout(() => depthGame(box), 2600);
    }));
  }
  function blinkGame(box) {
    const r = rand(Math.floor(Math.random() * 1e6)), stars = [];
    for (let i = 0; i < 70; i++) stars.push([r() * 600, r() * 260, 0.6 + r() * 2.4]);
    const v = Math.floor(r() * stars.length);
    box.innerHTML = `<h3>Blink comparator (practice)</h3><p class="small">Astronomers find variable stars by flipping between two pictures. One star changes brightness: click it.</p>
      <canvas class="sl-blinkcv" width="600" height="260" style="cursor:crosshair;background:#000;max-width:100%"></canvas><div class="row"><button type="button" class="b sl-blinknew">New field</button><span class="sl-blinkmsg small"></span></div>`;
    const cv = box.querySelector('canvas'), x = cv.getContext && cv.getContext('2d');
    let frame = 0;
    const draw = () => {
      if (!x) return;
      x.fillStyle = '#000'; x.fillRect(0, 0, 600, 260); x.fillStyle = '#fff';
      stars.forEach(([sx, sy, s], i) => { const rr = i === v && frame ? s * 2.2 : s; x.beginPath(); x.arc(sx, sy, rr, 0, 2 * Math.PI); x.fill(); });
      frame ^= 1;
    };
    draw();
    const timer = setInterval(() => { if (!cv.isConnected) return clearInterval(timer); draw(); }, 600);
    cv.addEventListener('click', e => {
      const rc = cv.getBoundingClientRect(), mx = (e.clientX - rc.left) * 600 / (rc.width || 600), my = (e.clientY - rc.top) * 260 / (rc.height || 260);
      const hit = Math.hypot(mx - stars[v][0], my - stars[v][1]) < 14;
      box.querySelector('.sl-blinkmsg').textContent = hit ? '✓ Found the variable star!' : '✗ Not that one; keep watching.';
      if (hit) bump('sl_blink_wins');
    });
    box.querySelector('.sl-blinknew').addEventListener('click', () => { clearInterval(timer); blinkGame(box); });
  }
  function mystery(box) {
    const c = periodic().find(x => (x.flags || []).length) || periodic()[0];
    if (!c) { box.innerHTML = '<h3>🕵️ The Case of the Missing Light</h3><p class="muted">A new case opens when the planet search has a candidate.</p>'; return; }
    const clues = [
      ['The scene', `Star TIC ${c.tic} dims by ${Math.round(c.depth * 1e6)} parts per million every ${num(c.period, 3)} days, for ${num(c.duration * 24, 1)} hours each time.`],
      ['Clue 1: the shape', c.ai ? `The AI eyewitness describes the dips as: "${esc((c.ai.reasons || []).join('; ') || c.ai.verdict)}".` : 'No eyewitness yet: look at the plot yourself.'],
      ['Clue 2: odd and even', (c.flags || []).some(f => /odd|even/i.test(f)) ? 'Odd-numbered dips are NOT as deep as even ones. Suspicious!' : 'Odd and even dips look equally deep.'],
      ['Clue 3: where the light went', c.centroid ? `The centroid test says the dimming is ${esc(c.centroid.verdict)}.` : 'The centroid test was not run.'],
      ['Clue 4: the odds', c.fpp ? `A statistical test (TRICERATOPS) says: ${esc(c.fpp.verdict)}.` : 'No statistical test yet.']];
    let i = 0;
    const show = () => {
      box.innerHTML = `<h3>🕵️ The Case of the Missing Light</h3>${clues.slice(0, i + 1).map(([h, t]) => `<p><b>${h}.</b> ${t}</p>`).join('')}
        ${i < clues.length - 1 ? '<button type="button" class="b sl-clue">Next clue</button>' : `<p><b>Your verdict?</b></p><div class="row">${ANS.slice(0, 4).map(v => `<button type="button" class="b sl-verdict">${v}</button>`).join('')}</div>`}`;
      const nb = box.querySelector('.sl-clue'); if (nb) nb.addEventListener('click', () => { i++; show(); });
      box.querySelectorAll('.sl-verdict').forEach(b => b.addEventListener('click', () => {
        LS.set('sl_mystery', LS.get('sl_mystery', 0) + 1); badges();
        box.insertAdjacentHTML('beforeend', `<p>Case filed: <b>${esc(b.textContent)}</b>. Real detectives disagree too; that's why candidates need many reviewers. <a target="_blank" rel="noopener" href="${esc(reviewLink(c.tic, b.textContent))}">Send your verdict</a>.</p>`);
        box.querySelectorAll('.sl-verdict').forEach(x => { x.disabled = true; });
      }));
    };
    show();
  }
  function raceTimer(box) {
    const cad = ((D().asteroids || {}).cad) || [];
    const rows = cad.map(r => {
      const au = parseFloat(r.dist), v = parseFloat(r.v_rel);
      if (!au || !v) return null;
      const km = au * 1.496e8, degPerHour = v / km * 3600 * 180 / Math.PI;
      return { des: r.des, when: r.cd, ld: au / 0.00257, degPerHour, moonMin: 0.5 / degPerHour * 60 };
    }).filter(Boolean).sort((a, b) => b.degPerHour - a.degPerHour).slice(0, 8);
    box.innerHTML = `<h3>☄️ Asteroid race timer</h3><p class="small">How fast close-approaching asteroids sweep across the sky at closest approach, judged by how long one would take to cross the full Moon's width (½°).</p>` +
      (rows.length ? `<table class="compact"><tr><th>asteroid</th><th>closest</th><th>distance</th><th>speed on the sky</th><th>crosses the Moon in</th></tr>${rows.map(r => `<tr><td><b>${esc(r.des)}</b></td><td>${esc(r.when)}</td><td class="mono">${num(r.ld, 1)} LD</td><td class="mono">${num(r.degPerHour, 2)}°/h</td><td class="mono">${r.moonMin < 1 ? num(r.moonMin * 60, 0) + ' s' : r.moonMin < 120 ? num(r.moonMin, 1) + ' min' : num(r.moonMin / 60, 1) + ' h'}</td></tr>`).join('')}</table><p class="small muted">LD = lunar distance (384,400 km). Uses each asteroid's speed relative to Earth, at closest approach.</p>` : '<p class="muted">No close approaches listed right now.</p>');
  }
  function playTab(el) {
    el.innerHTML = `<div class="grid g2"><div class="card sl-rev"></div><div class="card sl-ast"></div></div>
      <div class="grid g2"><div class="card sl-pbox"></div><div class="card sl-dip"></div></div>
      <div class="grid g2"><div class="card sl-blink"></div><div class="card sl-mys"></div></div><div class="grid g2"><div class="card sl-depthgame"></div><div class="card sl-race"></div></div>`;
    reviewSection(el.querySelector('.sl-rev')); asteroidSection(el.querySelector('.sl-ast'));
    practiceGame(el.querySelector('.sl-pbox')); dipGame(el.querySelector('.sl-dip'));
    blinkGame(el.querySelector('.sl-blink')); mystery(el.querySelector('.sl-mys')); raceTimer(el.querySelector('.sl-race')); depthGame(el.querySelector('.sl-depthgame'));
  }

  // ------------------------------------------------------------------ Create tab
  const EVENTS = [[13.8, 'Big Bang'], [13.6, 'First stars'], [13.2, 'Milky Way begins to form'], [4.6, 'The Sun forms'], [4.54, 'Earth forms'],
                  [3.7, 'Earliest signs of life'], [2.4, 'Oxygen builds up in the air'], [0.54, 'Animal life explodes (Cambrian)'], [0.066, 'Dinosaurs wiped out'], [0.0003, 'Modern humans']];
  function calendarDate(gyrAgo) {
    const frac = 1 - gyrAgo / 13.8, ms = Date.UTC(2001, 0, 1) + frac * 365 * 86400000;
    const d = new Date(ms);
    return d.toLocaleDateString([], { month: 'short', day: 'numeric', timeZone: 'UTC' }) + (gyrAgo < 0.01 ? ' ' + d.toISOString().slice(11, 16) : '');
  }
  function createTab(el) {
    const ps = periodic();
    const opts = ps.map(c => `<option value="${esc(c.tic)}">TIC ${esc(c.tic)} · ${num(c.period, 2)} d</option>`).join('');
    const near = cands().filter(c => c.star && c.star.dist_pc).sort((a, b) => a.star.dist_pc - b.star.dist_pc).slice(0, 4);
    el.innerHTML = `<div class="card sl-create"><h3>🎨 Make something from real data</h3>${ps.length ? `<div class="row"><label>Candidate <select class="sl-pick">${opts}</select></label>
      <button type="button" class="b sl-mkpost">Postcard</button><button type="button" class="b sl-mkart">Light-curve art</button><button type="button" class="b sl-mkwall">Phone wallpaper</button></div>
      <p class="small muted">Images are drawn from the candidate's dip shape, period and its star's colour, and download as PNG. Please share them as "candidates", not discoveries.</p>` : '<p class="muted">Art appears once the planet search has candidates.</p>'}</div>
      <div class="card"><h3>✨ What do these stars look like?</h3><div class="sl-stars">${cands().filter(c => c.star && c.star.teff).slice(0, 12).map(c => {
        const [r, g, b] = starRGB(c.star.teff), size = Math.max(10, Math.min(60, 22 * (c.star.radius_sun || 0.6)));
        return `<div class="sl-starcard"><div class="sl-glow" style="width:${size}px;height:${size}px;background:radial-gradient(circle, #fff 0%, rgb(${r},${g},${b}) 45%, rgba(${r},${g},${b},0) 72%)"></div><div class="small"><b>TIC ${esc(c.tic)}</b><br>${spectral(c.star.teff)}-type, ${Math.round(c.star.teff)} K${c.star.radius_sun ? ', ' + num(c.star.radius_sun, 2) + '× the Sun' : ''}</div></div>`;
      }).join('') || '<p class="muted">Star details appear with the next update.</p>'}</div><p class="small muted">Colours come from each star's temperature (cooler = redder). Sizes are relative to each other.</p></div>
      <div class="card"><h3>🗓 Cosmic calendar</h3><p class="small">Squeeze the universe's 13.8 billion years into one year, starting with the Big Bang on January 1.</p>
      <table class="compact">${EVENTS.map(([g, e]) => `<tr><td class="mono">${calendarDate(g)}</td><td>${esc(e)}</td></tr>`).join('')}
      ${near.map(c => `<tr><td class="mono">Dec 31, last ${num(c.star.dist_pc * 3.26 / 13.8e9 * 365 * 86400, 3)} s</td><td>Light we see today from TIC ${esc(c.tic)} leaves its star (${Math.round(c.star.dist_pc * 3.26)} light-years away)</td></tr>`).join('')}</table></div>`;
    const pick = () => candBy(el.querySelector('.sl-pick').value);
    const on = (cls, fn) => { const b = el.querySelector(cls); if (b) b.addEventListener('click', () => { const c = pick(); if (c) fn(c); }); };
    on('.sl-mkpost', c => { postcard(c); bump('sl_art'); });
    on('.sl-mkart', c => artPiece(c, 1080, 1080, `tic-${c.tic}-art.png`));
    on('.sl-mkwall', c => artPiece(c, 1170, 2532, `tic-${c.tic}-wallpaper.png`));
  }

  // ------------------------------------------------------------------ My lab tab
  function altAz(raDeg, decDeg, lat, lon, date) {
    const d = (date.getTime() / 86400000 + 2440587.5) - 2451545.0;
    const gmst = (280.46061837 + 360.98564736629 * d) % 360, lst = (gmst + lon + 360) % 360;
    const H = (lst - raDeg) * Math.PI / 180, dec = decDeg * Math.PI / 180, la = lat * Math.PI / 180;
    const alt = Math.asin(Math.sin(dec) * Math.sin(la) + Math.cos(dec) * Math.cos(la) * Math.cos(H));
    const az = Math.atan2(-Math.sin(H) * Math.cos(dec), Math.sin(dec) * Math.cos(la) - Math.cos(dec) * Math.sin(la) * Math.cos(H));
    return [alt * 180 / Math.PI, (az * 180 / Math.PI + 360) % 360];
  }
  const compass = az => ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'][Math.round(az / 45) % 8];
  function skyPointer(box) {
    const ts = (((D().exowatch || {}).stale) || []).filter(t => t.ra !== null && t.ra !== undefined);
    box.innerHTML = `<h3>🧭 Sky pointer</h3><p class="small">Where is that star right now from where you stand? Your location is used only in your browser and is never sent or saved.</p>
      ${ts.length ? `<div class="row"><select class="sl-ptarget">${ts.slice(0, 25).map((t, i) => `<option value="${i}">${esc(t.name)} (V ${num(t.vmag, 1)})</option>`).join('')}</select><button type="button" class="b sl-locate">Point me</button></div><div class="sl-pout"></div>` : '<p class="muted">Targets appear with the next Exoplanet Watch update.</p>'}`;
    const go = box.querySelector('.sl-locate'); if (!go) return;
    go.addEventListener('click', () => {
      const t = ts[+box.querySelector('.sl-ptarget').value], out = box.querySelector('.sl-pout');
      const saved = LS.get('sl_place', null);
      const show = p => {
        const [alt, az] = altAz(t.ra, t.dec, p.coords.latitude, p.coords.longitude, new Date());
        out.innerHTML = alt > 0 ? `<p><b>${esc(t.name)}</b> is <b>${Math.round(alt)}°</b> above the horizon toward the <b>${compass(az)}</b> (bearing ${Math.round(az)}°).</p><div class="sl-arrow" style="transform:rotate(${Math.round(az)}deg)">⬆</div><p class="small muted sl-compassnote">The arrow points that way when the top of your screen faces north.</p>${'DeviceOrientationEvent' in window ? '<button type="button" class="b sl-compass">📱 Use my phone\'s compass</button>' : ''}`
                                : `<p><b>${esc(t.name)}</b> is below your horizon right now (${Math.round(alt)}°). Try later tonight.</p>`;
        const cb = out.querySelector('.sl-compass');
        if (cb) cb.addEventListener('click', async () => {
          try { if (typeof DeviceOrientationEvent.requestPermission === 'function' && await DeviceOrientationEvent.requestPermission() !== 'granted') return; } catch (_) { return; }
          const arrow = out.querySelector('.sl-arrow');
          const on = e => {
            if (!arrow.isConnected) return removeEventListener('deviceorientation', on);
            const head = e.webkitCompassHeading ?? (e.absolute && e.alpha !== null ? 360 - e.alpha : null);
            if (head === null || head === undefined) return;
            arrow.style.transform = `rotate(${Math.round(az - head)}deg)`;
            out.querySelector('.sl-compassnote').textContent = `Hold the phone flat and turn until the arrow points straight up; then look ${Math.round(alt)}° above the horizon.`;
          };
          addEventListener('deviceorientation', on);
        });
      };
      if (saved && isFinite(saved.lat)) return show({ coords: { latitude: saved.lat, longitude: saved.lon } });
      if (!navigator.geolocation) { out.textContent = 'This browser cannot share a location. Set a ZIP code in the Stargazing tab instead.'; return; }
      out.textContent = 'Asking your browser for your location…';
      navigator.geolocation.getCurrentPosition(show, () => { out.textContent = 'Location not shared, so the pointer cannot work. Nothing was stored.'; }, { timeout: 15000, maximumAge: 600000 });
    });
  }
  function observingLog(box) {
    const render = () => {
      const log = LS.get('sl_log', []);
      box.innerHTML = `<h3>📓 My observing log</h3><p class="small">Saved only in this browser. Phone photos of the Moon or planets welcome.</p>
        <div class="row"><input class="sl-lobj" placeholder="What did you see? e.g. Jupiter's moons" style="flex:1;min-width:200px"><input type="file" accept="image/*" class="sl-lphoto"></div>
        <textarea class="sl-lnote" rows="2" placeholder="Notes: sky conditions, equipment…" style="width:100%"></textarea><div class="row"><button type="button" class="b primary sl-ladd">Add entry</button><button type="button" class="b sl-lexport">Export</button>${S.cloudCheck ? '<button type="button" class="b sl-cloud" title="Your local AI looks at the photo">☁️ What clouds are these?</button>' : ''}</div><div class="sl-cloudout small"></div>
        ${log.slice().reverse().map(e => `<div class="sl-logentry"><b>${esc(e.obj)}</b> <span class="small muted">${esc(e.date)}</span><br>${esc(e.note || '')}${e.img ? `<br><img src="${e.img}" alt="" class="sl-logimg">` : ''}</div>`).join('') || '<p class="muted small">No entries yet.</p>'}`;
      box.querySelector('.sl-ladd').addEventListener('click', () => {
        const obj = box.querySelector('.sl-lobj').value.trim(); if (!obj) return;
        const note = box.querySelector('.sl-lnote').value.trim(), f = box.querySelector('.sl-lphoto').files[0];
        const save = img => { LS.set('sl_log', LS.get('sl_log', []).concat({ obj, note, img, date: new Date().toLocaleString() }).slice(-60)); badges(); render(); };
        if (!f) return save(null);
        const fr = new FileReader();
        fr.onload = () => { const im = new Image(); im.onload = () => { const cv = document.createElement('canvas'), k = Math.min(1, 480 / im.width); cv.width = im.width * k; cv.height = im.height * k; const x = cv.getContext('2d'); if (x) { x.drawImage(im, 0, 0, cv.width, cv.height); save(cv.toDataURL('image/jpeg', 0.7)); } else save(null); }; im.src = fr.result; };
        fr.readAsDataURL(f);
      });
      const cl = box.querySelector('.sl-cloud');
      if (cl) cl.addEventListener('click', () => {
        const f = box.querySelector('.sl-lphoto').files[0], out = box.querySelector('.sl-cloudout');
        if (!f) { out.textContent = 'Choose a sky photo first.'; return; }
        out.textContent = 'Your local AI is looking at the photo…';
        const fr = new FileReader();
        fr.onload = () => Promise.resolve(S.cloudCheck(fr.result)).then(r => {
          out.innerHTML = r && r.type ? `<b>${esc(r.type)}</b>${typeof r.confidence === 'number' ? ' (' + Math.round(r.confidence * 100) + '% sure)' : ''}: ${esc(r.why || '')}${r.observing ? '<br>🔭 ' + esc(r.observing) : ''} <span class="muted">Disagree? Add your own answer in the notes.</span>` : 'No answer from the local AI.';
          const n = box.querySelector('.sl-lnote'); if (r && r.type && !n.value) n.value = 'Clouds: ' + r.type + ' (AI guess)';
        }, e => { out.textContent = 'The local AI could not check it: ' + (e && e.message || e); });
        fr.readAsDataURL(f);
      });
      box.querySelector('.sl-lexport').addEventListener('click', () => {
        const a = document.createElement('a'); a.download = 'observing-log.json';
        a.href = 'data:application/json,' + encodeURIComponent(JSON.stringify(LS.get('sl_log', []), null, 1)); a.click();
      });
    };
    render();
  }
  const GUIDE = [['Start with binoculars', '7×50 or 10×50 binoculars show Jupiter\'s moons, the Moon\'s craters and star clusters. Cheap, light, no setup.'],
    ['Learn the sky first', 'A free planetarium app (Stellarium) and a red flashlight. Find the bright planets and a few constellations.'],
    ['First telescope', 'A 6–8 inch Dobsonian is the classic best value: simple, stable and bright. Avoid "high magnification" toys on wobbly tripods.'],
    ['Smart telescopes', 'Seestar or Dwarf-type smart scopes photograph faint objects from cities and can time exoplanet transits for NASA\'s Exoplanet Watch.'],
    ['Join in', 'Local astronomy clubs lend telescopes. Citizen-science projects (Exoplanet Watch, AAVSO, Zooniverse) turn your nights into real data.']];
  const LEVELS = [[0, 'Stargazer', 'just starting'], [5, 'Classifier', 'can read a light curve'], [20, 'Reviewer', 'checks candidates like the pros'],
                  [50, 'Analyst', 'spots binaries and glitches at a glance'], [100, 'Contributor', 'sends reviews to the project']];
  function points() {
    const s = stats();
    return s.reviews + s.ast + (window.SpaceLabHome ? Object.keys(LS.get('sl_daily', {})).length : 0) + 2 * (s.periodWins + s.blinkWins + s.mystery + LS.get('sl_depth_wins', 0)) + 5 * LS.get('sl_sent', 0);
  }
  function levelHTML() {
    const pt = points(), i = LEVELS.filter(l => pt >= l[0]).length - 1, cur = LEVELS[i], nxt = LEVELS[i + 1];
    return `<div class="sl-level"><div class="sl-path">${LEVELS.map((l, j) => `<span class="${j <= i ? 'on' : ''}">${esc(l[1])}</span>`).join('<i>→</i>')}</div>
      <p>You're a <b>${esc(cur[1])}</b> (${esc(cur[2])}) with <b>${pt}</b> points.${nxt ? ` ${nxt[0] - pt} more to become a ${esc(nxt[1])}.` : ' The top level: thank you!'}</p>
      ${nxt ? `<div class="sl-bar"><i style="width:${Math.round((pt - cur[0]) / (nxt[0] - cur[0]) * 100)}%"></i></div>` : ''}
      <p class="small muted">Points: 1 per review or daily vote, 2 per game won, 5 per review sent to GitHub.</p></div>`;
  }
  function contribText() {
    const s = stats(), daily = Object.keys(LS.get('sl_daily', {})).length;
    const parts = [s.reviews && `reviewed ${s.reviews} TESS planet candidate${s.reviews > 1 ? 's' : ''}`, s.ast && `checked ${s.ast} asteroid sighting${s.ast > 1 ? 's' : ''}`,
                   daily && `voted on ${daily} candidate${daily > 1 ? 's' : ''} of the day`].filter(Boolean);
    return parts.length ? `🔭 I've ${parts.join(', ')} on Space Lab, citizen science with real NASA data. Join in: ${site()}` : '';
  }
  const favSig = c => `${c.status}${c.ai ? ', AI: ' + c.ai.verdict : ''}${c.fpp ? ', ' + c.fpp.verdict : ''}`;
  function myLab(el) {
    const favs = LS.get('sl_favs', []).map(String), last = LS.get('sl_seen_at', 0), snap = LS.get('sl_fav_snap', {}), notes = LS.get('sl_notes', {});
    const changes = favs.filter(t => { const c = candBy(t); return c && snap[t] && snap[t] !== favSig(c); }).length;
    const fresh = last ? cands().filter(c => c.created * 1000 > last) : [];
    const contrib = D().contributions || {}, comm = D().community || {};
    el.innerHTML = `<div class="grid g2"><div class="card"><h3>⭐ My candidate cards</h3>${favs.length ? `<div class="sl-cards">${favs.map(t => {
        const c = candBy(t);
        if (!c) return `<div class="sl-tcard"><b>TIC ${esc(t)}</b><div class="small muted">no longer on the list (reviewed or archived)</div></div>`;
        const [r, g, b] = starRGB(c.star && c.star.teff), was = snap[t], now = favSig(c);
        const changed = was && was !== now ? `<div class="sl-changed">🔔 updated since you saved it: ${esc(was)} → ${esc(now)}</div>` : '';
        return `<div class="sl-tcard" style="border-color:rgb(${r},${g},${b})"><b><a href="#tess" data-goto="tic-${esc(c.tic)}">TIC ${esc(c.tic)}</a></b><div class="small">${c.period ? num(c.period, 2) + '-day orbit' : 'lone dip'} · ${num(c.rp, 1)}× Earth if real</div><div class="small muted">${esc(now)}</div>${changed}
          <textarea rows="2" data-note="${esc(c.tic)}" placeholder="your notes">${esc(notes[t] || '')}</textarea></div>`;
      }).join('')}</div><p class="small muted">Notes stay in this browser. Share a card with the "Share text" button on the TESS tab.</p>` : '<p class="muted small">Tap ☆ Save on any TESS candidate to collect it here.</p>'}</div>
      <div class="card"><h3>🧭 Your path</h3>${levelHTML()}${contribText() ? `<h4>Share what you've done</h4><textarea class="sl-contrib" readonly rows="3">${esc(contribText())}</textarea><button type="button" class="b sl-copy">Copy</button>` : '<p class="small muted">Review a candidate or vote on the candidate of the day to start your record.</p>'}</div>
      <div class="card"><h3>🔔 Watchlist</h3><p>${last ? `${fresh.length} new candidate${fresh.length === 1 ? '' : 's'} since your last visit (${new Date(last).toLocaleDateString()}).` : 'This is your first visit: from now on, new candidates are marked for you.'}</p>
        <p class="small">Saved candidates: ${favs.length}.${changes ? ` <b>${changes} of them changed</b> since you last looked (see the 🔔 on the cards).` : ' When the AI, a test or the volunteer updates one, it gets a 🔔 here.'}</p><h3>🔥 Streak</h3><p>You've checked in <b>${streak()}</b> day${streak() === 1 ? '' : 's'} in a row.</p>${badgeHTML()}</div></div>
      <div class="grid g2"><div class="card sl-log"></div><div class="card sl-pointer"></div></div>
      <div class="grid g2"><div class="card"><h3>🏆 Community leaderboard</h3>${(comm.leaderboard || []).length ? `<table class="compact"><tr><th>reviewer</th><th>reviews</th></tr>${comm.leaderboard.map(r => `<tr><td>${esc(r.user)}</td><td class="mono">${r.reviews}</td></tr>`).join('')}</table>` : '<p class="muted small">No reviews posted on GitHub yet. Be the first: Play → Planet or not?</p>'}
        <p class="small muted">Counted from review forms on GitHub. Your own answers in this browser count toward your badges.</p></div>
      <div class="card"><h3>📊 This lab's contributions</h3><div class="kv"><b>Stars searched</b><span>${contrib.stars_searched ?? '—'}</span><b>Candidates reviewed</b><span>${contrib.tess_reviewed ?? '—'}</span><b>Exoplanet Watch light curves</b><span>${contrib.exowatch_reduced ?? '—'}</span><b>Asteroid sightings checked</b><span>${contrib.asteroid_sightings_checked ?? '—'}</span><b>Results submitted</b><span>${contrib.uploads_done ?? '—'}</span></div></div></div>
      <div class="card"><h3>🔭 Starting out with a telescope</h3><ol>${GUIDE.map(([h, t]) => `<li><b>${esc(h)}.</b> ${esc(t)}</li>`).join('')}</ol></div>`;
    observingLog(el.querySelector('.sl-log')); skyPointer(el.querySelector('.sl-pointer'));
    const cp = el.querySelector('.sl-copy');
    if (cp) cp.addEventListener('click', () => { const t = el.querySelector('.sl-contrib'); t.select(); try { navigator.clipboard && navigator.clipboard.writeText(t.value); } catch (_) {} cp.textContent = 'Copied ✓'; });
    el.querySelectorAll('textarea[data-note]').forEach(t => t.addEventListener('input', () => { const n = LS.get('sl_notes', {}); n[t.dataset.note] = t.value.slice(0, 1000); LS.set('sl_notes', n); }));
    el.querySelectorAll('[data-goto]').forEach(a => a.addEventListener('click', () => setTimeout(() => { const x = document.getElementById(a.dataset.goto) || document.querySelector(`.card[data-tic="${a.dataset.goto.slice(4)}"]`); if (x && x.scrollIntoView) x.scrollIntoView({ block: 'center' }); }, 400)));
    // remember what saved candidates look like now, so the next visit can show what changed
    const ns = {}; favs.forEach(t => { const c = candBy(t); if (c) ns[t] = favSig(c); }); LS.set('sl_fav_snap', Object.assign({}, snap, ns));
  }

  // ------------------------------------------------------------------ embed mode: ?card=<TIC> shows one card only
  function embedMode() {
    const tic = new URLSearchParams(location.search).get('card');
    if (!tic || !/^\d+$/.test(tic)) return false;
    document.body.classList.add('sl-embedded');
    S.onRender(() => {
      const c = candBy(tic), main = document.querySelector('main');
      if (!main || main.dataset.embedded) return;
      main.dataset.embedded = 1;
      main.innerHTML = c ? `<div class="card" data-tic="${esc(c.tic)}"><h3>Planet candidate TIC ${esc(c.tic)}</h3><div class="small">${c.period ? 'dips every ' + num(c.period, 3) + ' days · ' : ''}${Math.round(c.depth * 1e6)} ppm · ~${num(c.rp, 1)}× Earth if real · ${esc(c.status)}</div>
        ${c.plot ? `<img class="plot" src="${esc(c.plot)}" alt="light-curve plots">` : ''}<p class="small"><a href="${site()}#tic-${esc(c.tic)}" target="_blank" rel="noopener">Help review it at Space Lab ↗</a></p></div>`
        : `<div class="card">TIC ${esc(tic)} is not on the current list. <a href="${site()}" target="_blank" rel="noopener">Open Space Lab ↗</a></div>`;
      decorateTess(main);
    });
    return true;
  }

  // ------------------------------------------------------------------ Asteroids tab: the story behind each old sighting
  function precoveryStories(el) {
    const a = D().asteroids || {}, tg = {};
    (a.targets || []).forEach(t => { tg[t.des] = t; });
    for (const c of a.cands || []) {
      if (el.querySelector(`.sl-story[data-id="${c.id}"]`)) continue;
      const t = tg[c.des] || {}, year = parseInt(c.night, 10), arcYears = t.arc ? t.arc / 365.25 : null;
      const gain = year && t.arc ? (new Date().getFullYear() - year) - arcYears : null;
      const story = `<details class="sl-story note" data-id="${esc(c.id)}"><summary>📜 The story behind this picture</summary>
        <p>On the night of ${esc(c.night)}, the ${esc(c.archive)} survey photographed this patch of sky for its own reasons. Nobody knew ${esc(c.des)} was in the frame${t.arc ? `: today the asteroid's orbit rests on only ${t.arc < 60 ? Math.round(t.arc) + ' days' : num(arcYears, 1) + ' years'} of observations` : ''}.</p>
        <p>Using today's orbit, Space Lab worked out where it should have been that night and found a moving dot ${num(c.offset, 0)}″ (arc-seconds) away from the prediction. ${gain && gain > 0.2 ? `If a person confirms it, this one picture stretches the observed orbit by about ${num(gain, 1)} years, which pins down where the asteroid will be decades from now.` : 'If a person confirms it, the extra position makes the orbit more precise.'}</p>
        <p class="small muted">This is how many asteroids were taken off risk lists: an old photo showed where they really were.</p></details>`;
      const host = el.querySelector(`.card[data-pc="${c.id}"]`);
      if (host && !host.querySelector('.sl-story')) host.insertAdjacentHTML('beforeend', story);
    }
  }

  // ------------------------------------------------------------------ wiring
  recordVisit();
  if (!embedMode()) {
    S.addTab('play', 'Play & review', playTab);
    S.addTab('create', 'Create', createTab);
    S.addTab('mylist', 'My lab', myLab);
    S.onRender((tab, el) => {
      if (tab === 'tess' && el) decorateTess(el);
      if (tab === 'asteroids' && el) precoveryStories(el);
      if (tab === 'tess' && el && !el.querySelector('.sl-newbanner')) {
        const last = LS.get('sl_seen_at', 0), fresh = last ? cands().filter(c => c.created * 1000 > last).length : 0;
        if (fresh) el.insertAdjacentHTML('afterbegin', `<div class="note sl-newbanner">🔔 ${fresh} new candidate${fresh === 1 ? '' : 's'} since your last visit.</div>`);
      }
    });
    addEventListener('pagehide', () => LS.set('sl_seen_at', Date.now()));
  }
  window.SpaceLabExtras = { starRGB, spectral, altAz,
    level: () => { const pt = points(), i = LEVELS.filter(l => pt >= l[0]).length - 1; return { name: LEVELS[i][1], points: pt, next: LEVELS[i + 1] || null }; } };
})();
