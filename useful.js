// "Useful science tonight" (idea 59): for someone with a telescope, what science needs observed tonight from their place.
// Three kinds, each checked against the visitor's sky (dark, and high enough the whole time) and their telescope size:
//  - transits of known planets whose predicted times have drifted most (Exoplanet Watch list): a timing narrows them;
//  - this lab's own TESS planet candidates: a dip seen from the ground, on the right star, is their next step;
//  - objects on the Minor Planet Center's confirmation page (JPL Scout): positions in the next day or two decide what they are.
// Runs on the public page AND on the local Space Lab page (see EXTRAS.md). The place stays in this browser; nothing is sent anywhere.
(function () {
  'use strict';
  const S = window.SpaceLab, SG = window.SpaceLabStargazing;
  if (!S || !SG) return;
  const esc = S.esc, num = S.num, D = () => S.data || {};
  const LS = {
    get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
    set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* blocked */ } },
  };
  // rough limits: a transit needs the star bright enough for ~2 mmag photometry and a dip deep enough; an asteroid must
  // show up in a few stacked minutes. Real limits depend on the sky, the camera and patience.
  const SCOPES = {
    smart: { label: 'Smart telescope (50–80 mm: Seestar, Dwarf…)', vmax: 11.5, depth: 10, neo: 16.0 },
    mid: { label: '6–8 inch (150–200 mm) with a camera', vmax: 13.0, depth: 5, neo: 18.5 },
    big: { label: '11–14 inch (280–360 mm) or bigger', vmax: 14.5, depth: 3, neo: 20.0 },
  };
  const LINKS = {
    exo: [['AAVSO Exoplanet Database', 'https://aavso.org/apps/exosite/'], ['ExoClock', 'https://www.exoclock.space'],
          ['NASA Exoplanet Watch', 'https://science.nasa.gov/citizen-science/exoplanet-watch/']],
    neo: [['MPC confirmation page', 'https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html']],
  };
  const STEP = 6e5, N = 145;                          // 10-minute steps for 24 hours from local noon
  const hm = t => new Date(t).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
  const jdMs = jd => (jd - 2440587.5) * 864e5;
  const msJd = ms => ms / 864e5 + 2440587.5;

  function sky(p, now) {
    const start = SG.tonight(p, now).start.getTime();
    const ts = Array.from({ length: N }, (_, i) => start + i * STEP);
    const sun = ts.map(t => SG.sunAlt(p.lat, p.lon, new Date(t)));
    // the whole window [a, b] dark (Sun below -12°) with the star above `minAlt`; returns the lowest altitude or null
    const fits = (ra, dec, a, b, minAlt = 25) => {
      if (a < start || b > start + (N - 1) * STEP) return null;
      let low = 90;
      for (let i = Math.ceil((a - start) / STEP); i <= Math.floor((b - start) / STEP); i++) {
        if (sun[i] >= -12) return null;
        const alt = SG.altAz(ra, dec, p.lat, p.lon, new Date(ts[i]))[0];
        if (alt < minAlt) return null;
        low = Math.min(low, alt);
      }
      return low;
    };
    // the dark moment when the object is highest (for things that can be caught any time tonight)
    const best = (ra, dec, minAlt = 30) => {
      let b = null;
      ts.forEach((t, i) => { if (sun[i] < -12) { const a = SG.altAz(ra, dec, p.lat, p.lon, new Date(t))[0]; if (a >= minAlt && (!b || a > b.alt)) b = { t, alt: a }; } });
      return b;
    };
    return { start, end: start + (N - 1) * STEP, fits, best };
  }

  function items(p, scopeKey, now = new Date()) {
    const sc = SCOPES[scopeKey] || SCOPES.mid, sk = sky(p, now), out = [];
    const jd0 = msJd(sk.start), jd1 = msJd(sk.end);
    for (const x of ((D().exowatch || {}).stale || [])) {                  // known planets whose transit times have drifted
      if (!(x.period > 0.05) || !x.next_jd || !isFinite(x.ra) || !isFinite(x.dec) || x.ra === null || x.dec === null || x.vmag > sc.vmax || x.depth_ppt < sc.depth) continue;
      const half = ((x.dur_h || 2.5) / 2 + 1) / 24;
      for (let k = Math.ceil((jd0 - x.next_jd + half) / x.period); ; k++) {
        const mid = x.next_jd + k * x.period;
        if (mid + half > jd1) break;
        const low = sk.fits(x.ra, x.dec, jdMs(mid - half), jdMs(mid + half));
        if (low === null) continue;
        out.push({ kind: 'exo', score: Math.min(x.sigma_min || 0, 120) / 2 + 10, name: x.name, from: jdMs(mid - half), to: jdMs(mid + half), mid: jdMs(mid),
          what: `Transit of <b>${esc(x.name)}</b> (star V ${num(x.vmag, 1)}, ${num(x.depth_ppt, 1)} ppt deep, never below ${Math.round(low)}° high)`,
          why: `Its predicted time has drifted: it is now uncertain by about ±${Math.round(x.sigma_min)} min. One good timing pins it down for future observers (ExoClock keeps such timings current for ESA's Ariel mission).`,
          how: 'Start an hour before, keep going an hour after, then reduce the light curve (EXOTIC is free) and upload the timing.', links: LINKS.exo });
      }
    }
    const btjd = jd => jd - 2457000;
    for (const c of ((D().tess || {}).cands || [])) {                      // this lab's own planet candidates
      const st = c.star || {};
      if (c.kind !== 'periodic' || !(c.period > 0.05) || !c.t0 || !isFinite(st.ra) || !isFinite(st.dec) || st.ra === null || st.dec === null || !isFinite(st.tmag) || st.tmag === null) continue;
      const ppt = (c.depth || 0) * 1000;
      if (st.tmag + 1 > sc.vmax || ppt < sc.depth) continue;             // cool dwarfs: V is about a magnitude fainter than TESS's T
      const half = ((c.duration || 0.1) / 2) + 1.5 / 24;
      for (let k = Math.ceil((btjd(jd0) - c.t0 + half) / c.period); ; k++) {
        const mid = c.t0 + k * c.period + 2457000;
        if (mid + half > jd1) break;
        const low = sk.fits(st.ra, st.dec, jdMs(mid - half), jdMs(mid + half));
        if (low === null) continue;
        out.push({ kind: 'tess', score: 40 + Math.min(c.snr || 0, 30), name: 'TIC ' + c.tic, tic: c.tic, from: jdMs(mid - half), to: jdMs(mid + half), mid: jdMs(mid),
          what: `Predicted dip of <b>TIC ${esc(c.tic)}</b>, a Space Lab planet candidate (TESS mag ${num(st.tmag, 1)}, ${num(ppt, 1)} ppt deep, never below ${Math.round(low)}° high)`,
          why: `Seen only by TESS so far (${esc(c.status || 'under review')}). A dip from the ground, on this star and not a neighbour, is the next step for any candidate. ${k} orbits have passed since TESS watched, so the time could be off by an hour or more: the window is wide on purpose.`,
          how: 'Measure the star and its neighbours within 2 arcminutes; a neighbour that dims instead means an eclipsing binary, also worth knowing.', links: [] });
      }
    }
    for (const o of ((D().asteroids || {}).neocp || [])) {                 // the Minor Planet Center's confirmation page
      if ([o.ra_deg, o.dec_deg, o.v].some(v => v === null || v === undefined || !isFinite(v)) || o.v > sc.neo) continue;
      const b = sk.best(o.ra_deg, o.dec_deg);
      if (!b) continue;
      const hard = (o.unc || 0) > 30;
      out.push({ kind: 'neo', score: 70 + (o.neoScore || 0) / 5 - (hard ? 25 : 0), name: o.name, from: b.t, to: b.t, mid: b.t,
        what: `<b>${esc(o.name)}</b> on the confirmation page (V ${num(o.v, 1)}, highest around ${hm(b.t)} at ${Math.round(b.alt)}°)`,
        why: `${o.neoScore !== null && o.neoScore !== undefined ? `JPL Scout gives it a ${Math.round(o.neoScore)}% chance of being a near-Earth object. ` : ''}It has ${o.nObs || 'a few'} observations over ${num(o.arc, 1)} hours; positions from anyone in the next day or two decide its orbit, before it is lost.`,
        how: `It moves ${num(o.rate, 1)}″ per minute${o.unc ? ` and its position is uncertain by about ${Math.round(o.unc)}′${hard ? ' (a big search area: a wide field helps)' : ''}` : ''}. Get the current position from the MPC page just before you start, take short exposures, and report with your MPC observatory code.`,
        links: LINKS.neo });
    }
    return out.sort((a, b) => b.score - a.score);
  }

  function html(p) {
    const key = LS.get('sl_scope', 'mid'), list = items(p, key);
    const opt = Object.entries(SCOPES).map(([k, v]) => `<option value="${k}"${k === key ? ' selected' : ''}>${esc(v.label)}</option>`).join('');
    const when = x => x.kind === 'neo' ? `best around ${hm(x.mid)}` : `${hm(x.from)} to ${hm(x.to)} (middle ${hm(x.mid)})`;
    const links = x => x.links.map(([t, u]) => `<a href="${esc(u)}" target="_blank" rel="noopener">${esc(t)} ↗</a>`).join(' · ');
    const card = (x, big) => `<div class="ul-item${big ? ' ul-pick' : ''}"><div>${big ? '<span class="chip ok">tonight\'s pick</span> ' : ''}${x.what}</div>
      <div class="small"><b>${when(x)}</b></div><p class="small">${x.why}</p><p class="small muted">${x.how}</p>${links(x) ? `<p class="small">${links(x)}</p>` : ''}
      ${x.kind === 'tess' ? `<p class="small"><a href="#tic-${esc(x.tic)}" data-ul-tic="${esc(x.tic)}">See the candidate</a></p>` : ''}</div>`;
    return `<div class="row"><h3>🔭 Useful science tonight from ${esc(p.label)}</h3></div>
      <p class="small">For telescope owners: what science needs observed tonight, checked against your sky (dark, and high enough the whole time). Times are your clock.</p>
      <label class="small">Your telescope <select class="ul-scope">${opt}</select></label>
      ${list.length ? card(list[0], true) + (list.length > 1 ? `<details${list.length <= 4 ? ' open' : ''}><summary class="small">${list.length - 1} more for tonight</summary>${list.slice(1, 12).map(x => card(x, false)).join('')}</details>` : '')
        : '<p class="small muted">Nothing on the lists fits tonight for this telescope from here. Try a bigger telescope setting, or look again tomorrow: the lists change every few hours.</p>'}
      <p class="small muted">Limits by telescope are rough. Lists: planets whose timings have drifted (NASA Exoplanet Archive, ExoClock), this lab's TESS candidates, and the Minor Planet Center's confirmation page (JPL Scout). Nothing is ever sent from this page.</p>`;
  }

  function mount(el) {
    const p = SG.place();
    let box = el.querySelector('.ul-card');
    if (!box) {
      box = document.createElement('div'); box.className = 'card ul-card';
      const after = el.querySelector('.sg-tonight') || el.querySelector('.card');
      if (after) after.after(box); else el.prepend(box);
    }
    if (!p) {
      box.innerHTML = '<h3>🔭 Useful science tonight</h3><p>Own a telescope? Set your location to see which transits, planet candidates and newly found asteroids need observing tonight from your sky.</p><button type="button" class="b primary ul-setplace">📍 Set your location</button>';
      box.querySelector('.ul-setplace').addEventListener('click', SG.openPlace);
      return;
    }
    try { box.innerHTML = html(p); } catch (e) { console.error('useful tonight', e); box.innerHTML = '<p class="small muted">Useful science tonight is having a problem.</p>'; return; }
    box.querySelector('.ul-scope').addEventListener('change', ev => { LS.set('sl_scope', ev.target.value); mount(el); });
    box.querySelectorAll('[data-ul-tic]').forEach(a => a.addEventListener('click', ev => {
      const b = document.querySelector('nav.tabs button[data-tab="tess"]');
      if (b) { ev.preventDefault(); b.click(); setTimeout(() => { const c = (S.showTic && S.showTic(a.dataset.ulTic)); if (c && c.scrollIntoView) c.scrollIntoView({ block: 'start' }); }, 300); }
    }));
  }

  S.onRender((t, el) => { if (t === 'sky' && el) mount(el); });
  window.SpaceLabUseful = { items, SCOPES };
})();
