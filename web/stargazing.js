// Space Lab "Stargazing" tab: your place (ZIP code or GPS), the dark-sky finder, upcoming events with calendar
// reminders, and "What's that in the sky?". Runs on the public page AND inside Meridian (see EXTRAS.md).
// A visitor's location is kept only in their own browser (localStorage sl_place) and is never sent anywhere.
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num, D2R = Math.PI / 180;
  const B = () => ((S.data || {}).beyond) || {};
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { if (v === null) localStorage.removeItem(k); else localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* blocked */ } },
  };
  const compass = az => ['north', 'north-east', 'east', 'south-east', 'south', 'south-west', 'west', 'north-west'][Math.round(((az % 360) + 360) % 360 / 45) % 8];
  const DIRS = { N: 0, NE: 45, E: 90, SE: 135, S: 180, SW: 225, W: 270, NW: 315 };
  const place = () => LS.get('sl_place', null);
  const siteUrl = () => (((S.data || {}).site || {}).url) || '';
  const fmtDate = (ev, opt) => {
    const d = ev.t ? new Date(ev.t) : new Date(ev.date + 'T12:00:00Z');
    return ev.t ? d.toLocaleString([], opt || { weekday: 'short', month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  };
  const kmBetween = (a, b, c, d) => {          // great-circle distance in km
    const x = Math.sin((c - a) * D2R / 2) ** 2 + Math.cos(a * D2R) * Math.cos(c * D2R) * Math.sin((d - b) * D2R / 2) ** 2;
    return 12742 * Math.asin(Math.sqrt(x));
  };
  const bearing = (a, b, c, d) => (Math.atan2(Math.sin((d - b) * D2R) * Math.cos(c * D2R),
    Math.cos(a * D2R) * Math.sin(c * D2R) - Math.sin(a * D2R) * Math.cos(c * D2R) * Math.cos((d - b) * D2R)) / D2R + 360) % 360;

  // ------------------------------------------------------------------ sky positions (good to a degree: plenty to point a finger)
  function altAz(ra, dec, lat, lon, date) {
    const d = date.getTime() / 864e5 + 2440587.5 - 2451545;
    const lst = ((280.46061837 + 360.98564736629 * d + lon) % 360 + 360) % 360;
    const H = (lst - ra) * D2R, de = dec * D2R, la = lat * D2R;
    const alt = Math.asin(Math.sin(de) * Math.sin(la) + Math.cos(de) * Math.cos(la) * Math.cos(H));
    const az = Math.atan2(-Math.sin(H) * Math.cos(de), Math.sin(de) * Math.cos(la) - Math.cos(de) * Math.sin(la) * Math.cos(H));
    return [alt / D2R, (az / D2R + 360) % 360];
  }
  function moonRaDec(date) {                   // low-precision Moon (about 1 degree)
    const d = date.getTime() / 864e5 + 2440587.5 - 2451545;
    const L = 218.316 + 13.176396 * d, M = (134.963 + 13.064993 * d) * D2R, F = (93.272 + 13.22935 * d) * D2R;
    const lam = (L + 6.289 * Math.sin(M)) * D2R, beta = 5.128 * Math.sin(F) * D2R, eps = 23.439 * D2R;
    const ra = Math.atan2(Math.sin(lam) * Math.cos(eps) - Math.tan(beta) * Math.sin(eps), Math.cos(lam));
    const dec = Math.asin(Math.sin(beta) * Math.cos(eps) + Math.cos(beta) * Math.sin(eps) * Math.sin(lam));
    return [(ra / D2R + 360) % 360, dec / D2R];
  }
  function sunAlt(lat, lon, date) {
    const n = date.getTime() / 864e5 + 2440587.5 - 2451545, g = (357.528 + 0.9856003 * n) * D2R;
    const lam = (280.46 + 0.9856474 * n + 1.915 * Math.sin(g) + 0.02 * Math.sin(2 * g)) * D2R, eps = 23.439 * D2R;
    const ra = Math.atan2(Math.cos(eps) * Math.sin(lam), Math.cos(lam)) / D2R, dec = Math.asin(Math.sin(eps) * Math.sin(lam)) / D2R;
    return altAz((ra + 360) % 360, dec, lat, lon, date)[0];
  }
  // the brightest stars (J2000 RA/Dec in degrees, visual magnitude, colour)
  const STARS = [['Sirius', 101.29, -16.72, -1.46, 'white'], ['Canopus', 95.99, -52.70, -0.74, 'white'], ['Arcturus', 213.92, 19.18, -0.05, 'orange'],
    ['Vega', 279.23, 38.78, 0.03, 'blue-white'], ['Capella', 79.17, 46.00, 0.08, 'yellow'], ['Rigel', 78.63, -8.20, 0.13, 'blue-white'],
    ['Procyon', 114.83, 5.22, 0.34, 'white'], ['Betelgeuse', 88.79, 7.41, 0.5, 'orange-red'], ['Altair', 297.70, 8.87, 0.77, 'white'],
    ['Aldebaran', 68.98, 16.51, 0.85, 'orange'], ['Antares', 247.35, -26.43, 1.06, 'red'], ['Spica', 201.30, -11.16, 0.97, 'blue-white'],
    ['Pollux', 116.33, 28.03, 1.14, 'orange'], ['Fomalhaut', 344.41, -29.62, 1.16, 'white'], ['Deneb', 310.36, 45.28, 1.25, 'white'],
    ['Regulus', 152.09, 11.97, 1.35, 'blue-white']];

  // ------------------------------------------------------------------ your place
  let zipTable = null;
  function lookupZip(zip) {
    const z = (B().zip || {});
    if (!z.url) return Promise.reject(new Error('The ZIP code table is not on the site yet; use "Use my location" instead.'));
    const get = zipTable ? Promise.resolve(zipTable) : S.load(z.url).then(t => (zipTable = t));
    return get.then(t => {
      const m = t.match(new RegExp('^' + zip + ' (-?\\d+) (-?\\d+)$', 'm'));
      if (!m) throw new Error(`ZIP code ${zip} not found (PO-box-only ZIP codes have no area; try a nearby one).`);
      return { lat: +m[1] / 100, lon: +m[2] / 100, label: 'ZIP ' + zip };
    });
  }
  function placeCard(box, onChange) {
    const p = place();
    box.innerHTML = `<h3>📍 Your place</h3><p class="small">Used for dark skies, Space Station passes and "What's that in the sky?". It stays in this browser: nothing is sent or saved anywhere else.</p>
      <div class="row"><input class="sg-zip" inputmode="numeric" maxlength="5" placeholder="US ZIP code" style="width:130px" value="${esc(p && /^ZIP/.test(p.label) ? p.label.slice(4) : '')}">
      <button type="button" class="b primary sg-setzip">Use ZIP code</button><button type="button" class="b sg-gps">Use my location</button>${p ? '<button type="button" class="b sg-forget">Forget</button>' : ''}</div>
      <p class="sg-placeout small">${p ? `Using <b>${esc(p.label)}</b> (${num(Math.abs(p.lat), 1)}°${p.lat >= 0 ? 'N' : 'S'}, ${num(Math.abs(p.lon), 1)}°${p.lon >= 0 ? 'E' : 'W'}).` : 'No place set yet.'}</p>`;
    const out = box.querySelector('.sg-placeout'), set = v => { LS.set('sl_place', v); placeCard(box, onChange); onChange(); };
    box.querySelector('.sg-setzip').addEventListener('click', () => {
      const zip = box.querySelector('.sg-zip').value.trim();
      if (!/^\d{5}$/.test(zip)) { out.textContent = 'Enter a 5-digit US ZIP code.'; return; }
      out.textContent = 'Looking it up (in your browser)…';
      lookupZip(zip).then(set, e => { out.textContent = e.message; });
    });
    box.querySelector('.sg-gps').addEventListener('click', () => {
      if (!navigator.geolocation) { out.textContent = 'This browser cannot share a location.'; return; }
      out.textContent = 'Asking your browser…';
      navigator.geolocation.getCurrentPosition(pos => set({ lat: Math.round(pos.coords.latitude * 100) / 100, lon: Math.round(pos.coords.longitude * 100) / 100, label: 'your location' }),
        () => { out.textContent = 'Location not shared. You can type a ZIP code instead.'; }, { timeout: 15000, maximumAge: 600000 });
    });
    const f = box.querySelector('.sg-forget'); if (f) f.addEventListener('click', () => set(null));
    box.querySelector('.sg-zip').addEventListener('keydown', e => { if (e.key === 'Enter') box.querySelector('.sg-setzip').click(); });
  }

  // ------------------------------------------------------------------ dark-sky finder
  let skyPixels = null;
  function loadDarkMap(ds) {
    if (skyPixels) return Promise.resolve(skyPixels);
    return new Promise((ok, bad) => {
      const im = new Image();
      im.onload = () => {
        const cv = document.createElement('canvas'); cv.width = ds.w; cv.height = ds.h;
        const x = cv.getContext && cv.getContext('2d');
        if (!x) return bad(new Error('This browser cannot read the map.'));
        x.drawImage(im, 0, 0);
        const px = x.getImageData(0, 0, ds.w, ds.h).data, v = new Uint8Array(ds.w * ds.h);
        for (let i = 0; i < v.length; i++) v[i] = px[i * 4];
        ok(skyPixels = v);
      };
      im.onerror = () => bad(new Error('The dark-sky map did not load.'));
      im.src = ds.url;
    });
  }
  const SKY_CLASS = [[0.25, 'Excellent dark sky', 'the Milky Way is bright and detailed'], [0.5, 'Good dark sky', 'the Milky Way is easy to see'],
    [0.75, 'Rural sky', 'the Milky Way shows on good nights'], [0.9, 'Suburban sky', 'the Milky Way is faint or invisible; bright planets and stars are fine'],
    [1.01, 'Bright city sky', 'only the Moon, planets and the brightest stars']];
  const skyClass = (ds, v) => SKY_CLASS.find(c => (ds.cdf[v] ?? 1) <= c[0]) || SKY_CLASS[4];
  function darkFinder(box) {
    const ds = B().darksky, p = place();
    box.innerHTML = `<h3>🌌 Find dark skies near you</h3>${!ds || !ds.url ? '<p class="muted small">The dark-sky map appears after the next update.</p>'
      : !p ? '<p class="small">Set your place above (ZIP code or location) to find the darkest skies within driving distance.</p>'
      : `<div class="row"><label class="small">How far will you drive? <select class="sg-radius">${[60, 100, 150, 250].map(r => `<option value="${r}" ${r === 150 ? 'selected' : ''}>${r} km (${Math.round(r * 0.621)} miles)</option>`).join('')}</select></label></div>
        <div class="sg-darkout small muted">Reading the map…</div>`}`;
    if (!ds || !ds.url || !p) return;
    const run = () => loadDarkMap(ds).then(v => showDark(box, ds, v, p, +box.querySelector('.sg-radius').value),
      e => { box.querySelector('.sg-darkout').textContent = e.message; });
    box.querySelector('.sg-radius').addEventListener('change', run);
    run();
  }
  // pure search, so it can be tested: darkest land spot in each distance band (nearly-equal spots: the closer one wins)
  function darkSpots(ds, v, p, radius) {
    const [lat0, lon0, lat1, lon1] = ds.bbox;
    const toPx = (la, lo) => [Math.round((lo - lon0) / (lon1 - lon0) * ds.w), Math.round((lat1 - la) / (lat1 - lat0) * ds.h)];
    const toLL = (x, y) => [lat1 - (y + 0.5) / ds.h * (lat1 - lat0), lon0 + (x + 0.5) / ds.w * (lon1 - lon0)];
    const [px, py] = toPx(p.lat, p.lon);
    if (px < 0 || py < 0 || px >= ds.w || py >= ds.h) return null;
    let here = v[py * ds.w + px];
    if (here === 255) here = Math.min(254, ...[-2, -1, 1, 2].flatMap(dx => [-2, -1, 1, 2].map(dy => v[(py + dy) * ds.w + px + dx] ?? 255)));   // on a lake shore
    const bands = [[0, Math.min(60, radius)], [60, Math.min(120, radius)], [120, radius]].filter(b => b[1] > b[0]);
    const rpx = Math.ceil(radius / (111 * (lat1 - lat0) / ds.h)) + 2, found = [];
    for (const [r0, r1] of bands) {
      let best = null;
      for (let y = Math.max(0, py - rpx); y < Math.min(ds.h, py + rpx); y++) {
        for (let x = Math.max(0, px - rpx * 2); x < Math.min(ds.w, px + rpx * 2); x++) {
          const val = v[y * ds.w + x];
          if (val === 255) continue;
          const [la, lo] = toLL(x, y), km = kmBetween(p.lat, p.lon, la, lo);
          if (km < r0 || km > r1) continue;
          const score = val + km * 0.04;
          if (!best || score < best.score) best = { score, val, la, lo, km, x, y };
        }
      }
      if (best && best.val < here - 8 && !found.some(f => kmBetween(f.la, f.lo, best.la, best.lo) < 25)) found.push(best);
    }
    return { here, found, px, py, rpx };
  }
  function showDark(box, ds, v, p, radius) {
    const out = box.querySelector('.sg-darkout'), r = darkSpots(ds, v, p, radius);
    if (!r) { out.innerHTML = 'The dark-sky map covers the lower 48 US states only (for now).'; return; }
    const { here, found, px, py, rpx } = r;
    const ratio = (a, b) => Math.pow(10, (a - b) / 254 * (ds.hi - ds.lo));
    const hc = skyClass(ds, here), osm = (la, lo) => `https://www.openstreetmap.org/?mlat=${la.toFixed(3)}&mlon=${lo.toFixed(3)}#map=10/${la.toFixed(3)}/${lo.toFixed(3)}`;
    const lpm = (la, lo) => `https://www.lightpollutionmap.info/#zoom=9.00&lat=${la.toFixed(4)}&lon=${lo.toFixed(4)}`;
    out.classList.remove('muted');
    out.innerHTML = `<p>Your sky (${esc(p.label)}): <b>${hc[1]}</b>: ${hc[2]}.</p>
      ${found.length ? `<table class="compact"><tr><th>where</th><th>sky there</th><th>darker by</th><th></th></tr>${found.map(f => {
        const c = skyClass(ds, f.val);
        return `<tr><td><b>${Math.round(f.km)} km (${Math.round(f.km * 0.621)} mi) ${compass(bearing(p.lat, p.lon, f.la, f.lo))}</b><div class="small muted">${num(Math.abs(f.la), 2)}°${f.la >= 0 ? 'N' : 'S'}, ${num(Math.abs(f.lo), 2)}°W</div></td>
          <td>${c[1]}</td><td class="mono">about ${num(ratio(here, f.val), 1)}×</td><td><a href="${osm(f.la, f.lo)}" target="_blank" rel="noopener">map ↗</a> · <a href="${lpm(f.la, f.lo)}" target="_blank" rel="noopener">light map ↗</a></td></tr>`;
      }).join('')}</table>` : `<p>Your sky is already about as dark as it gets within ${radius} km.</p>`}
      <canvas class="sg-map" width="360" height="240"></canvas>
      <ul class="small"><li>Check the spot is a park or public land that is open at night, and safe to reach.</li>
      <li>Go near the New Moon${nextNewMoon() ? ` (next: ${esc(nextNewMoon())})` : ''}, give your eyes 20 minutes, and use a red light.</li>
      <li>Estimated from NASA's night-lights map (Black Marble): a guide, not a measurement.${ds.water ? '' : ' Water is not masked on this map: make sure the spot is on land.'}</li></ul>`;
    drawMini(out.querySelector('canvas'), ds, v, px, py, rpx, found.map(f => [f.x, f.y]));
  }
  function drawMini(cv, ds, v, px, py, rpx, spots) {
    const x = cv.getContext && cv.getContext('2d'); if (!x) return;
    const w = rpx * 4, h = rpx * 2, sx = cv.width / w, sy = cv.height / h, img = x.createImageData(cv.width, cv.height);
    for (let j = 0; j < cv.height; j++) for (let i = 0; i < cv.width; i++) {
      const gx = Math.floor(px - rpx * 2 + i / sx), gy = Math.floor(py - rpx + j / sy), k = (j * cv.width + i) * 4;
      const val = gx >= 0 && gy >= 0 && gx < ds.w && gy < ds.h ? v[gy * ds.w + gx] : 255;
      const c = val === 255 ? [12, 30, 52] : [Math.min(255, val * 1.6), Math.min(255, val * 1.1), Math.max(30, 90 - val * 0.2)];
      img.data[k] = c[0]; img.data[k + 1] = c[1]; img.data[k + 2] = c[2]; img.data[k + 3] = 255;
    }
    x.putImageData(img, 0, 0);
    const dot = (gx, gy, col, r) => { x.fillStyle = col; x.beginPath(); x.arc((gx - px + rpx * 2) * sx, (gy - py + rpx) * sy, r, 0, 2 * Math.PI); x.fill(); };
    spots.forEach(([gx, gy]) => dot(gx, gy, '#2fbf71', 6));
    dot(px, py, '#4db3d3', 6);
  }
  function nextNewMoon() {
    const nm = (B().events || []).find(e => e.kind === 'moon' && /New/.test(e.title));
    return nm ? fmtDate(nm, { month: 'short', day: 'numeric' }) : '';
  }

  // ------------------------------------------------------------------ events and calendar reminders
  const ICON = { meteor: '☄️', moon: '🌕', eclipse: '🌘', launch: '🚀', comet: '💫' };
  const icsEsc = t => String(t || '').replace(/\\/g, '\\\\').replace(/;/g, '\\;').replace(/,/g, '\\,').replace(/\n/g, '\\n');
  const icsDate = d => d.toISOString().replace(/[-:]/g, '').replace(/\.\d+/, '');
  function icsFor(ev) {
    const lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Space Lab//Sky events//EN', 'BEGIN:VEVENT',
      `UID:${(ev.kind + '-' + (ev.t || ev.date) + '-' + ev.title).replace(/[^A-Za-z0-9]+/g, '-').slice(0, 100)}@spacelab`, `DTSTAMP:${icsDate(new Date())}`];
    if (ev.t) { const t = new Date(ev.t); lines.push(`DTSTART:${icsDate(t)}`, `DTEND:${icsDate(new Date(t.getTime() + 36e5))}`); }
    else { const d = new Date(ev.date + 'T00:00:00Z'), e = new Date(d.getTime() + 864e5); lines.push(`DTSTART;VALUE=DATE:${ev.date.replace(/-/g, '')}`, `DTEND;VALUE=DATE:${e.toISOString().slice(0, 10).replace(/-/g, '')}`); }
    lines.push(`SUMMARY:${icsEsc(ev.title)}`, `DESCRIPTION:${icsEsc((ev.detail || '') + ' ' + siteUrl())}`, 'BEGIN:VALARM', 'ACTION:DISPLAY',
      `DESCRIPTION:${icsEsc(ev.title)}`, `TRIGGER:${ev.t ? '-PT2H' : '-PT6H'}`, 'END:VALARM', 'END:VEVENT', 'END:VCALENDAR');
    return lines.join('\r\n') + '\r\n';
  }
  function eventsCard(box) {
    const evs = (B().events || []).filter(e => (e.t ? Date.parse(e.t) : Date.parse(e.date + 'T23:59:59Z')) > Date.now() - 864e5);
    const filt = box.dataset.filter || '', url = siteUrl(), webcal = url ? url.replace(/^https?:/, 'webcal:') + 'events.ics' : '';
    const kinds = [...new Set(evs.map(e => e.kind))];
    box.innerHTML = `<h3>📅 Coming up in the sky</h3>
      ${url ? `<div class="note small"><b>Get reminders:</b> subscribe once and meteor showers, eclipses, New and Full Moons and launches appear in your calendar, updated automatically.
        <div class="row" style="margin-top:6px"><a class="b primary" href="${esc(webcal)}">Subscribe (Apple / Outlook)</a><a class="b" target="_blank" rel="noopener" href="https://calendar.google.com/calendar/r?cid=${encodeURIComponent(webcal)}">Subscribe in Google Calendar ↗</a></div>
        <span class="muted">Or tap "Remind me" on any event below to add just that one, with an alert.</span></div>` : ''}
      <div class="row sg-kinds"><button type="button" class="b ${filt ? '' : 'primary'} sg-kind" data-k="">All</button>${kinds.map(k => `<button type="button" class="b sg-kind ${filt === k ? 'primary' : ''}" data-k="${k}">${ICON[k] || ''} ${k === 'meteor' ? 'Meteor showers' : k === 'moon' ? 'Moon' : k === 'eclipse' ? 'Eclipses' : k === 'launch' ? 'Launches' : 'Comets'}</button>`).join('')}</div>
      <ul class="list sg-events">${evs.filter(e => !filt || e.kind === filt).map((e, i) => `<li><span><b>${ICON[e.kind] || ''} ${esc(e.title)}</b><br><span class="small">${fmtDate(e)}${e.t ? '' : ' (all day)'}</span><br><span class="small muted">${esc(e.detail || '')}</span></span>
        <button type="button" class="b sg-remind" data-i="${evs.indexOf(e)}">Remind me</button></li>`).join('') || '<li class="muted">Nothing listed yet.</li>'}</ul>`;
    box.querySelectorAll('.sg-kind').forEach(b => b.addEventListener('click', () => { box.dataset.filter = b.dataset.k; eventsCard(box); }));
    box.querySelectorAll('.sg-remind').forEach(b => b.addEventListener('click', () => {
      const ev = evs[+b.dataset.i], a = document.createElement('a');
      a.download = (ev.title.replace(/[^A-Za-z0-9]+/g, '-').slice(0, 50) || 'event') + '.ics';
      a.href = 'data:text/calendar;charset=utf-8,' + encodeURIComponent(icsFor(ev));
      a.click(); b.textContent = 'Added ✓'; S.track('remind-' + ev.kind, true);
    }));
  }

  // ------------------------------------------------------------------ What's that in the sky?
  function identify(p, when, dir, height, motion, look) {
    const out = [], az0 = DIRS[dir], altBand = { low: [0, 30], mid: [25, 65], high: [55, 90] }[height];
    const near = (az, alt) => (dir === 'up' ? alt > 60 : Math.abs(((az - az0 + 540) % 360) - 180) <= 35) && (dir === 'up' || (alt >= altBand[0] - 5 && alt <= altBand[1] + 5));
    const dark = sunAlt(p.lat, p.lon, when);
    const add = (score, name, why) => out.push({ score, name, why });
    if (look === 'blink' || look === 'redgreen') add(look === 'redgreen' ? 95 : 85, 'An aircraft', 'Planes have flashing white strobes and red/green lights at the wing tips. Satellites and planets do not blink.');
    if (look === 'train') {
      const recent = ((B().launches || {}).previous || []).filter(l => /starlink/i.test(l.name) && Date.now() - Date.parse(l.net) < 12 * 864e5);
      add(recent.length ? 95 : 75, 'A Starlink "train" of satellites', `${recent.length ? `SpaceX launched a Starlink batch ${Math.round((Date.now() - Date.parse(recent[0].net)) / 864e5)} days ago; for a week or two they fly in a line before spreading out. ` : 'New batches of Starlink satellites fly in a line for a week or two after launch. '}${((B().satellites || {}).starlink) ? `About ${Number(B().satellites.starlink).toLocaleString()} Starlink satellites are in orbit now.` : ''}`);
    }
    if (motion === 'streak') {
      const sh = ((B().sky || {}).showers || []).find(s => Math.abs(s.days) <= 3);
      add(90, sh ? `A ${sh.name.replace(/s$/, '')} meteor` : 'A meteor (shooting star)', sh ? `The ${sh.name} shower peaks ${sh.days === 0 ? 'tonight' : sh.days > 0 ? 'in ' + sh.days + ' days' : Math.abs(sh.days) + ' days ago'}: a grain of dust from ${sh.parent} burning up 100 km up.` : 'A grain of space dust burning up about 100 km up. A few random ones appear every hour.');
      if (look === 'bright') add(70, 'A fireball', 'An unusually bright meteor: a pebble-sized or bigger piece of rock. You can report it to the American Meteor Society.');
    }
    if (motion === 'steady' && look !== 'blink' && look !== 'redgreen' && look !== 'train') {
      const iss = B().iss, beyond = window.SpaceLabBeyond;
      if (iss && iss.xyz && beyond && beyond.issPasses) {
        const passes = beyond.issPasses(iss, p.lat, p.lon, 5, true).filter(x => Math.abs(x.tmax - when.getTime()) < 15 * 6e4);
        if (passes.length) add(97, 'The International Space Station', `It passed over you at ${new Date(passes[0].tmax).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}, up to ${Math.round(passes[0].max)}° high, moving ${compass(passes[0].az0)} to ${compass(passes[0].az1)}.`);
      }
      add(dark < -3 && dark > -25 ? 70 : 50, 'A satellite', `A steady dot gliding across the sky in a few minutes, catching sunlight${dark < -3 && dark > -25 ? ' (the hours after sunset and before sunrise are the best time to see them)' : ''}. About ${((B().satellites || {}).active || 10000).toLocaleString()} working satellites are up there.`);
    }
    if (motion === 'still' || motion === 'slow') {
      const cands = [];
      for (const pl of ((B().sky || {}).planets || [])) {
        if (pl.ra === undefined) continue;
        const [alt, az] = altAz(pl.ra, pl.dec, p.lat, p.lon, when);
        if (alt > -1 && near(az, alt)) cands.push([pl.mag, pl.name, alt, az, 'planet']);
      }
      const [mra, mdec] = moonRaDec(when), [malt, maz] = altAz(mra, mdec, p.lat, p.lon, when);
      if (malt > -1 && near(maz, malt)) cands.push([-12, 'The Moon', malt, maz, 'moon']);
      for (const st of STARS) { const [alt, az] = altAz(st[1], st[2], p.lat, p.lon, when); if (alt > -1 && near(az, alt)) cands.push([st[3], st[0], alt, az, 'star', st[4]]); }
      cands.sort((a, b) => a[0] - b[0]);
      cands.slice(0, 3).forEach(([mag, name, alt, az, kind, col], i) => {
        const twinkly = kind === 'star' && alt < 25;
        add((look === 'bright' && mag < -1 ? 92 : 80) - i * 12 + (look === 'twinkle' && twinkly ? 10 : 0),
          kind === 'planet' ? `The planet ${name}` : name,
          `${kind === 'planet' ? `${name} is ${mag < -3 ? 'the brightest thing in the night sky after the Moon' : mag < -1 ? 'brighter than any star' : 'as bright as the brighter stars'}` : kind === 'moon' ? 'The Moon' : `${name}, a ${col} star`} and was ${Math.round(alt)}° up in the ${compass(az)} at that time. ${twinkly ? 'Stars low in the sky twinkle and flash colours because of the air.' : kind === 'planet' ? 'Planets shine steadily and barely twinkle.' : ''}`);
      });
      if (!cands.length) add(40, 'A faint star, or a satellite far away', 'Nothing bright was in that part of the sky. Try a sky app like Stellarium for a full map.');
    }
    if (dark > -6 && motion !== 'streak') out.forEach(o => { if (/star|satellite/i.test(o.name)) o.score -= 10; });
    return out.sort((a, b) => b.score - a.score).slice(0, 3);
  }
  function whatsThat(box) {
    const p = place();
    const now = new Date(Date.now() - new Date().getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
    box.innerHTML = `<h3>🔎 What's that in the sky?</h3><p class="small">Saw something and wondered what it was? Describe it and we'll check the planets, bright stars, the Moon, the Space Station, satellites and meteor showers for that moment.</p>
      ${p ? '' : '<p class="small"><b>Set your place above first.</b></p>'}
      <div class="bx-form"><label>When <input type="datetime-local" class="sg-when" value="${now}"></label>
      <label>Direction <select class="sg-dir">${Object.keys(DIRS).map(d => `<option>${d}</option>`).join('')}<option value="up">straight up</option></select></label>
      <label>How high <select class="sg-height"><option value="low">low (near the horizon)</option><option value="mid">halfway up</option><option value="high">high up</option></select></label>
      <label>How it moved <select class="sg-motion"><option value="still">not moving</option><option value="slow">slowly, with the stars</option><option value="steady">gliding steadily across in minutes</option><option value="streak">a fast streak (a second or two)</option></select></label>
      <label>What it looked like <select class="sg-look"><option value="steady">a steady dot</option><option value="bright">very bright</option><option value="twinkle">twinkling, changing colours</option><option value="blink">blinking or flashing</option><option value="redgreen">red and green lights</option><option value="train">a line of several lights</option></select></label></div>
      <button type="button" class="b primary sg-identify" ${p ? '' : 'disabled'}>What was it?</button><div class="sg-idout"></div>`;
    const go = box.querySelector('.sg-identify');
    go.addEventListener('click', () => {
      const v = c => box.querySelector(c).value, when = new Date(v('.sg-when'));
      if (isNaN(when)) return;
      const res = identify(place(), when, v('.sg-dir'), v('.sg-height'), v('.sg-motion'), v('.sg-look'));
      box.querySelector('.sg-idout').innerHTML = res.length ? `<ol class="sg-guesses">${res.map((r, i) => `<li><b>${esc(r.name)}</b> <span class="chip ${i ? '' : 'ok'}">${r.score >= 85 ? 'very likely' : r.score >= 65 ? 'likely' : 'possible'}</span><br><span class="small">${esc(r.why)}</span></li>`).join('')}</ol>`
        : '<p class="muted">No good match. It may have been an aircraft, a drone or a weather balloon.</p>';
      S.track('identify', true);
    });
  }

  // ------------------------------------------------------------------ tonight from your place
  const RADIANT = { Quadrantids: [230, 49], Lyrids: [271, 34], 'Eta Aquariids': [338, -1], 'Southern Delta Aquariids': [340, -16], Perseids: [48, 58],
                    Draconids: [262, 54], Orionids: [95, 16], Leonids: [152, 22], Geminids: [112, 33], Ursids: [217, 76] };
  const hm = t => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  function tonight(p, now = new Date()) {
    const start = new Date(now); start.setHours(12, 0, 0, 0);
    if (now.getHours() < 12) start.setDate(start.getDate() - 1);
    const N = 145, step = 6e5, ts = Array.from({ length: N }, (_, i) => new Date(start.getTime() + i * step));
    const sun = ts.map(t => sunAlt(p.lat, p.lon, t));
    const cross = (arr, level, up) => { for (let i = 1; i < arr.length; i++) if (up ? arr[i - 1] < level && arr[i] >= level : arr[i - 1] >= level && arr[i] < level) return ts[i].getTime(); return null; };
    const night = { sunset: cross(sun, -0.83, false), sunrise: cross(sun, -0.83, true), dark: cross(sun, -18, false), darkEnd: cross(sun, -18, true) };
    const darkIdx = i => sun[i] < -6;
    const track = (ra, dec) => ts.map(t => altAz(ra, dec, p.lat, p.lon, t));
    const upWindow = (alts) => {          // first and last moment above 10 degrees while the sky is dark, and the highest point
      let a = null, b = null, best = null;
      alts.forEach(([alt, az], i) => { if (alt > 10 && darkIdx(i)) { if (a === null) a = i; b = i; if (!best || alt > best[0]) best = [alt, az, i]; } });
      return a === null ? null : { from: ts[a].getTime(), to: ts[b].getTime(), alt: best[0], az: best[1], at: ts[best[2]].getTime() };
    };
    const planets = ((B().sky || {}).planets || []).filter(x => x.ra !== undefined).map(x => ({ name: x.name, mag: x.mag, w: upWindow(track(x.ra, x.dec)) }));
    const moonAlt = ts.map(t => { const [ra, dec] = moonRaDec(t); return altAz(ra, dec, p.lat, p.lon, t)[0]; });
    const moon = { rise: cross(moonAlt, 0, true), set: cross(moonAlt, 0, false), illum: ((B().sky || {}).moon || {}).illumination, phase: ((B().sky || {}).moon || {}).phase };
    const showers = ((B().sky || {}).showers || []).filter(x => Math.abs(x.days) <= 4 && RADIANT[x.name]).map(x => {
      const w = upWindow(track(...RADIANT[x.name]));
      const moonUp = w ? moonAlt[Math.round((w.at - start.getTime()) / step)] > 0 : false;
      return { ...x, w, rate: w ? Math.round(x.zhr * Math.sin(w.alt * D2R) * (moonUp && (moon.illum || 0) > 0.5 ? 0.4 : 1)) : 0 };
    });
    const kp = (((B().space_weather || {}).kp) || []).filter(k => k.kp !== null).slice(-1)[0];
    const kpNeed = p.lat >= 50 ? 5 : p.lat >= 47 ? 6 : p.lat >= 44 ? 7 : p.lat >= 40 ? 8 : 9;
    let iss = [];
    const beyond = window.SpaceLabBeyond;
    if (B().iss && beyond) iss = beyond.issPasses(B().iss, p.lat, p.lon).filter(x => x.visible && x.start >= ts[0].getTime() && x.start <= ts[N - 1].getTime());
    return { night, planets, moon, showers, kp, kpNeed, iss, start };
  }
  function tonightHTML(p) {
    const t = tonight(p), n = t.night;
    const vis = t.planets.filter(x => x.w).sort((a, b) => a.mag - b.mag);
    return `<div class="row"><h3>🌙 Tonight from ${esc(p.label)}</h3><span class="spacer"></span><span class="small muted">times are your clock</span></div>
      <div class="kv"><b>Sunset</b><span>${n.sunset ? hm(n.sunset) : '—'}${n.dark ? ` · fully dark from <b>${hm(n.dark)}</b>` : ' · never fully dark tonight'}${n.darkEnd ? ` to <b>${hm(n.darkEnd)}</b>` : ''}</span>
      <b>Moon</b><span>${esc(t.moon.phase || '')}${t.moon.illum !== undefined ? `, ${Math.round(t.moon.illum * 100)}% lit` : ''}${t.moon.rise ? ` · rises ${hm(t.moon.rise)}` : ''}${t.moon.set ? ` · sets ${hm(t.moon.set)}` : ''}</span></div>
      <h4>Planets you can see</h4>${vis.length ? `<ul class="hm-list">${vis.map(x => `<li><b>${esc(x.name)}</b>: ${hm(x.w.from)} to ${hm(x.w.to)}, highest (${Math.round(x.w.alt)}°) in the ${compass(x.w.az)} around ${hm(x.w.at)}</li>`).join('')}</ul>` : '<p class="small muted">No bright planets are up while it is dark tonight.</p>'}
      ${t.showers.length ? `<h4>Meteors</h4><ul class="hm-list">${t.showers.map(x => `<li><b>${esc(x.name)}</b> (peak ${x.days === 0 ? 'tonight' : x.days > 0 ? 'in ' + x.days + ' day' + (x.days > 1 ? 's' : '') : Math.abs(x.days) + ' days ago'}): ${x.w ? `best around ${hm(x.w.at)}, maybe ${x.rate} an hour from a dark site` : 'the shower\'s source stays low tonight'}</li>`).join('')}</ul>` : ''}
      ${t.iss.length ? `<h4>Space Station</h4><ul class="hm-list">${t.iss.map(x => `<li><b>${hm(x.start)}</b>, ${Math.round((x.end - x.start) / 6e4) || 1} min, up to ${Math.round(x.max)}° high, ${compass(x.az0)} → ${compass(x.az1)}</li>`).join('')}</ul>` : '<p class="small muted">No visible Space Station pass tonight.</p>'}
      <p class="small"><b>Aurora:</b> at your latitude it usually takes Kp ${t.kpNeed} or more${t.kp ? `; right now Kp is ${num(t.kp.kp, 1)}${t.kp.kp >= t.kpNeed ? ' <span class="chip warn">look north after dark!</span>' : ''}` : ''}.</p>
      <div class="row"><button type="button" class="b sg-clouds">☁️ Cloud forecast for tonight</button><span class="small muted">asks Open-Meteo, a free weather service, for your area (rounded to ~10 km)</span></div><div class="sg-cloudout"></div>`;
  }
  function wireTonight(box, p) {
    const b = box.querySelector('.sg-clouds'); if (!b) return;
    b.addEventListener('click', () => {
      const out = box.querySelector('.sg-cloudout');
      if (!S.weather) { out.textContent = 'Not available here.'; return; }
      out.textContent = 'Checking…';
      S.weather(p.lat, p.lon).then(w => {
        const h = w.hourly || {}, t0 = tonight(p).start.getTime();
        const rows = (h.time || []).map((t, i) => [t * 1000, (h.cloud_cover || [])[i]]).filter(([t]) => t >= t0 + 5 * 36e5 && t <= t0 + 20 * 36e5);
        if (!rows.length) { out.textContent = 'No forecast for tonight.'; return; }
        const clear = rows.filter(r => r[1] <= 30).length;
        out.innerHTML = `<div class="sg-clouds-bars">${rows.map(([t, c]) => `<span title="${hm(t)}: ${c}% cloud" style="--c:${c}"><i></i><em>${new Date(t).getHours()}</em></span>`).join('')}</div>
          <p class="small">${clear >= rows.length * 0.6 ? '✅ Mostly clear: a good night to go out.' : clear >= rows.length * 0.25 ? '⛅ Some clear spells.' : '☁️ Mostly cloudy tonight.'} Bars show the % of sky covered by cloud each hour (shorter = clearer). Forecast: Open-Meteo.</p>`;
      }, e => { out.textContent = 'The weather service did not answer: ' + e.message; });
    });
  }

  // ------------------------------------------------------------------ location chip in the header (every tab)
  function chip() {
    let c = document.querySelector('.sg-chip');
    if (!c) {
      const h1 = document.querySelector('header h1');
      if (!h1) return;
      c = document.createElement('button'); c.type = 'button'; c.className = 'b sg-chip';
      h1.after(c);
      c.addEventListener('click', openPlace);
    }
    const p = place();
    c.textContent = p ? '📍 ' + p.label : '📍 Set your location';
    c.title = 'Personalise tonight\'s sky, Space Station passes and dark-sky spots. Stays in this browser.';
  }
  function rerender() {
    chip();
    const cur = document.querySelector('nav.tabs button[aria-selected="true"]');
    if (cur) cur.click();
  }
  function openPlace() {
    const m = document.getElementById('modal'), body = document.getElementById('modalBody');
    if (!m || !body) { const b = document.querySelector('nav.tabs button[data-tab="stargazing"]'); if (b) b.click(); return; }
    body.innerHTML = '<div class="card sg-place"></div>';
    placeCard(body.querySelector('.sg-place'), () => { rerender(); setTimeout(() => m.classList.remove('on'), 900); });
    m.classList.add('on');
  }

  // ------------------------------------------------------------------ tab
  function tab(el) {
    el.innerHTML = `<div class="card"><h3>🔭 Stargazing</h3><p>Tools to get out and see things: find dark skies within driving distance, get reminders for meteor showers and eclipses, and work out what that light in the sky was.</p></div>
      <div class="card sg-place"></div><div class="grid g2"><div class="card sg-dark"></div><div class="card sg-what"></div></div><div class="card sg-ev"></div>`;
    const refresh = () => { chip(); darkFinder(el.querySelector('.sg-dark')); whatsThat(el.querySelector('.sg-what')); };
    placeCard(el.querySelector('.sg-place'), refresh);
    refresh();
    eventsCard(el.querySelector('.sg-ev'));
  }
  S.addTab('stargazing', 'Stargazing', tab);
  S.onRender((t, el) => {
    chip();
    const p = place();
    if (t === 'sky' && el && !el.querySelector('.sg-tonight')) {
      const box = document.createElement('div'); box.className = 'card sg-tonight';
      box.innerHTML = p ? tonightHTML(p) : '<h3>🌙 Tonight from your place</h3><p>Set your location to see when it gets dark, which planets are up and when, the best time for meteors and Space Station passes over you.</p><button type="button" class="b primary sg-setplace">📍 Set your location</button>';
      const first = el.querySelector('.card'); if (first) first.after(box); else el.prepend(box);
      if (p) wireTonight(box, p); else box.querySelector('.sg-setplace').addEventListener('click', openPlace);
    }
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', chip); else chip();
  window.SpaceLabStargazing = { identify, altAz, moonRaDec, sunAlt, icsFor, lookupZip, darkSpots, skyClass, tonight, tonightHTML, wireTonight, openPlace, place };
})();
