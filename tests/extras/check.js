// Automatic checks for web/extras.js (ROADMAP tasks 1-11), run in a simulated browser (jsdom) with fake data.
//   one-time:   cd tests/extras && npm install
//   run all:    node tests/extras/check.js
//   one task:   node tests/extras/check.js 3
// Every line that starts with FAIL tells you exactly what to fix. Exit code 0 = everything passed.
'use strict';
const fs = require('fs');
const path = require('path');
let JSDOM, VirtualConsole;
try { ({ JSDOM, VirtualConsole } = require('jsdom')); } catch (e) {
  console.error('jsdom is missing. Run once:  cd tests/extras && npm install');
  process.exit(2);
}

const ROOT = path.resolve(__dirname, '..', '..');
const read = p => fs.readFileSync(path.join(ROOT, p), 'utf8');
const FIXTURE = JSON.parse(fs.readFileSync(path.join(__dirname, 'fixture.json'), 'utf8'));
const ONLY = process.argv[2] ? String(process.argv[2]) : null;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const norm = s => String(s || '').replace(/\s+/g, ' ').trim();
const TABS = ['today', 'brief', 'exowatch', 'tess', 'asteroids', 'about'];

async function boot({ extras = true, storage = {}, data = FIXTURE, hash = '' } = {}) {
  const html = read('web/public.html').replace(/<script src=[^>]+><\/script>/g, '').replace(/<link rel="stylesheet"[^>]*>/g, '');
  const rec = { errors: [], opened: [], alerts: 0, downloads: [], audio: [], canvasText: [] };
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => { const m = String(e && (e.detail && e.detail.message || e.message) || e); if (!/Not implemented/.test(m)) rec.errors.push('uncaught: ' + m.slice(0, 200)); });
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://example.github.io/Space-Lab/' + hash, pretendToBeVisual: true, virtualConsole: vc });
  const w = dom.window;
  w.console.error = (...a) => rec.errors.push(a.map(String).join(' ').slice(0, 300));
  w.alert = w.confirm = w.prompt = () => { rec.alerts++; return null; };
  w.open = url => { rec.opened.push(String(url)); return null; };
  w.HTMLAnchorElement.prototype.click = function () {
    if (this.hasAttribute('download')) rec.downloads.push({ name: this.getAttribute('download'), href: this.href });
    else if (this.target === '_blank') rec.opened.push(this.href);
  };
  w.HTMLCanvasElement.prototype.getContext = function () {
    const ctx = new Proxy({}, {
      get: (t, k) => {
        if (k === 'fillText' || k === 'strokeText') return (s) => rec.canvasText.push(String(s));
        if (k === 'measureText') return s => ({ width: String(s).length * 8 });
        if (k === 'createLinearGradient' || k === 'createRadialGradient') return () => ({ addColorStop() {} });
        if (k === 'getImageData') return () => ({ data: new Uint8ClampedArray(4) });
        if (k in t) return t[k];
        return () => {};
      },
      set: (t, k, v) => { t[k] = v; return true; },
    });
    return ctx;
  };
  w.HTMLCanvasElement.prototype.toDataURL = () => 'data:image/png;base64,AAAA';
  class FakeParam { constructor() { this.value = 1; this.calls = 0; } setValueAtTime(v) { this.calls++; rec.audio.calls = (rec.audio.calls || 0) + 1; this.value = v; return this; }
    linearRampToValueAtTime() { return this.setValueAtTime(...arguments); } exponentialRampToValueAtTime() { return this.setValueAtTime(...arguments); }
    setTargetAtTime() { return this.setValueAtTime(...arguments); } cancelScheduledValues() { return this; } setValueCurveAtTime() { return this.setValueAtTime(...arguments); } }
  class FakeNode { constructor() { this.gain = new FakeParam(); this.frequency = new FakeParam(); this.detune = new FakeParam(); }
    connect(n) { return n; } disconnect() {} start() { this.started = true; } stop() { this.stopped = true; } }
  class FakeAudioContext {
    constructor() { this.state = 'running'; this.currentTime = 0; this.sampleRate = 44100; this.destination = new FakeNode(); rec.audio.push(this); }
    createOscillator() { return new FakeNode(); } createGain() { return new FakeNode(); } createBufferSource() { return new FakeNode(); }
    createBuffer(c, n) { return { getChannelData: () => new Float32Array(Math.min(n, 1e6)) }; } createBiquadFilter() { return new FakeNode(); }
    resume() { return Promise.resolve(); } suspend() { return Promise.resolve(); } close() { this.state = 'closed'; return Promise.resolve(); }
  }
  w.AudioContext = w.webkitAudioContext = FakeAudioContext;
  for (const [k, v] of Object.entries(storage)) w.localStorage.setItem(k, v);
  const style = w.document.createElement('style');
  style.textContent = read('web/style.css') + '\n' + (extras ? read('web/extras.css') : '');
  w.document.head.appendChild(style);
  const json = JSON.parse(JSON.stringify(data));
  w.fetch = async () => ({ ok: true, json: async () => json });
  w.eval(read('web/public.js'));
  if (extras) {
    try { w.eval(read('web/extras.js')); } catch (e) { rec.errors.push('extras.js crashed while loading: ' + e.message); }
  }
  for (let i = 0; i < 50 && !/updated/.test(w.document.querySelector('#updated').textContent); i++) await sleep(20);
  const $ = s => w.document.querySelector(s);
  const $$ = s => [...w.document.querySelectorAll(s)];
  const tab = async id => { const b = $(`nav.tabs button[data-tab="${id}"]`); if (b) b.click(); await sleep(10); return $('#v-' + id); };
  const visible = () => $$('section.view.on').map(s => s.id);
  return { w, rec, $, $$, tab, visible };
}

