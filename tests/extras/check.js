// Automatic checks for web/extras.js and web/beyond.js (ROADMAP tasks), run in a simulated browser (jsdom) with fake data.
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
const ANSWER_SLUGS = { 'Planet-like': 'planet-like', 'Eclipsing binary (two stars)': 'eclipsing-binary', 'Comes from a nearby star': 'nearby-star', 'Noise / instrument glitch': 'noise', 'Not sure': 'not-sure' };

async function boot({ extras = true, storage = {}, data = FIXTURE, hash = '', search = '', geo = null } = {}) {
  const html = read('web/public.html').replace(/<script src=[^>]+><\/script>/g, '').replace(/<link rel="stylesheet"[^>]*>/g, '');
  const rec = { errors: [], opened: [], alerts: 0, downloads: [], audio: [], canvasText: [] };
  const vc = new VirtualConsole();
  vc.on('jsdomError', e => { const m = String(e && (e.detail && e.detail.message || e.message) || e); if (!/Not implemented/.test(m)) rec.errors.push('uncaught: ' + m.slice(0, 200)); });
  const dom = new JSDOM(html, { runScripts: 'outside-only', url: 'https://example.github.io/Space-Lab/' + search + hash, pretendToBeVisual: true, virtualConsole: vc });
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
  Object.defineProperty(w.navigator, 'geolocation', { configurable: true, value: geo ? { getCurrentPosition: ok => ok({ coords: { latitude: geo[0], longitude: geo[1] } }) } : undefined });
  w.HTMLElement.prototype.scrollIntoView = function () {};
  const style = w.document.createElement('style');
  style.textContent = read('web/style.css') + '\n' + (extras ? read('web/extras.css') : '');
  w.document.head.appendChild(style);
  const json = JSON.parse(JSON.stringify(data));
  if (json.beyond && json.beyond.iss) json.beyond.iss.t0 = Math.floor(Date.now() / 1000) - 60;   // the fixture's ISS path starts "now"
  const ZIPS = '12345 4000 -10500\n10001 4075 -7400\n';
  w.fetch = async u => (/zip\.txt$/.test(String(u)) ? { ok: true, text: async () => ZIPS } : { ok: true, json: async () => json, text: async () => JSON.stringify(json) });
  w.eval(read('web/public.js'));
  if (extras) {
    for (const f of ['web/beyond.js', 'web/extras.js', 'web/home.js', 'web/stargazing.js']) {
      try { w.eval(read(f)); } catch (e) { rec.errors.push(f + ' crashed while loading: ' + e.message); }
    }
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
      for (const el of [...view.querySelectorAll('button[class*="sl-"], a[class*="sl-"], [class*="sl-"] button, [class*="sl-"][role=button], .sl-fav, button[class*="bx-"], [class*="bx-"] button, button[class*="hm-"], [class*="hm-"] button, button[class*="sg-"], [class*="sg-"] button')]) {
        if (!el.isConnected) continue;
        el.click(); await sleep(5);
        if (env.visible().length !== 1 || /undefined/.test(env.w.location.hash)) { fails.push(`clicking "${norm(el.textContent).slice(0, 30)}" in tab ${t} broke the page`); break; }
      }
    }
    // Links out are fine (they open in a new tab when clicked); loading anything from another site is not.
    const LINKS_OK = ['https://github.com/TechAmatuer/Space-Lab', 'https://techamatuer.github.io/Space-Lab/', 'https://sky.esa.int/esasky/', 'http://www.w3.org/2000/svg',
                      'https://www.openstreetmap.org/', 'https://www.lightpollutionmap.info/', 'https://calendar.google.com/calendar/'];
    for (const f of ['web/extras.js', 'web/beyond.js', 'web/home.js', 'web/stargazing.js']) {
      const src = read(f);
      const urls = (src.match(/https?:\/\/[^\s'"`)]+/g) || []).filter(u => !LINKS_OK.some(ok => u.startsWith(ok)));
      if (urls.length) fails.push(f + ' may only link to the project, its page and ESASky; found: ' + urls.join(', '));
      if (/\balert\(|\bconfirm\(|\bprompt\(/.test(src)) fails.push(f + ': do not use alert/confirm/prompt; show messages inside the page');
      if (/\bfetch\(|XMLHttpRequest|import\(|new Image\(\)\.src\s*=\s*['"`]http|sendBeacon|WebSocket/.test(src)) fails.push(f + ' must not load anything from the internet (no fetch/XMLHttpRequest/beacons)');
    }
    const keys = Object.keys(env.w.localStorage);
    const bad = keys.filter(k => !/^sl_[a-z0-9_]+$/.test(k) || k.startsWith('sl_sl_'));
    for (const c of env.$$('[class*="bx-"] .chip, .bx-dot')) if (/undefined|NaN/.test(c.textContent + (c.getAttribute('style') || ''))) fails.push('a Sky watch / Beyond element shows undefined or NaN: ' + norm(c.outerHTML).slice(0, 120));
    if (bad.length) fails.push('localStorage keys must look like sl_name (not sl_sl_...): ' + bad.join(', '));
    if (env.rec.alerts) fails.push(`alert/confirm was called ${env.rec.alerts} times`);
    const errs = env.rec.errors.filter(e => !/Not implemented: (navigation|HTMLMediaElement)/.test(e));
    if (errs.length) fails.push('errors in the browser console:\n      ' + errs.slice(0, 6).join('\n      '));
    report('G', 'whole page: no crashes, nothing broken, nothing external', fails);
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

  async 5() {
    const fails = [], env = await boot();
    const tabBtn = env.$('nav.tabs button[data-tab="play"]');
    if (!tabBtn) { fails.push('no "Play & review" tab (SpaceLab.addTab("play", ...))'); return report(5, 'Planet or not?', fails); }
    const v0 = await env.tab('play');
    const v = v0 && v0.querySelector('.sl-rev');
    if (!v) { fails.push('put the planet review inside a container with class sl-rev (it stays; its content changes)'); return report(5, 'Planet or not?', fails); }
    const first = v.querySelector('.sl-cand');
    if (!first) { fails.push('the candidate shown must be an element with class sl-cand and data-tic="<tic>"'); return report(5, 'Planet or not?', fails); }
    const tic1 = first.dataset.tic;
    if (!v.querySelector('.sl-cand img')) fails.push('show the candidate\'s plot image');
    const answers = [...v.querySelectorAll('button.sl-answer')];
    const yml = read('.github/ISSUE_TEMPLATE/candidate-review.yml'), opts = JSON.parse((yml.match(/options: (\[.*\])/) || [0, '[]'])[1]);
    const got = answers.map(a => norm(a.textContent));
    if (JSON.stringify(got) !== JSON.stringify(opts)) fails.push(`the answer buttons must be exactly the GitHub form's options so the form can be prefilled: ${JSON.stringify(opts)} (got ${JSON.stringify(got)})`);
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
    const v2 = await env2.tab('play');
    const again = v2 && v2.querySelector('.sl-rev .sl-cand');
    if (again && again.dataset.tic === tic1) fails.push('candidates already reviewed must be skipped next time');
    report(5, 'Planet or not? review game', fails);
  },

  async 6() {
    const fails = [], env = await boot();
    const titles = env.$$('nav.tabs button').map(b => norm(b.textContent));
    if (titles.filter(t => /^asteroids$/i.test(t)).length > 1) fails.push('do not add a second tab called "Asteroids"; put the asteroid review inside the Play tab');
    const v = await env.tab('play');
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
    const v2 = await env2.tab('play');
    if (!v2 || !/no sightings waiting/i.test(v2.textContent)) fails.push('with no sightings, say "No sightings waiting right now"');
    report(6, 'Is the asteroid there?', fails);
  },

  async 7() {
    const fails = [], env = await boot();
    await env.tab('play');
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
    if (!tabBtn) fails.push('add a "My lab" tab with SpaceLab.addTab("mylist", "My lab", ...)');
    else { const v = await env.tab('mylist'); if (!v.textContent.includes('111111')) fails.push('the My list tab must show the saved TIC numbers'); }
    report(9, 'My list', fails);
  },

  async 11() {
    const fails = [], env = await boot();
    if (env.$('nav.tabs .sl-badges, nav.tabs [class*="badge"]')) fails.push('do not put badges inside nav.tabs');
    const v = await env.tab('play');
    const a = v && v.querySelector('.sl-rev button.sl-answer');
    if (a) { a.click(); await sleep(5); }
    let b = {}; try { b = JSON.parse(env.w.localStorage.getItem('sl_badges') || '{}'); } catch (_) {}
    if (!b.first_review) fails.push('after the first review, sl_badges must contain first_review');
    const env2 = await boot({ storage: { sl_reviews: env.w.localStorage.getItem('sl_reviews') || '[]', sl_badges: env.w.localStorage.getItem('sl_badges') || '{}' } });
    const v2 = await env2.tab('mylist');
    if (!v2 || !v2.querySelector('.sl-badge.earned')) fails.push('show earned badges (.sl-badge.earned) in the My lab tab');
    report(11, 'Personal badges', fails);
  },
  async 12() {
    const fails = [], env = await boot();
    const v = await env.tab('sky');
    if (!v) { fails.push('no "Sky watch" tab (SpaceLab.addTab("sky", ...) in web/beyond.js)'); return report(12, 'Sky watch', fails); }
    const B = FIXTURE.beyond;
    if (!v.querySelector('svg.bx-moon')) fails.push('draw the Moon phase (svg.bx-moon)');
    if (!v.textContent.includes(B.sky.moon.phase)) fails.push('say the Moon phase');
    if (!/Leonids/.test(v.textContent)) fails.push('list upcoming meteor showers');
    if (v.querySelectorAll('.bx-kp').length !== B.space_weather.kp.length) fails.push('one Kp bar per 3-hour value');
    if (!v.querySelector('.bx-kp[data-lvl=storm]')) fails.push('Kp 5 or more must be marked as a storm');
    if (!v.querySelector('svg.bx-line path')) fails.push('draw the X-ray light curve');
    const sun = v.querySelector('.bx-sun');
    if (!sun) fails.push('no "Listen to the Sun" button'); else { sun.click(); await sleep(5); if (!env.rec.audio.length) fails.push('Listen to the Sun must make sound'); sun.click(); }
    for (const t of ['C/2026 A1', 'S261101ab', 'ZTF26aaaaaaa']) if (!v.textContent.includes(t)) fails.push(`show ${t} from the data`);
    const iss = v.querySelector('.bx-issgo');
    if (!iss) fails.push('no ISS pass button'); else { iss.click(); await sleep(5); if (!/cannot share|not shared/i.test(v.querySelector('.bx-issout').textContent)) fails.push('without location access, say so (nothing else)'); }
    const env2 = await boot({ geo: [40, -100] });
    const v2 = await env2.tab('sky');
    v2.querySelector('.bx-issgo').click(); await sleep(20);
    const out = v2.querySelector('.bx-issout');
    if (!out.querySelector('table') && !/No passes/i.test(out.textContent)) fails.push('with a location, list ISS passes in a table');
    if (env2.rec.opened.length) fails.push('working out ISS passes must not open or send anything');
    report(12, 'Sky watch (Moon, ISS, space weather, fireballs, comets, waves, supernovae)', fails);
  },

  async 13() {
    const fails = [], env = await boot();
    const v = await env.tab('beyond');
    if (!v) { fails.push('no "Beyond astronomy" tab'); return report(13, 'Beyond astronomy', fails); }
    for (const id of ['bx-bio', 'bx-chem', 'bx-eng', 'bx-earth']) if (!v.querySelector('#' + id)) fails.push(`missing section #${id}`);
    if (!v.textContent.includes('Example-1 e')) fails.push('list habitable worlds from the data');
    const btn = [...v.querySelectorAll('.bx-els button')].find(b => b.dataset.el === 'Na');
    if (!btn) fails.push('spectrum: no element buttons'); else {
      btn.click(); await sleep(2);
      const hl = [...v.querySelectorAll('.bx-spec i.hl')];
      if (hl.length !== 2 || hl.some(i => i.dataset.el !== 'Na')) fails.push(`tapping Sodium must highlight its 2 lines (got ${hl.length})`);
      const left = parseFloat(hl[0] && hl[0].style.left);
      if (!(Math.abs(left - (589.0 - 380) / 4) < 0.2)) fails.push('sodium lines must sit at 589 nm on the 380-780 nm scale');
    }
    const r = v.querySelector('.bx-rocket .bx-rout');
    const dv = r && parseFloat((r.textContent.match(/([\d.]+) km\/s/) || [])[1]);
    const want = 348 * 9.80665 * Math.log(125 / 14) / 1000;
    if (!(Math.abs(dv - want) < 0.01)) fails.push(`rocket equation: default stage should give ${want.toFixed(2)} km/s, got ${dv}`);
    const o = v.querySelector('.bx-orbit .bx-oout');
    if (!o || !/92\.\d minutes/.test(o.textContent)) fails.push('orbit calculator: the ISS at 420 km should take about 92.7 minutes per lap');
    const geo = v.querySelector('.bx-preset[data-alt="35786"]');
    if (geo) { geo.click(); await sleep(2); if (!/23\.9\d hours/.test(o.textContent)) fails.push('geostationary orbit must take about 23.93 hours'); }
    if (!v.querySelector('.bx-launch [data-net]')) fails.push('upcoming launches need a countdown');
    if (!v.querySelector('.bx-map .bx-dot.quake')) fails.push('plot earthquakes on the map');
    if (!v.querySelector('img[src$="earth-day.jpg"]')) fails.push('show the Earth image from the data');
    report(13, 'Beyond astronomy (biology, chemistry, engineering, Earth)', fails);
  },

  async 14() {
    const fails = [], env = await boot({ search: '?card=111111' });
    await sleep(20);
    const main = env.$('main');
    const cards = main.querySelectorAll('.card');
    if (cards.length !== 1 || !/TIC 111111/.test(main.textContent)) fails.push('?card=<TIC> must show just that candidate\'s card');
    if (!env.w.document.body.classList.contains('sl-embedded')) fails.push('embedded mode must add body.sl-embedded (hides the header)');
    const env2 = await boot();
    const tess = await env2.tab('tess');
    const sh = tess.querySelector('#tic-111111 .sl-share');
    if (sh) { sh.click(); await sleep(2); const ta = tess.querySelectorAll('#tic-111111 .sl-sharebox textarea');
      if (ta.length < 2 || !/\?card=111111/.test(ta[1].value)) fails.push('the share box must offer an embed snippet using ?card=<TIC>');
      if (!/candidate/i.test(ta[0] && ta[0].value)) fails.push('share text must call it a candidate'); }
    else fails.push('no Share button (.sl-share)');
    report(14, 'Share and embed', fails);
  },

  async 15() {
    const fails = [], env = await boot();
    const tess = await env.tab('tess');
    tess.querySelector('#tic-111111 .sl-fav').click(); await sleep(2);
    const c = await env.tab('create');
    for (const [cls, name] of [['.sl-mkart', 'art'], ['.sl-mkwall', 'wallpaper']]) {
      const b = c.querySelector(cls); if (!b) { fails.push('Create tab: no ' + name + ' button'); continue; }
      const n = env.rec.downloads.length; b.click(); await sleep(2);
      if (env.rec.downloads.length !== n + 1) fails.push(name + ' must download a PNG');
    }
    if (!c.querySelector('.sl-glow')) fails.push('show what the stars look like (.sl-glow)');
    if (!/Big Bang/.test(c.textContent)) fails.push('cosmic calendar missing');
    let v = await env.tab('mylist');
    const note = v.querySelector('textarea[data-note="111111"]');
    if (!note) fails.push('saved candidates need a notes box'); else { note.value = 'check sector 40'; note.dispatchEvent(new env.w.Event('input')); }
    const notes = JSON.parse(env.w.localStorage.getItem('sl_notes') || '{}');
    if (notes['111111'] !== 'check sector 40') fails.push('notes must be saved in sl_notes');
    const changed = JSON.parse(JSON.stringify(FIXTURE)); changed.tess.cands[0].status = 'real';
    const env2 = await boot({ data: changed, storage: { sl_favs: '["111111"]', sl_fav_snap: env.w.localStorage.getItem('sl_fav_snap') } });
    v = await env2.tab('mylist');
    if (!v.querySelector('.sl-changed')) fails.push('a saved candidate whose status changed must show a 🔔 change note');
    const go = v.querySelector('.sl-locate');
    if (go) { go.click(); await sleep(2); if (!/cannot share/i.test(v.querySelector('.sl-pout').textContent)) fails.push('sky pointer without location must say so'); }
    else fails.push('no sky pointer');
    const env3 = await boot({ geo: [40, -100] });
    v = await env3.tab('mylist');
    v.querySelector('.sl-locate').click(); await sleep(5);
    if (!/above the horizon|below your horizon/.test(v.querySelector('.sl-pout').textContent)) fails.push('sky pointer must give a direction and height');
    report(15, 'Create and My lab', fails);
  },

  async 16() {
    const fails = [], env = await boot();
    const a = await env.tab('asteroids');
    const st = a.querySelector('.card[data-pc="7"] .sl-story');
    if (!st) fails.push('each old sighting needs its story (.sl-story inside .card[data-pc])');
    else if (!/2019-04-12/.test(st.textContent) || !/Pan-STARRS/.test(st.textContent)) fails.push('the story must use that sighting\'s night and survey');
    if (a.querySelectorAll('.sl-story').length !== FIXTURE.asteroids.cands.length) fails.push('one story per sighting');
    const p = await env.tab('play');
    if (!/LD/.test(p.querySelector('.sl-race').textContent) || p.querySelectorAll('.sl-race tr').length < 3) fails.push('asteroid race timer must list the close approaches');
    report(16, 'Asteroid stories and race timer', fails);
  },
  async 17() {
    const fails = [], env = await boot();
    env.w.goatcounter = { count: o => env.rec.opened.push('count:' + o.path + (o.event ? ':event' : '')) };
    const v = await env.tab('today');
    const daily = v.querySelector('#today-daily');
    if (!daily || !/TIC 222222/.test(daily.textContent)) { fails.push('Home must show the candidate of the day (TIC 222222 in the fixture) in #today-daily'); return report(17, 'Home and candidate of the day', fails); }
    const votes = [...daily.querySelectorAll('button.hm-vote')];
    if (votes.map(b => norm(b.textContent)).join('|') !== Object.keys(ANSWER_SLUGS).join('|')) fails.push('vote buttons must match the GitHub review form answers');
    if (/Votes so far/.test(daily.textContent)) fails.push('do not show today\'s votes before the visitor has voted');
    if (!/Yesterday: TIC 111111/.test(daily.textContent) || !daily.querySelector('.hm-bar')) fails.push('show yesterday\'s candidate with a vote chart');
    if (!/not a planet/.test(daily.textContent)) fails.push('show the volunteer\'s verdict on yesterday\'s candidate');
    votes[0] && votes[0].click(); await sleep(5);
    const d2 = v.querySelector('#today-daily');
    if (!/Your vote: Planet-like/.test(d2.textContent)) fails.push('after voting, confirm the vote');
    if (!env.rec.opened.includes('count:vote-2026-11-09-planet-like:event')) fails.push('a vote must be counted as event vote-<date>-<answer> (got ' + env.rec.opened.filter(x => x.startsWith('count:')).join(', ') + ')');
    if (!/planet-like/.test(d2.textContent) || !/Votes so far/.test(d2.textContent)) fails.push('after voting, show the AI verdict and the votes so far');
    const saved = JSON.parse(env.w.localStorage.getItem('sl_daily') || '{}');
    if (saved['2026-11-09'] !== 'Planet-like') fails.push('save the daily vote in sl_daily');
    if (!/Daily streak: 1/.test(d2.textContent)) fails.push('show the daily streak');
    if (!v.querySelector('.hm-radar') || !/Webb spots water/.test(v.textContent)) fails.push('Home must show the top radar items');
    if (!/Moon:/.test(v.textContent)) fails.push('Home must show tonight\'s sky summary');
    if (!/1<\/b><span>⏱ Transit timings/.test(v.innerHTML)) fails.push('Home must count results by type');
    const env2 = await boot({ storage: { sl_daily: JSON.stringify({ '2026-11-08': 'Not sure', '2026-11-07': 'Not sure' }) } });
    const v2 = await env2.tab('today');
    if (!/Your vote/.test(v2.querySelector('#today-daily').textContent)) {
      v2.querySelector('#today-daily button.hm-vote').click(); await sleep(5);
      if (!/Daily streak: 3/.test(v2.querySelector('#today-daily').textContent)) fails.push('voting on 3 days in a row must show a streak of 3');
    }
    const groups = env.$$('nav.tabs .navgroup').map(g => g.textContent).join('|');
    if (groups !== 'Explore|Research|Join in') fails.push('tabs must be grouped Explore | Research | Join in (got ' + groups + ')');
    const order = env.$$('nav.tabs button').map(b => b.dataset.tab).join(' ');
    if (order !== 'today sky stargazing brief beyond tess asteroids exowatch results play create mylist about') fails.push('tab order: ' + order);
    report(17, 'Home, candidate of the day and navigation', fails);
  },

  async 18() {
    const fails = [], env = await boot();
    const r = await env.tab('results');
    if (!r || !/Transit of WASP-12 b/.test(r.textContent) || !/accepted/.test(r.textContent)) fails.push('Results tab must list the results with their status');
    if (r && r.querySelector('.hm-owner')) fails.push('the public page must not show the owner\'s editing form');
    const b = await env.tab('brief');
    const card = b.querySelector('.hm-radar-card');
    if (!card) fails.push('Space brief must start with the radar card'); else {
      if (card.querySelectorAll('.hm-radar-item').length !== 3) fails.push('show all radar items in the Space brief');
      const sun = [...card.querySelectorAll('.hm-topic')].find(x => x.dataset.topic === 'Sun & space weather');
      if (sun) { sun.click(); await sleep(2); const shown = [...card.querySelectorAll('.hm-radar-item')].filter(li => li.style.display !== 'none');
        if (shown.length !== 1) fails.push('topic buttons must filter the radar'); } else fails.push('radar topic filter missing');
      if (/Feeds that failed/.test(card.textContent)) fails.push('failed feeds are for the owner (Meridian) only');
      if (!/about this lab/.test(card.textContent)) fails.push('mark radar items about this lab\'s own targets');
    }
    const env2 = await boot({ hash: '#tic-222222' });
    await sleep(400);
    if (!env2.$('#v-tess.on')) fails.push('a #tic-<TIC> link must open the TESS tab');
    if (!env2.$('#tic-222222.flash')) fails.push('a #tic-<TIC> link must highlight that candidate');
    const tess = await env.tab('tess');
    const sh = tess.querySelector('#tic-111111 .sl-share'); sh.click(); await sleep(2);
    if (!/https:\/\/example\.github\.io\/Space-Lab\/c\/111111\.html/.test(tess.querySelector('#tic-111111 .sl-sharebox textarea').value)) fails.push('share text must link to the candidate\'s own page c/<TIC>.html on the site address from data.site');
    report(18, 'Results, radar, deep links and share pages', fails);
  },
  async 19() {
    const fails = [], env = await boot();
    const v = await env.tab('stargazing');
    if (!v) { fails.push('no Stargazing tab'); return report(19, 'Stargazing tools', fails); }
    const zip = v.querySelector('.sg-zip');
    zip.value = '99999'; v.querySelector('.sg-setzip').click(); await sleep(20);
    if (!/not found/.test(v.querySelector('.sg-placeout').textContent)) fails.push('an unknown ZIP code must say "not found"');
    v.querySelector('.sg-zip').value = '12345'; v.querySelector('.sg-setzip').click(); await sleep(20);
    const pl = JSON.parse(env.w.localStorage.getItem('sl_place') || 'null');
    if (!pl || Math.abs(pl.lat - 40.0) > 0.01 || Math.abs(pl.lon + 105.0) > 0.01) fails.push('ZIP 12345 must set sl_place to 40.0, -105.0 (got ' + JSON.stringify(pl) + ')');
    if (!/ZIP 12345/.test(v.querySelector('.sg-placeout').textContent)) fails.push('confirm the place that is being used');
    if (env.rec.opened.length) fails.push('looking up a ZIP code must not open or send anything');
    const ev = v.querySelector('.sg-ev');
    if (ev.querySelectorAll('.sg-events li').length !== FIXTURE.beyond.events.length) fails.push('list every upcoming event');
    const sub = ev.querySelector('a[href^="webcal:"]');
    if (!sub || sub.getAttribute('href') !== 'webcal://example.github.io/Space-Lab/events.ics') fails.push('offer a calendar subscription to webcal://<site>/events.ics');
    const met = [...ev.querySelectorAll('.sg-kind')].find(b => b.dataset.k === 'meteor'); met.click(); await sleep(2);
    if (ev.querySelectorAll('.sg-events li').length !== 2) fails.push('the meteor filter must show only the 2 showers');
    ev.querySelector('.sg-remind').click(); await sleep(2);
    const dl = env.rec.downloads[env.rec.downloads.length - 1];
    const ics = dl ? decodeURIComponent(dl.href.split(',')[1]) : '';
    if (!/BEGIN:VCALENDAR/.test(ics) || !/DTSTART;VALUE=DATE:20991117/.test(ics) || !/BEGIN:VALARM/.test(ics)) fails.push('"Remind me" must download a calendar file with the date and an alarm');
    const SG = env.w.SpaceLabStargazing, place = { lat: 40.0, lon: -105.0 };
    let when = null;
    for (let h = 0; h < 48 && !when; h++) { const t = new Date(Date.UTC(2026, 10, 9, h)); const [alt] = SG.altAz(200.12, -11.34, place.lat, place.lon, t); if (alt > 5 && alt < 25 && SG.sunAlt(place.lat, place.lon, t) < -6) when = t; }
    if (!when) fails.push('test setup: no time found when Venus is low and the sky is dark');
    if (when) {
      const [, az] = SG.altAz(200.12, -11.34, place.lat, place.lon, when);
      const dir = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.round(az / 45) % 8];
      const r = SG.identify(place, when, dir, 'low', 'still', 'bright');
      if (!r.length || !/Venus/.test(r[0].name)) fails.push(`a very bright, still light low in the ${dir} when Venus is there must be identified as Venus (got ${r.map(x => x.name).join(', ')})`);
    }
    const plane = SG.identify(place, new Date(), 'N', 'mid', 'steady', 'redgreen');
    if (!/aircraft/i.test(plane[0].name)) fails.push('red and green lights must be identified as an aircraft');
    const met2 = SG.identify(place, new Date(), 'N', 'mid', 'streak', 'steady');
    if (!/meteor/i.test(met2[0].name)) fails.push('a fast streak must be identified as a meteor');
    const ds = { bbox: [24, -125, 50, -66], w: 118, h: 52, cdf: FIXTURE.beyond.darksky.cdf, lo: -3, hi: 1 };
    const grid = new Uint8Array(118 * 52).fill(200);
    const at = (la, lo) => Math.round((50 - la) / 26 * 52) * 118 + Math.round((lo + 125) / 59 * 118);
    grid[at(40.5, -106.3)] = 20;                 // a dark spot ~120 km west-north-west of the place
    for (let i = 0; i < 118; i++) grid[51 * 118 + i] = 255;   // water along the bottom edge
    const res = SG.darkSpots(ds, grid, place, 150);
    if (!res || !res.found.length || Math.abs(res.found[0].la - 40.5) > 0.4 || Math.abs(res.found[0].lo + 106.3) > 0.4) fails.push('dark-sky finder must find the dark spot near 40.5N 106.3W (got ' + JSON.stringify(res && res.found.map(f => [f.la.toFixed(2), f.lo.toFixed(2)])) + ')');
    if (SG.darkSpots(ds, grid, { lat: 61, lon: -150 }, 150) !== null) fails.push('places outside the map (e.g. Alaska) must return null');
    const sky = await env.tab('sky');
    sky.querySelector('.bx-issgo').click(); await sleep(20);
    if (/cannot share|not shared/.test(sky.querySelector('.bx-issout').textContent)) fails.push('ISS passes must use the saved place instead of asking for location');
    report(19, 'Stargazing: place, events and reminders, what\'s that, dark skies', fails);
  },

  async 20() {
    const fails = [], env = await boot();
    const p = await env.tab('play');
    if (!/reviewed <b>0<\/b> of <b>3<\/b>/.test(p.querySelector('.sl-rev').innerHTML)) fails.push('show "You\'ve reviewed 0 of 3"');
    for (let i = 0; i < 3; i++) { const a = p.querySelector('.sl-rev button.sl-answer'); if (!a) break; a.click(); await sleep(3); const n = p.querySelector('.sl-next'); if (n) { n.click(); await sleep(3); } }
    if (!/seen them all/.test(p.querySelector('.sl-rev').textContent) || !p.querySelector('.sl-summary')) fails.push('after reviewing everything, celebrate and show what the candidates told us');
    if (p.querySelectorAll('.sl-depthgame .sl-depth').length !== 4) fails.push('estimate-the-depth game needs 4 answers');
    const t = await env.tab('tess');
    if (!t.querySelector('#tic-111111 .sl-reviewed')) fails.push('TESS cards must say "you reviewed this"');
    if (t.querySelectorAll('.hm-funnel rect').length !== 5) fails.push('TESS tab needs the big-picture funnel with 5 steps');
    if (t.querySelectorAll('.hm-next tr').length !== 2) fails.push('TESS tab needs the next predicted dips of the 2 periodic candidates');
    const m = await env.tab('mylist');
    if (!/You're a <b>/.test(m.innerHTML)) fails.push('My lab must show the learning path level');
    const ct = m.querySelector('.sl-contrib');
    if (!ct || !/reviewed 3 TESS planet candidates/.test(ct.value)) fails.push('My lab must offer a shareable summary of what you did');
    report(20, 'Progress, celebration, big picture, path and sharing', fails);
  },
};

(async () => {
  const order = ['G', '2', '3', '5', '6', '7', '8', '9', '11', '12', '13', '14', '15', '16', '17', '18', '19', '20'];
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