// Card text with everything added by extras removed: must equal the page without extras.
function coreText(card) {
  const c = card.cloneNode(true);
  c.querySelectorAll('abbr.sl-gl').forEach(a => a.replaceWith(c.ownerDocument.createTextNode(a.textContent)));
  c.querySelectorAll('[class*="sl-"]').forEach(e => e.remove());
  return norm(c.textContent);
}

const results = [];
function report(task, name, fails) {
  results.push({ task, name, fails: [...new Set(fails)] });
}

const CHECKS = {
  async G() {
    const fails = [];
    const base = await boot({ extras: false });
    const baseCards = {};
    await base.tab('tess');
    base.$$('#v-tess .card[id^="tic-"]').forEach(c => { baseCards[c.id] = norm(c.textContent); });
    const env = await boot();
    for (const t of [...TABS, ...env.$$('nav.tabs button').map(b => b.dataset.tab).filter(x => x && !TABS.includes(x))]) {
      await env.tab(t);
      if (env.visible().length !== 1) fails.push(`opening tab "${t}" shows ${env.visible().length} views (must be exactly 1)`);
    }
    await env.tab('tess');
    for (const [id, txt] of Object.entries(baseCards)) {
      const card = env.$('#' + id);
      if (!card) { fails.push(`card #${id} disappeared`); continue; }
      const got = coreText(card);
      if (got !== txt) fails.push(`card #${id}: the original text was changed or duplicated. Every element extras add must have a class starting with "sl-", and glossary words must be wrapped, not copied.\n      expected: ${txt.slice(0, 160)}\n      got:      ${got.slice(0, 160)}`);
    }
    for (const t of env.$$('nav.tabs button').map(b => b.dataset.tab)) {
      if (!t) fails.push('every button inside nav.tabs must be a real tab (data-tab). Put other controls (Basic/Expert, badges...) outside nav.tabs, e.g. in header .status');
    }
    // click every extras control in every tab: the page must survive
    for (const t of env.$$('nav.tabs button').map(b => b.dataset.tab).filter(Boolean)) {
      const view = await env.tab(t);
      for (const el of [...view.querySelectorAll('button[class*="sl-"], a[class*="sl-"], [class*="sl-"] button, [class*="sl-"][role=button], .sl-fav')]) {
        if (!el.isConnected) continue;
        el.click(); await sleep(5);
        if (env.visible().length !== 1 || /undefined/.test(env.w.location.hash)) { fails.push(`clicking "${norm(el.textContent).slice(0, 30)}" in tab ${t} broke the page`); break; }
      }
    }
    const src = read('web/extras.js');
    const urls = (src.match(/https?:\/\/[^\s'"`)]+/g) || []).filter(u => !u.startsWith('https://github.com/TechAmatuer/Space-Lab/issues/new') && !u.startsWith('http://www.w3.org/2000/svg'));
    if (urls.length) fails.push('extras.js must not contain other web addresses: ' + urls.join(', '));
    if (/\balert\(|\bconfirm\(|\bprompt\(/.test(src)) fails.push('do not use alert/confirm/prompt; show messages inside the page');
    if (/\bfetch\(|XMLHttpRequest|import\(/.test(src)) fails.push('extras.js must not load anything (no fetch/XMLHttpRequest)');
    const keys = Object.keys(env.w.localStorage);
    const bad = keys.filter(k => !/^sl_[a-z0-9_]+$/.test(k) || k.startsWith('sl_sl_'));
    if (bad.length) fails.push('localStorage keys must look like sl_name (not sl_sl_...): ' + bad.join(', '));
    if (env.rec.alerts) fails.push(`alert/confirm was called ${env.rec.alerts} times`);
    const errs = env.rec.errors.filter(e => !/Not implemented: (navigation|HTMLMediaElement)/.test(e));
    if (errs.length) fails.push('errors in the browser console:\n      ' + errs.slice(0, 6).join('\n      '));
    report('G', 'whole page: no crashes, nothing broken, nothing external', fails);
  },

  async 1() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    const abbrs = [...tess.querySelectorAll('abbr.sl-gl')];
    if (abbrs.length < 3) fails.push(`TESS tab: expected at least 3 glossary words (abbr.sl-gl), found ${abbrs.length}`);
    abbrs.forEach(a => { if (!norm(a.title || a.dataset.def)) fails.push(`glossary word "${a.textContent}" has no definition (title is empty: look up the term case-insensitively)`); });
    tess.querySelectorAll('.card').forEach(card => {
      const seen = {};
      card.querySelectorAll('abbr.sl-gl').forEach(a => { const k = a.textContent.toLowerCase(); if (seen[k]) fails.push(`"${a.textContent}" wrapped twice in one card`); seen[k] = 1; });
    });
    if (tess.querySelector('a abbr.sl-gl, button abbr.sl-gl')) fails.push('glossary words must not be wrapped inside links or buttons');
    const card = tess.querySelector('#tic-111111');
    if (card && card.querySelectorAll('abbr.sl-gl').length < 2) fails.push('each card should get every term it contains (first occurrence of EACH term), not just one term per card');
    const ast = await env.tab('asteroids');
    if (!ast.querySelector('abbr.sl-gl')) fails.push('Asteroids tab: no glossary words (it mentions Palermo, Torino, lunar distance...). Apply the glossary on every tab.');
    const a = abbrs[0];
    if (a) { a.click(); await env.w.Promise.resolve(); const pop = env.$('[class*="sl-gl-pop"], #sl-glossary-popup');
      if (!pop || env.w.getComputedStyle(pop).display === 'none' || !norm(pop.textContent)) fails.push('tapping a glossary word must show its definition in a popup (element with class sl-gl-pop)'); }
    report(1, 'Glossary tooltips', fails);
  },

  async 2() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    for (const c of FIXTURE.tess.cands) {
      const card = tess.querySelector('#tic-' + c.tic);
      const svg = card && card.querySelector('svg.sl-size, .sl-size svg');
      if (!svg) { fails.push(`card ${c.tic}: no size picture (svg with class sl-size)`); continue; }
      const earth = svg.querySelector('[data-body="earth"]'), cand = svg.querySelector('[data-body="candidate"]');
      if (!earth || !cand) { fails.push(`card ${c.tic}: mark the circles with data-body="earth" and data-body="candidate" (also "neptune", "jupiter")`); continue; }
      const ratio = parseFloat(cand.getAttribute('r')) / parseFloat(earth.getAttribute('r'));
      if (!(Math.abs(ratio - c.rp) / c.rp < 0.1)) fails.push(`card ${c.tic}: candidate circle should be ${c.rp}x Earth's, it is ${ratio.toFixed(2)}x. Read rp from SpaceLab.data, not from the text`);
      const vb = (svg.getAttribute('viewBox') || '').split(/[\s,]+/).map(Number);
      if (vb.length !== 4) fails.push(`card ${c.tic}: give the svg a viewBox so nothing is cut off`);
      else svg.querySelectorAll('circle').forEach(ci => {
        const cx = +ci.getAttribute('cx'), cy = +ci.getAttribute('cy'), r = +ci.getAttribute('r');
        if (cx - r < vb[0] - 0.5 || cx + r > vb[0] + vb[2] + 0.5 || cy - r < vb[1] - 0.5 || cy + r > vb[1] + vb[3] + 0.5) fails.push(`card ${c.tic}: the ${ci.getAttribute('data-body') || 'a'} circle sticks out of the viewBox`);
      });
      const h = parseFloat(svg.getAttribute('height') || svg.style.height || '0');
      if (h > 140) fails.push(`card ${c.tic}: picture is ${h}px tall (max 140)`);
      if (!/if it is a planet|if real/i.test((svg.parentElement.closest('.sl-size') || svg.parentElement).textContent)) fails.push(`card ${c.tic}: caption must say "if it is a planet"`);
    }
    report(2, 'How big is it?', fails);
  },

  async 3() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    if (env.rec.audio.length) fails.push('sound must not start (no AudioContext) before a click');
    for (const c of FIXTURE.tess.cands) {
      const btn = tess.querySelector(`#tic-${c.tic} button.sl-listen`);
      if (c.kind === 'periodic' && !btn) fails.push(`card ${c.tic}: no "Listen" button (button.sl-listen)`);
      if (c.kind !== 'periodic' && btn) fails.push(`card ${c.tic}: single-dip candidates have no period, so no Listen button`);
    }
    const b1 = tess.querySelector('#tic-111111 button.sl-listen'), b2 = tess.querySelector('#tic-222222 button.sl-listen');
    if (b1 && b2) {
      const t0 = Date.now(); b2.click(); await sleep(5);
      if (Date.now() - t0 > 300) fails.push(`clicking Listen froze the page for ${Date.now() - t0} ms (schedule a few hundred gain changes, not one per sample)`);
      if ((env.rec.audio.calls || 0) > 5000) fails.push(`${env.rec.audio.calls} audio scheduling calls; keep it under 5000`);
      if (!b2.classList.contains('playing')) fails.push('the clicked button itself must get class "playing" (each button must play ITS OWN candidate)');
      if (b1.classList.contains('playing')) fails.push('another card\'s button got "playing"');
      b2.click(); await sleep(5);
      if (b2.classList.contains('playing')) fails.push('clicking again must stop the sound and remove "playing"');
      const ctx = env.rec.audio[env.rec.audio.length - 1];
      if (ctx && ctx.state !== 'closed') fails.push('stopping must close the AudioContext');
    }
    report(3, 'Listen to a transit', fails);
  },

  async 4() {
    const fails = [], env = await boot();
    const tog = env.$('.sl-level');
    if (!tog) { fails.push('no Basic/Expert control (class sl-level) in the header'); return report(4, 'Basic / Expert switch', fails); }
    if (tog.closest('nav.tabs')) fails.push('the Basic/Expert control must not be inside nav.tabs (it would be treated as a tab)');
    if (env.w.document.body.dataset.level !== 'basic') fails.push('first-time visitors must start in Basic (body data-level="basic")');
    await env.tab('tess');
    const kv = env.$('#v-tess .kv');
    if (kv && env.w.getComputedStyle(kv).display !== 'none') fails.push('in Basic mode .kv tables must be hidden (display:none in extras.css)');
    tog.click(); await sleep(5);
    if (env.w.document.body.dataset.level !== 'expert') fails.push('clicking the control must switch to Expert');
    if (kv && env.w.getComputedStyle(kv).display === 'none') fails.push('in Expert mode .kv tables must be visible');
    if (env.w.localStorage.getItem('sl_level') !== 'expert' && env.w.localStorage.getItem('sl_level') !== '"expert"') fails.push('the choice must be saved in localStorage key sl_level');
    const env2 = await boot({ storage: { sl_level: env.w.localStorage.getItem('sl_level') } });
    if (env2.w.document.body.dataset.level !== 'expert') fails.push('after reload the saved choice (Expert) must be restored');
    report(4, 'Basic / Expert switch', fails);
  },

  async 5() {
    const fails = [], env = await boot();
    const tabBtn = env.$('nav.tabs button[data-tab="review"]');
    if (!tabBtn) { fails.push('no "Review" tab (SpaceLab.addTab("review", "Review", ...))'); return report(5, 'Planet or not?', fails); }
    const v0 = await env.tab('review');
    const v = v0 && v0.querySelector('.sl-rev');
    if (!v) { fails.push('put the planet review inside a container with class sl-rev (it stays; its content changes)'); return report(5, 'Planet or not?', fails); }
    const first = v.querySelector('.sl-cand');
    if (!first) { fails.push('the candidate shown must be an element with class sl-cand and data-tic="<tic>"'); return report(5, 'Planet or not?', fails); }
    const tic1 = first.dataset.tic;
    if (!v.querySelector('.sl-cand img')) fails.push('show the candidate\'s plot image');
    const answers = [...v.querySelectorAll('button.sl-answer')];
    if (answers.length !== 4) fails.push(`expected 4 answer buttons (button.sl-answer), found ${answers.length}`);
    if (!answers.length) return report(5, 'Planet or not?', fails);
    answers[0].click(); await sleep(5);
    const c1 = FIXTURE.tess.cands.find(c => String(c.tic) === String(tic1));
    if (c1 && c1.ai && !v.textContent.toLowerCase().includes(c1.ai.verdict.toLowerCase())) fails.push('after answering, show what the AI said (ai.verdict)');
    if (/checks agree|AI agrees|agrees? with you/i.test(v.textContent) && c1 && c1.ai) fails.push('do not claim the checks "agree" with the visitor; just show what they said');
    const link = v.querySelector('a[href*="issues/new"]');
    if (!link) fails.push('after answering, offer a "Send my review" link to GitHub');
    else {
      const u = new URL(link.href);
      if (u.searchParams.get('template') !== 'candidate-review.yml') fails.push('the link must use template=candidate-review.yml');
      if (u.searchParams.get('tic') !== String(tic1)) fails.push(`the link must include tic=${tic1}`);
      if (!u.searchParams.get('verdict')) fails.push('the link must include verdict=<the answer, e.g. Planet-like>');
      if (link.target !== '_blank') fails.push('open the GitHub link in a new tab (target=_blank, rel=noopener)');
      if (!/GitHub account/i.test(v.textContent)) fails.push('mention that sending needs a free GitHub account');
    }
    const next = v.querySelector('button.sl-next');
    if (!next) fails.push('after answering, show a "Next candidate" button (button.sl-next)');
    else { next.click(); await sleep(5); const c2 = v.querySelector('.sl-cand'); if (!c2 || c2.dataset.tic === tic1) fails.push('"Next" must show a different candidate'); }
    let saved = []; try { saved = JSON.parse(env.w.localStorage.getItem('sl_reviews') || '[]'); } catch (_) {}
    if (!saved.some(r => String(r.tic) === String(tic1))) fails.push('save answers in localStorage sl_reviews as a JSON list of {tic, answer}');
    const env2 = await boot({ storage: { sl_reviews: env.w.localStorage.getItem('sl_reviews') || '[]' } });
    const v2 = await env2.tab('review');
    const again = v2 && v2.querySelector('.sl-rev .sl-cand');
    if (again && again.dataset.tic === tic1) fails.push('candidates already reviewed must be skipped next time');
    report(5, 'Planet or not? review game', fails);
  },

  async 6() {
    const fails = [], env = await boot();
    const titles = env.$$('nav.tabs button').map(b => norm(b.textContent));
    if (titles.filter(t => /^asteroids$/i.test(t)).length > 1) fails.push('do not add a second tab called "Asteroids"; put the asteroid review inside the Review tab');
    const v = await env.tab('review');
    const box = v && v.querySelector('.sl-ast');
    if (!box) { fails.push('Review tab: no asteroid section (class sl-ast, with data-id="<id>")'); return report(6, 'Is the asteroid there?', fails); }
    if (!box.querySelector('img[src$=".gif"]')) fails.push('show the animated gif of the sighting');
    const ans = [...box.querySelectorAll('button.sl-answer')];
    if (ans.length !== 4) fails.push(`expected 4 answer buttons in the asteroid section, found ${ans.length}`);
    if (ans.length) {
      const id = box.dataset.id; ans[0].click(); await sleep(5);
      const link = v.querySelector('.sl-ast a[href*="issues/new"]');
      if (!link) fails.push('after answering, offer a "Send my review" link to GitHub');
      else {
        const u = new URL(link.href);
        if (u.searchParams.get('template') !== 'asteroid-review.yml') fails.push('the link must use template=asteroid-review.yml');
        const c = FIXTURE.asteroids.cands.find(x => String(x.id) === String(id));
        if (!c || !(u.searchParams.get('candidate') || '').includes(c.des)) fails.push('the link must include candidate=<designation, night, archive (#id)>');
      }
    }
    const empty = JSON.parse(JSON.stringify(FIXTURE)); empty.asteroids.cands = [];
    const env2 = await boot({ data: empty });
    const v2 = await env2.tab('review');
    if (!v2 || !/no sightings waiting/i.test(v2.textContent)) fails.push('with no sightings, say "No sightings waiting right now"');
    report(6, 'Is the asteroid there?', fails);
  },

  async 7() {
    const fails = [], env = await boot();
    let v = (await env.tab('practice')) || null;
    let game = env.$('.sl-practice');
    if (!game) { fails.push('no practice game (element with class sl-practice and data-true-period, in a "practice" or "review" tab)'); return report(7, 'Find-the-period game', fails); }
    const slider = game.querySelector('input[type=range]'), status = game.querySelector('.sl-status'), newBtn = game.querySelector('button.sl-new');
    if (!game.querySelector('canvas')) fails.push('draw the light curve on a <canvas>');
    if (!slider || !status || !newBtn) { fails.push('need input[type=range], a .sl-status line and a button.sl-new ("New star")'); return report(7, 'Find-the-period game', fails); }
    if (!/made-up|practice/i.test(game.textContent)) fails.push('say clearly that this is practice with made-up data');
    if (/found/i.test(status.textContent)) fails.push('the answer must not be shown before the visitor tries');
    const P = parseFloat(game.dataset.truePeriod);
    if (!(P >= 1 && P <= 8)) fails.push(`data-true-period should be the true period between 1 and 8 days (got ${game.dataset.truePeriod})`);
    const setP = async p => { slider.value = String(p); slider.dispatchEvent(new env.w.Event('input', { bubbles: true })); await sleep(2); };
    await setP(Math.min(10, P * 1.37)); if (/found it/i.test(status.textContent)) fails.push('a wrong period must not say "found it"');
    await setP(P); if (!/found it/i.test(status.textContent)) fails.push('the true period must say "You found it"');
    newBtn.click(); await sleep(5);
    game = env.$('.sl-practice');
    const P2 = parseFloat(game.dataset.truePeriod);
    if (P2 === P) fails.push('"New star" must make a new star with a new period');
    if (/found it/i.test(game.querySelector('.sl-status').textContent)) fails.push('"New star" must not reveal the new answer');
    report(7, 'Find-the-period game', fails);
  },

  async 8() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    const btn = tess.querySelector('#tic-222222 button.sl-postcard');
    if (!btn) { fails.push('no Postcard button (button.sl-postcard) on periodic candidates'); return report(8, 'Postcard', fails); }
    if (/�/.test(btn.textContent) || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])/.test(btn.textContent)) fails.push('the button label has a broken emoji');
    btn.click(); await sleep(5);
    const d = env.rec.downloads[env.rec.downloads.length - 1];
    if (!d) fails.push('clicking must download a PNG (a link with download="...png")');
    else if (!d.name.includes('222222')) fails.push(`clicked TIC 222222's postcard but got "${d.name}" (each button must use its own candidate)`);
    const txt = env.rec.canvasText.join(' | ');
    if (!/candidate/i.test(txt)) fails.push('the postcard text must say "candidate"');
    if (!txt.includes('222222')) fails.push('the postcard must show the TIC number of the candidate clicked');
    report(8, 'Postcard', fails);
  },

  async 9() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    const s1 = tess.querySelector('#tic-111111 .sl-fav'), s2 = tess.querySelector('#tic-222222 .sl-fav');
    if (!s1 || !s2) { fails.push('each TESS card needs a favorite star (class sl-fav, a <button>)'); return report(9, 'My list', fails); }
    s1.click(); await sleep(5);
    if (!s1.classList.contains('on')) fails.push('clicking a star must turn it on (class "on") on THAT card');
    if (s2.classList.contains('on')) fails.push('clicking card 111111\'s star turned on another card\'s star');
    let favs = []; try { favs = JSON.parse(env.w.localStorage.getItem('sl_favs') || '[]').map(String); } catch (_) {}
    if (!favs.includes('111111')) fails.push('save favorites in localStorage sl_favs (JSON list of TIC numbers)');
    const tabBtn = env.$('nav.tabs button[data-tab="mylist"]');
    if (!tabBtn) fails.push('add a "My list" tab with SpaceLab.addTab("mylist", "My list", ...)');
    else { const v = await env.tab('mylist'); if (!v.textContent.includes('111111')) fails.push('the My list tab must show the saved TIC numbers'); }
    report(9, 'My list', fails);
  },

  async 10() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    const btn = tess.querySelector('#tic-222222 button.sl-walk');
    if (!btn) { fails.push('no "Walk me through it" button (button.sl-walk)'); return report(10, 'Walkthrough', fails); }
    btn.click(); await sleep(5);
    const panel = tess.querySelector('#tic-222222 .sl-walk-panel');
    if (!panel) fails.push('clicking must open a .sl-walk-panel inside that card');
    else {
      const steps = panel.querySelectorAll('.sl-walk-step');
      if (steps.length !== 5) fails.push(`expected 5 steps (.sl-walk-step), found ${steps.length}`);
      if (!panel.querySelector('img[src*="222222"]')) fails.push('show this candidate\'s own plot image in the walkthrough');
    }
    report(10, 'How we checked this signal', fails);
  },

  async 11() {
    const fails = [], env = await boot();
    if (env.$('nav.tabs .sl-badges, nav.tabs [class*="badge"]')) fails.push('do not put badges inside nav.tabs');
    const v = await env.tab('review');
    const a = v && v.querySelector('.sl-rev button.sl-answer');
    if (a) { a.click(); await sleep(5); }
    let b = {}; try { b = JSON.parse(env.w.localStorage.getItem('sl_badges') || '{}'); } catch (_) {}
    if (!b.first_review) fails.push('after the first review, sl_badges must contain first_review');
    const env2 = await boot({ storage: { sl_reviews: env.w.localStorage.getItem('sl_reviews') || '[]', sl_badges: env.w.localStorage.getItem('sl_badges') || '{}' } });
    const v2 = await env2.tab('review');
    if (!v2 || !v2.querySelector('.sl-badge.earned')) fails.push('show earned badges (.sl-badge.earned) in the Review tab');
    report(11, 'Personal badges', fails);
  },
};

(async () => {
  const order = ['G', '1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11'];
  for (const k of order) {
    if (ONLY && k !== ONLY && k !== 'G') continue;
    try { await CHECKS[k](); } catch (e) { report(k, 'check crashed', ['the check itself crashed (usually because an expected element is missing): ' + e.message]); }
  }
  let failed = 0;
  for (const r of results) {
    const label = r.task === 'G' ? 'General' : 'Task ' + r.task;
    if (r.fails.length) { failed++; console.log(`FAIL  ${label}: ${r.name}`); r.fails.forEach(f => console.log('      - ' + f)); }
    else console.log(`ok    ${label}: ${r.name}`);
  }
  console.log(failed ? `\n${failed} of ${results.length} groups need work.` : `\nAll ${results.length} groups passed.`);
  process.exit(failed ? 1 : 0);
})();
