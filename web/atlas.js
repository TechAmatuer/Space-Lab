// Space Lab atlas: "a map app for space". Pan and zoom the whole night sky in different kinds of light, jump to famous places,
// see where this lab's finds are, explore the Moon, Mars and other worlds, and look up photos in NASA's public library.
// Runs on the public page AND inside Meridian through window.SpaceLab.
// This is a core file (not an "extra"): it is the only place that shows maps and photos from other sites, and only after the
// visitor opens the atlas. Sky map: the Aladin Lite viewer inside a sandboxed frame (atlas-frame.html) with sky pictures from
// ESA and the CDS. Worlds: NASA Solar System Treks map tiles. Photos: the NASA Image and Video Library.
(function () {
  'use strict';
  const S = window.SpaceLab;
  if (!S) return;
  const esc = S.esc, num = S.num;
  const D = () => S.data || {};
  const HERE = (document.currentScript && document.currentScript.src || '').replace(/atlas\.js.*$/, '');
  const LS = { get(k, d) { try { const v = localStorage.getItem(k); return v === null ? d : JSON.parse(v); } catch (_) { return d; } },
               set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (_) { /* private mode */ } } };
  const ESA = 'https://skies.esac.esa.int/', ESDC = 'https://esdchips.esac.esa.int/', TREK = 'https://trek.nasa.gov/tiles/';
  const NASA_API = 'https://images-api.nasa.gov/search', NASA_FILES = 'https://images-assets.nasa.gov/';

  // ------------------------------------------------------------------ what the atlas knows
  const LAYERS = [    // whole-sky pictures in different kinds of light
    ['vis', 'Visible light', { url: ESA + 'DSSColor' }, 'The sky as a big telescope camera sees it (Digitized Sky Survey, STScI/NASA).'],
    ['ir', 'Infrared', { url: ESA + '2MASSColor' }, 'Near-infrared light passes through dust, so hidden stars appear and dark clouds fade (2MASS).'],
    ['heat', 'Heat glow', { url: ESA + 'AllWISEColor' }, 'Mid-infrared from NASA\'s WISE telescope: warm dust lit up by young stars.'],
    ['dust', 'Cold dust', { url: ESA + 'IRISColor' }, 'Far-infrared: the cold clouds where future stars will form (IRAS).'],
    ['gaia', 'Gaia star map', { url: ESA + 'Gaia/dr3/RGB' }, 'Built from 1.8 billion stars measured one by one by ESA\'s Gaia spacecraft.'],
    ['gamma', 'Gamma rays', { url: ESA + 'FermiColor' }, 'The most energetic light there is: pulsars, black holes and the glow of our galaxy (NASA Fermi).'],
  ];
  const OVERLAYS = [  // sharper pictures that cover only some spots; shown on top of the sky
    ['hst', 'Hubble photos', { id: 'CDS/P/HST/EPO' }, 'Hubble\'s public colour pictures placed where they belong on the sky (ESA/Hubble, via CDS).'],
    ['jwst', 'Webb photos', { url: 'https://alasky.cds.unistra.fr/JWST-outreach/CDS_P_JWST_EPO' }, 'James Webb\'s public pictures placed on the sky (ESA/Webb, via CDS).'],
    ['euclid', 'Euclid deep field', { url: 'https://cdn.skies.esac.esa.int/Q1-EDFS-R4-PNG-RGB_HIPS/' }, 'ESA Euclid\'s first deep field. Zoom in: nearly every dot is a galaxy.', [61.24, -48.42, 1.2]],
    ['xray', 'X-rays', { url: ESDC + 'XMM-Newton/EPIC-RGB/' }, 'Million-degree gas, black holes and dead stars, in the patches ESA\'s XMM-Newton has looked at.'],
  ];
  const PLACES = [    // [group, name, RA°, Dec°, view width°, one line, NASA photo search]
    ['Nebulas', 'Orion Nebula', 83.82, -5.39, 1.5, 'The nearest big star nursery, 1,350 light-years away. Visible by eye as the fuzzy "star" in Orion\'s sword.', 'Orion Nebula'],
    ['Nebulas', 'Pillars of Creation', 274.70, -13.81, 0.5, 'Towers of gas and dust in the Eagle Nebula where new stars are forming.', 'Pillars of Creation'],
    ['Nebulas', 'Carina Nebula', 161.26, -59.87, 2.5, 'A huge southern star nursery, home of the unstable giant star Eta Carinae.', 'Carina Nebula'],
    ['Nebulas', 'Horsehead Nebula', 85.24, -2.46, 0.6, 'A dark cloud shaped like a horse\'s head in front of glowing gas.', 'Horsehead Nebula'],
    ['Nebulas', 'Crab Nebula', 83.63, 22.01, 0.25, 'What is left of a star that people saw explode in the year 1054.', 'Crab Nebula'],
    ['Nebulas', 'Ring Nebula', 283.40, 33.03, 0.12, 'A dying Sun-like star puffing off its outer layers.', 'Ring Nebula'],
    ['Nebulas', 'Helix Nebula', 337.41, -20.84, 0.5, 'One of the closest dying stars; sometimes called the Eye of God.', 'Helix Nebula'],
    ['Nebulas', 'Tarantula Nebula', 84.68, -69.10, 0.8, 'The most active star nursery in our neighbourhood of galaxies.', 'Tarantula Nebula'],
    ['Nebulas', 'Veil Nebula', 312.75, 30.67, 3.5, 'The wispy shell of a star that exploded about 10,000 years ago.', 'Veil Nebula'],
    ['Nebulas', 'Lagoon Nebula', 270.92, -24.38, 1.2, 'A bright cloud of gas in Sagittarius, visible in binoculars.', 'Lagoon Nebula'],
    ['Galaxies', 'Andromeda Galaxy', 10.68, 41.27, 3.5, 'The nearest big galaxy, 2.5 million light-years away: the farthest thing you can see by eye.', 'Andromeda Galaxy'],
    ['Galaxies', 'Whirlpool Galaxy', 202.47, 47.20, 0.35, 'A face-on spiral pulling on a smaller companion galaxy.', 'Whirlpool Galaxy'],
    ['Galaxies', 'Sombrero Galaxy', 190.00, -11.62, 0.25, 'A galaxy seen almost edge-on, with a dark lane of dust like a hat brim.', 'Sombrero Galaxy'],
    ['Galaxies', 'Triangulum Galaxy', 23.46, 30.66, 1.2, 'The third-largest galaxy of our local group.', 'Triangulum Galaxy'],
    ['Galaxies', 'Pinwheel Galaxy', 210.80, 54.35, 0.6, 'A grand spiral seen face-on, near the Big Dipper\'s handle.', 'Pinwheel Galaxy M101'],
    ['Galaxies', 'Centaurus A', 201.37, -43.02, 0.6, 'A galaxy with a giant black hole firing jets, the result of two galaxies merging.', 'Centaurus A'],
    ['Galaxies', 'Large Magellanic Cloud', 80.89, -69.76, 8, 'A small galaxy orbiting the Milky Way, seen from the southern half of Earth.', 'Large Magellanic Cloud'],
    ['Galaxies', 'Stephan\'s Quintet', 339.01, 33.96, 0.15, 'Five galaxies close together; four of them are colliding.', 'Stephan\'s Quintet'],
    ['Galaxies', 'Cartwheel Galaxy', 9.42, -33.72, 0.1, 'A ring-shaped galaxy made when a smaller galaxy punched through it.', 'Cartwheel Galaxy'],
    ['Star clusters', 'Pleiades', 56.75, 24.12, 2.5, 'The Seven Sisters: a young cluster of hot blue stars, easy to see by eye.', 'Pleiades'],
    ['Star clusters', 'Omega Centauri', 201.70, -47.48, 0.9, 'About ten million stars packed into a ball: the biggest cluster of our galaxy.', 'Omega Centauri'],
    ['Star clusters', 'Hercules Cluster', 250.42, 36.46, 0.5, 'A ball of several hundred thousand old stars, a favourite of northern stargazers.', 'M13 globular cluster'],
    ['Black holes', 'M87', 187.71, 12.39, 0.3, 'The galaxy whose central black hole was the first ever photographed (2019).', 'M87 black hole'],
    ['Black holes', 'Centre of the Milky Way', 266.42, -29.01, 3, 'Our galaxy\'s heart, hiding a black hole four million times the Sun\'s mass. Try the infrared layer here.', 'Sagittarius A*'],
    ['Black holes', 'Cygnus X-1', 299.59, 35.20, 0.5, 'The first object accepted to be a black hole, slowly eating a giant blue star.', 'Cygnus X-1'],
    ['Deep fields', 'Hubble Ultra Deep Field', 53.16, -27.79, 0.1, 'A "blank" patch where Hubble stared for days and found 10,000 galaxies.', 'Hubble Ultra Deep Field'],
    ['Deep fields', 'Webb\'s First Deep Field', 110.84, -73.45, 0.1, 'Webb\'s first public picture: a cluster of galaxies bending the light of galaxies far behind it.', 'Webb First Deep Field SMACS 0723'],
    ['Deep fields', 'Euclid Deep Field South', 61.24, -48.42, 1.2, 'One of the patches ESA\'s Euclid revisits to map dark matter. Switch on the Euclid layer.', 'Euclid deep field'],
    ['Deep fields', 'Perseus galaxy cluster', 49.95, 41.51, 1, 'A thousand galaxies held together mostly by dark matter.', 'Perseus cluster'],
    ['Planet hosts', 'TRAPPIST-1', 346.62, -5.04, 0.2, 'A small red star with seven Earth-sized planets, 40 light-years away.', 'TRAPPIST-1'],
    ['Planet hosts', 'Proxima Centauri', 217.43, -62.68, 0.5, 'The closest star to the Sun, with at least one planet.', 'Proxima Centauri'],
    ['Planet hosts', 'Kepler\'s field of view', 290.67, 44.50, 14, 'The patch NASA\'s Kepler telescope stared at for four years, finding over 2,600 planets.', 'Kepler exoplanets'],
  ];
  const WORLDS = {    // global maps from NASA Solar System Treks (longitude −180…180, north up)
    moon: { name: 'The Moon', icon: '🌕', path: 'Moon/EQ/LRO_WAC_Mosaic_Global_303ppd_v02', max: 8, km: 1737.4, credit: 'NASA / GSFC / Arizona State University (Lunar Reconnaissance Orbiter)',
      pins: [['Apollo 11 (first people on the Moon, 1969)', 0.674, 23.473, 'Apollo 11 lunar surface'], ['Apollo 12', -3.012, -23.422, 'Apollo 12 lunar surface'], ['Apollo 14', -3.645, -17.471, 'Apollo 14 lunar surface'],
        ['Apollo 15 (first Moon car)', 26.132, 3.634, 'Apollo 15 lunar rover'], ['Apollo 16', -8.973, 15.500, 'Apollo 16 lunar surface'], ['Apollo 17 (last people, 1972)', 20.191, 30.772, 'Apollo 17 lunar surface'],
        ['Chang\'e 4 (first far-side landing, China 2019)', -45.445, 177.599, 'far side of the Moon'], ['Chandrayaan-3 (India 2023)', -69.373, 32.319, 'Moon south pole'],
        ['SLIM (Japan 2024)', -13.316, 25.251, 'Moon surface'], ['Blue Ghost (Firefly 2025)', 18.56, 61.81, 'Blue Ghost lunar lander'], ['Tycho crater', -43.31, -11.36, 'Tycho crater'],
        ['Copernicus crater', 9.62, -20.08, 'Copernicus crater Moon'], ['Shackleton crater (south pole, Artemis target area)', -89.0, 129.78, 'Shackleton crater']] },
    mars: { name: 'Mars', icon: '🔴', path: 'Mars/EQ/Mars_Viking_MDIM21_ClrMosaic_global_232m', max: 7, km: 3389.5, credit: 'NASA / JPL / USGS (Viking orbiters)',
      alt: ['Heights in colour', 'Mars/EQ/Mars_MGS_MOLA_ClrShade_merge_global_463m', 'NASA / JPL / GSFC (Mars Global Surveyor laser altimeter): blue is low, red and white are high'],
      pins: [['Perseverance rover (2021–)', 18.445, 77.451, 'Perseverance rover Mars'], ['Curiosity rover (2012–)', -4.590, 137.442, 'Curiosity rover Mars'], ['Opportunity rover', -1.946, -5.527, 'Opportunity rover Mars'],
        ['Spirit rover', -14.568, 175.473, 'Spirit rover Mars'], ['InSight lander', 4.502, 135.623, 'InSight lander Mars'], ['Phoenix lander', 68.22, -125.75, 'Phoenix lander Mars'],
        ['Viking 1 (first long-lived lander, 1976)', 22.27, -47.95, 'Viking 1 Mars surface'], ['Viking 2', 47.64, 134.29, 'Viking 2 Mars surface'], ['Pathfinder and Sojourner', 19.13, -33.22, 'Mars Pathfinder Sojourner'],
        ['Zhurong rover (China 2021)', 25.066, 109.925, 'Utopia Planitia Mars'], ['Olympus Mons (tallest volcano known)', 18.65, -133.8, 'Olympus Mons'],
        ['Valles Marineris (canyon as long as the USA)', -13.9, -59.2, 'Valles Marineris'], ['Hellas basin (giant impact scar)', -42.4, 70.5, 'Hellas Planitia']] },
    mercury: { name: 'Mercury', icon: '🪨', path: 'Mercury/EQ/Mercury_MESSENGER_MDIS_Basemap_EnhancedColor_Mosaic_Global_665m', max: 7, km: 2439.7, credit: 'NASA / Johns Hopkins APL / Carnegie (MESSENGER); colours exaggerated to show different rocks', pins: [] },
    vesta: { name: 'Vesta', icon: '🥔', path: 'Vesta/EQ/Vesta_Dawn_FC_HAMO_Mosaic_Global_74ppd', max: 6, km: 262.7, credit: 'NASA / JPL-Caltech / UCLA / MPS / DLR / IDA (Dawn)', pins: [] },
    ceres: { name: 'Ceres', icon: '⚪', path: 'Ceres/EQ/Ceres_Dawn_FC_HAMO_ClrShade_DLR_Global_60ppd_Oct2016', max: 5, km: 469.7, credit: 'NASA / JPL-Caltech / UCLA / MPS / DLR / IDA (Dawn); colour shows height', pins: [] },
  };
  const QUICK = ['James Webb', 'Hubble', 'Apollo', 'Mars rover', 'Saturn', 'Jupiter', 'Earth from space', 'Nebula', 'Artemis', 'Aurora'];

  // ------------------------------------------------------------------ small helpers
  const hms = ra => { const h = ((ra % 360) + 360) % 360 / 15, hh = Math.floor(h), mm = Math.floor((h - hh) * 60); return `${hh}h ${String(mm).padStart(2, '0')}m`; };
  const dms = d => `${d < 0 ? '−' : '+'}${Math.abs(d).toFixed(1)}°`;
  const fovWords = f => f >= 60 ? 'a big part of the sky' : f >= 10 ? 'about a constellation' : f >= 1 ? `${num(f / 0.5, 0)} full Moons wide` : f >= 0.1 ? `${num(f * 60, 0)}′ wide (the Moon is 30′)` : `${num(f * 3600, 0)}″ wide`;
  const esasky = (ra, dec, fov) => `https://sky.esa.int/esasky/?target=${(+ra).toFixed(4)}%20${(+dec).toFixed(4)}&fov=${(+fov || 1).toFixed(2)}`;
  const agreed = () => !!LS.get('sl_atlas_ok', false);
  let view = null;      // the atlas tab's element while it is open
  let sky = null;       // {frame, ready, queue, layer, overlay, pins:{}}
  let sel = null;       // the thing shown in the info box

  // the lab's own finds and lists that have a place on the sky
  function pinSets() {
    const d = D(), b = d.beyond || {};
    const cands = ((d.tess || {}).cands || []).filter(c => c.star && isFinite(c.star.ra) && isFinite(c.star.dec));
    return [
      { key: 'cands', name: 'This lab\'s planet candidates', color: '#ffd166', shape: 'circle', items: cands.map(c => ({ id: 'c' + c.id, ra: c.star.ra, dec: c.star.dec, label: 'TIC ' + c.tic,
          info: { title: 'TIC ' + c.tic, text: `A star where this lab found a repeating dip${c.period ? ` every ${num(c.period, 2)} days` : ''}. Status: ${c.status || 'under review'}. It is a candidate, not a confirmed planet.`, url: ((d.site || {}).url) ? d.site.url + 'c/' + c.tic + '.html' : '', urlText: 'see the evidence and give your verdict' } })) },
      { key: 'habitable', name: 'Possibly habitable worlds', color: '#7bd88f', shape: 'rhomb', items: (b.habitable || []).filter(p => isFinite(p.ra) && isFinite(p.dec)).map((p, i) => ({ id: 'h' + i, ra: p.ra, dec: p.dec, label: p.pl_name,
          info: { title: p.pl_name, text: `A confirmed planet ${p.sy_dist ? num(p.sy_dist * 3.262, 0) + ' light-years away' : ''}${p.pl_rade ? `, about ${num(p.pl_rade, 1)}× the size of Earth` : ''}, getting a similar amount of light to Earth. You are looking at its star${p.constellation ? ' in ' + p.constellation : ''}; the planet itself is far too faint to see.`, q: p.hostname } })) },
      { key: 'exowatch', name: 'Exoplanet Watch targets', color: '#8ecae6', shape: 'square', items: ((d.exowatch || {}).stale || []).filter(p => isFinite(p.ra) && isFinite(p.dec)).map((p, i) => ({ id: 'e' + i, ra: p.ra, dec: p.dec, label: p.name || p.planet || '',
          info: { title: p.name || p.planet || 'Exoplanet Watch target', text: 'A known planet whose transit times need fresh measurements. This lab times its transits for NASA\'s Exoplanet Watch.' } })) },
      { key: 'sn', name: 'Fresh supernova candidates', color: '#ff7b72', shape: 'cross', items: (b.supernovae || []).filter(s => isFinite(s.ra) && isFinite(s.dec)).map((s, i) => ({ id: 's' + i, ra: s.ra, dec: s.dec, label: s.id,
          info: { title: 'Supernova candidate ' + s.id, text: 'A new point of light in a distant galaxy, flagged by the ALeRCE alert system as a likely exploding star. It will not show in these older sky pictures: that is the point.', url: s.url } })) },
      { key: 'planets', name: 'Planets tonight', color: '#f4a261', shape: 'triangle', items: (((b.sky || {}).planets) || []).filter(p => isFinite(p.ra) && isFinite(p.dec)).map((p, i) => ({ id: 'p' + i, ra: p.ra, dec: p.dec, label: p.name,
          info: { title: p.name + ' (where it is this week)', text: 'Planets move, so the old sky pictures here will not show it, but this is where to look among the stars.', q: p.name } })) },
    ].filter(s => s.items.length);
  }
  const placeItems = () => PLACES.map((p, i) => ({ id: 'P' + i, ra: p[2], dec: p[3], fov: p[4], label: p[1], info: { title: p[1], text: p[5], q: p[6] } }));

  // ------------------------------------------------------------------ the info box and NASA photos
  function info(item) {
    sel = item;
    const box = view && view.querySelector('.at-info');
    if (!box) return;
    if (!item) { box.innerHTML = '<p class="small muted">Tap a place, a pin or a landing site to learn about it and see NASA photos.</p>'; return; }
    const i = item.info || {}, sky_ = isFinite(item.ra);
    box.innerHTML = `<h3>${esc(i.title || item.label)}</h3><p>${esc(i.text || '')}</p>
      <div class="row small">${sky_ ? `<span class="muted mono">RA ${hms(item.ra)} · Dec ${dms(item.dec)}</span>` : isFinite(item.lat) ? `<span class="muted mono">${Math.abs(item.lat).toFixed(2)}°${item.lat < 0 ? 'S' : 'N'} · ${Math.abs(item.lon).toFixed(2)}°${item.lon < 0 ? 'W' : 'E'}</span>` : ''}
        ${i.q ? `<button class="b at-photos" data-q="${esc(i.q)}">📷 NASA photos</button>` : ''}
        ${i.url ? `<a href="${esc(i.url)}" target="_blank" rel="noopener">${esc(i.urlText || 'details')} ↗</a>` : ''}
        ${sky_ ? `<a href="${esasky(item.ra, item.dec, item.fov || 0.5)}" target="_blank" rel="noopener">open in ESASky ↗</a>` : ''}</div>
      <div class="at-gallery"></div>`;
    const ph = box.querySelector('.at-photos');
    if (ph) ph.addEventListener('click', () => photos(box.querySelector('.at-gallery'), ph.dataset.q));
  }
  const safeImg = u => (typeof u === 'string' && u.startsWith(NASA_FILES)) ? u.replace(/ /g, '%20') : '';
  function photos(host, q, page) {
    if (!host || !q) return;
    host.innerHTML = '<p class="small muted">Asking NASA\'s photo library…</p>';
    S.track('atlas/photos', true);
    fetch(`${NASA_API}?q=${encodeURIComponent(q)}&media_type=image&page_size=12&page=${page || 1}`)
      .then(r => (r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status))))
      .then(js => {
        const items = ((js.collection || {}).items || []).map(it => ({ d: (it.data || [])[0] || {}, th: safeImg(((it.links || [])[0] || {}).href) })).filter(x => x.th && x.d.nasa_id);
        const total = ((js.collection || {}).metadata || {}).total_hits || items.length;
        if (!items.length) { host.innerHTML = `<p class="small muted">NASA's library has no photos for "${esc(q)}". Try other words.</p>`; return; }
        host.innerHTML = `<p class="small muted">${Number(total).toLocaleString()} photos for "${esc(q)}" in NASA's public library. Tap one to enlarge.</p>
          <div class="at-thumbs">${items.map((x, i) => `<button class="at-thumb" data-i="${i}" title="${esc(x.d.title || '')}"><img src="${esc(x.th)}" alt="${esc(x.d.title || 'NASA photo')}" loading="lazy" referrerpolicy="no-referrer"></button>`).join('')}</div>
          <div class="at-big"></div>${total > (page || 1) * 12 ? '<button class="b at-more">More photos</button>' : ''}`;
        host.querySelectorAll('.at-thumb').forEach(b => b.addEventListener('click', () => {
          const x = items[+b.dataset.i], d = x.d, id = encodeURIComponent(d.nasa_id), big = host.querySelector('.at-big');
          const text = String(d.description || '').replace(/<[^>]+>/g, ' ');
          big.innerHTML = `<figure><img src="${esc(x.th.replace(/~(thumb|small)\.jpg$/, '~medium.jpg'))}" alt="${esc(d.title || '')}" referrerpolicy="no-referrer" data-th="${esc(x.th)}">
            <figcaption><b>${esc(d.title || '')}</b> <span class="small muted">${esc((d.date_created || '').slice(0, 10))}${d.center ? ' · NASA ' + esc(d.center) : ''}${d.secondary_creator ? ' · ' + esc(d.secondary_creator) : ''}</span>
            <p class="small">${esc(text.slice(0, 700))}${text.length > 700 ? '…' : ''}</p>
            <p class="small"><a href="https://images.nasa.gov/details/${id}" target="_blank" rel="noopener">Full size and details at images.nasa.gov ↗</a></p></figcaption></figure>`;
          const im = big.querySelector('img');
          im.addEventListener('error', () => { if (im.src !== im.dataset.th) im.src = im.dataset.th; }, { once: true });
          big.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }));
        const more = host.querySelector('.at-more');
        if (more) more.addEventListener('click', () => photos(host, q, (page || 1) + 1));
      })
      .catch(e => { host.innerHTML = `<p class="small warn-t">NASA's photo library did not answer (${esc(e.message)}). Try again in a moment.</p>`; });
  }

  // ------------------------------------------------------------------ the night sky (Aladin Lite in a sandboxed frame)
  const send = m => { if (!sky) return; if (sky.ready) sky.frame.contentWindow.postMessage(m, '*'); else sky.queue.push(m); };
  function onFrame(e) {
    if (!sky || !sky.frame || e.source !== sky.frame.contentWindow || !e.data || typeof e.data.t !== 'string') return;
    const m = e.data, st = view && view.querySelector('.at-status');
    if (m.t === 'hello') send0({ t: 'init', url: LAYERS[0][2].url, ra: sky.start[0], dec: sky.start[1], fov: sky.start[2] });
    else if (m.t === 'ready') { sky.ready = true; sky.queue.splice(0).forEach(send); if (st) st.textContent = ''; }
    else if (m.t === 'error') { if (st) st.textContent = 'The sky map had a problem: ' + String(m.why || '').slice(0, 120); }
    else if (m.t === 'pos') {
      sky.at = [m.ra, m.dec, m.fov];
      const p = view && view.querySelector('.at-pos');
      if (p && isFinite(m.ra)) p.textContent = `RA ${hms(m.ra)} · Dec ${dms(m.dec)}${m.fov ? ' · view ' + fovWords(m.fov) : ''}`;
      LS.set('sl_atlas_view', [+(+m.ra).toFixed(3), +(+m.dec).toFixed(3), +(+m.fov || 60).toFixed(3)]);
    } else if (m.t === 'pin') {
      const all = [...placeItems(), ...pinSets().flatMap(s => s.items)];
      const it = all.find(x => x.id === m.id);
      if (it) info(it);
    } else if (m.t === 'layer' || m.t === 'overlay') {
      if (st) st.textContent = m.ok ? '' : 'That picture layer cannot be reached from your network right now, so the map kept the previous one.';
      if (!m.ok && view) view.querySelectorAll(`.at-layer[data-k="${m.k}"]`).forEach(b => b.classList.remove('on'));
    }
  }
  const send0 = m => sky.frame.contentWindow.postMessage(m, '*');
  window.addEventListener('message', onFrame);
  const fly = (ra, dec, fov) => send({ t: 'goto', ra: +ra, dec: +dec, fov: +fov || undefined });
  function showPins(key, on) {
    const set = key === 'places' ? { key, color: '#ffffff', shape: 'plus', size: 12, labels: true, items: placeItems() } : pinSets().find(s => s.key === key);
    if (!set) return;
    send({ t: 'pins', key, color: set.color, shape: set.shape, size: set.size, labels: !!set.labels || set.items.length <= 60, items: on ? set.items.map(i => ({ id: i.id, ra: i.ra, dec: i.dec, label: i.label })) : [] });
    const off = LS.get('sl_atlas_off', []).filter(k => k !== key);
    LS.set('sl_atlas_off', on ? off : off.concat(key));
  }
  function find(q) {
    const st = view.querySelector('.at-status'), s = q.trim().toLowerCase();
    if (!s) return;
    const all = [...placeItems(), ...pinSets().flatMap(x => x.items)];
    const hit = all.find(x => String(x.label).toLowerCase() === s) || all.find(x => String(x.label).toLowerCase().includes(s));
    const co = /^\s*(-?\d+(?:\.\d+)?)[\s,]+([+-]?\d+(?:\.\d+)?)\s*$/.exec(q);
    if (hit) { fly(hit.ra, hit.dec, hit.fov || 0.5); info(hit); st.textContent = ''; }
    else if (co && +co[1] >= 0 && +co[1] < 360 && Math.abs(+co[2]) <= 90) { fly(+co[1], +co[2]); st.textContent = ''; }
    else { st.textContent = `"${q}" is not on the atlas list. Type RA and Dec in degrees (like 83.8 -5.4), or look it up in NASA's photos below.`; info({ label: q, info: { title: q, text: 'Not a place this atlas knows, but NASA\'s library may have photos.', q } }); }
  }
  function skyMode(stage) {
    const sets = pinSets(), off = LS.get('sl_atlas_off', []), groups = [...new Set(PLACES.map(p => p[0]))];
    stage.innerHTML = `<div class="row at-bar"><input type="search" class="at-q" placeholder="Find: Orion, Andromeda, TIC number, or 83.8 -5.4" aria-label="Find a place on the sky"><button class="b at-find">Go</button>
        <span class="spacer"></span><span class="small muted mono at-pos"></span></div>
      <div class="at-map"><iframe class="at-frame" title="Interactive map of the night sky" sandbox="allow-scripts" referrerpolicy="no-referrer" src="${esc(HERE)}atlas-frame.html"></iframe></div>
      <p class="small warn-t at-status">Loading the sky map…</p>
      <div class="at-set"><b class="small">See the sky in:</b> ${LAYERS.map((l, i) => `<button class="b at-layer at-base${i ? '' : ' on'}" data-k="${l[0]}" title="${esc(l[3])}">${esc(l[1])}</button>`).join('')}</div>
      <div class="at-set"><b class="small">Add sharp photos on top:</b> ${OVERLAYS.map(l => `<button class="b at-layer at-over" data-k="${l[0]}" title="${esc(l[3])}">${esc(l[1])}</button>`).join('')}</div>
      <p class="small muted at-layerinfo">${esc(LAYERS[0][3])}</p>
      <div class="at-set"><b class="small">Show on the map:</b> <label class="small"><input type="checkbox" class="at-pin" data-k="places" ${off.includes('places') ? '' : 'checked'}> famous places</label>
        ${sets.map(s => `<label class="small"><input type="checkbox" class="at-pin" data-k="${s.key}" ${off.includes(s.key) ? '' : 'checked'}> <span class="at-dot" style="background:${s.color}"></span>${esc(s.name)} (${s.items.length})</label>`).join('')}</div>
      <details class="at-places" open><summary><b>Famous places</b> <span class="small muted">tap one to fly there</span></summary>
        ${groups.map(g => `<div class="at-set"><span class="small muted">${esc(g)}:</span> ${PLACES.map((p, i) => p[0] === g ? `<button class="b at-place" data-i="${i}">${esc(p[1])}</button>` : '').join('')}</div>`).join('')}</details>`;
    const saved = LS.get('sl_atlas_view', null);
    sky = { frame: stage.querySelector('.at-frame'), ready: false, queue: [], start: Array.isArray(saved) && saved.length === 3 && saved.every(isFinite) ? saved : [83.82, -5.39, 20] };
    showPins('places', !off.includes('places'));
    sets.forEach(s => showPins(s.key, !off.includes(s.key)));
    stage.querySelectorAll('.at-place').forEach(b => b.addEventListener('click', () => { const it = placeItems()[+b.dataset.i]; fly(it.ra, it.dec, it.fov); info(it); S.track('atlas/place', true); }));
    stage.querySelectorAll('.at-pin').forEach(c => c.addEventListener('change', () => showPins(c.dataset.k, c.checked)));
    stage.querySelectorAll('.at-base').forEach(b => b.addEventListener('click', () => {
      const l = LAYERS.find(x => x[0] === b.dataset.k);
      stage.querySelectorAll('.at-base').forEach(x => x.classList.toggle('on', x === b));
      stage.querySelector('.at-layerinfo').textContent = l[3];
      send(Object.assign({ t: 'layer', k: l[0], name: l[1] }, l[2]));
    }));
    stage.querySelectorAll('.at-over').forEach(b => b.addEventListener('click', () => {
      const l = OVERLAYS.find(x => x[0] === b.dataset.k), on = !b.classList.contains('on');
      stage.querySelectorAll('.at-over').forEach(x => x.classList.toggle('on', on && x === b));
      stage.querySelector('.at-layerinfo').textContent = on ? l[3] + ' Fly to a famous place and zoom in to see them.' : '';
      send(on ? Object.assign({ t: 'overlay', k: l[0], name: l[1] }, l[2]) : { t: 'overlay' });
      if (on && l[4]) fly(l[4][0], l[4][1], l[4][2]);
    }));
    const q = stage.querySelector('.at-q'), go = () => find(q.value);
    stage.querySelector('.at-find').addEventListener('click', go);
    q.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
  }

  // ------------------------------------------------------------------ other worlds (NASA Trek tiles; a small map viewer)
  function worldMode(stage, key) {
    const W = WORLDS[key], T = 256;
    stage.innerHTML = `<div class="row at-bar"><b>${W.icon} ${esc(W.name)}</b>${W.alt ? `<button class="b at-alt">${esc(W.alt[0])}</button>` : ''}<span class="spacer"></span><span class="small muted mono at-pos"></span></div>
      <div class="at-map at-world" tabindex="0" aria-label="Map of ${esc(W.name)}. Drag to move, scroll or use the buttons to zoom."><div class="at-tiles"></div><div class="at-marks"></div>
        <div class="at-zoom"><button class="b at-in" aria-label="Zoom in">+</button><button class="b at-out" aria-label="Zoom out">−</button></div></div>
      <p class="small muted at-credit"></p>
      ${W.pins.length ? `<div class="at-set"><b class="small">Go to:</b> ${W.pins.map((p, i) => `<button class="b at-site" data-i="${i}">${esc(p[0].replace(/ \(.*$/, ''))}</button>`).join('')}</div>` : ''}`;
    const host = stage.querySelector('.at-world'), tiles = host.querySelector('.at-tiles'), marks = host.querySelector('.at-marks'), posEl = stage.querySelector('.at-pos');
    let z = 2, cx = 0.5, cy = 0.5, path = W.path, credit = W.credit, live = new Map();
    const WW = () => T * Math.pow(2, z + 1), HH = () => T * Math.pow(2, z);
    const pins = W.pins.map((p, i) => ({ id: 'w' + i, lat: p[1], lon: p[2], label: p[0], info: { title: p[0], text: `On ${W.name} at ${Math.abs(p[1]).toFixed(1)}°${p[1] < 0 ? 'S' : 'N'}, ${Math.abs(p[2]).toFixed(1)}°${p[2] < 0 ? 'W' : 'E'}.`, q: p[3] } }));
    pins.forEach(p => { const b = document.createElement('button'); b.className = 'at-mark'; b.title = p.label; b.setAttribute('aria-label', p.label); b.addEventListener('click', e => { e.stopPropagation(); info(p); }); marks.appendChild(b); p.el = b; });
    function draw() {
      const w = host.clientWidth || 600, h = host.clientHeight || 400, cols = Math.pow(2, z + 1), rows = Math.pow(2, z);
      cy = HH() <= h ? 0.5 : Math.min(1 - h / 2 / HH(), Math.max(h / 2 / HH(), cy));
      cx = ((cx % 1) + 1) % 1;
      const px = cx * WW(), py = cy * HH(), want = new Set();
      for (let y = Math.max(0, Math.floor((py - h / 2) / T)); y <= Math.min(rows - 1, Math.floor((py + h / 2) / T)); y++) {
        for (let x = Math.floor((px - w / 2) / T); x <= Math.floor((px + w / 2) / T); x++) {
          const k = `${z}/${y}/${x}`; want.add(k);
          let im = live.get(k);
          if (!im) {
            im = document.createElement('img'); im.alt = ''; im.draggable = false; im.referrerPolicy = 'no-referrer'; im.className = 'at-tile';
            im.addEventListener('error', () => { im.style.visibility = 'hidden'; });
            im.src = `${TREK}${path}/1.0.0/default/default028mm/${z}/${y}/${((x % cols) + cols) % cols}.jpg`;
            tiles.appendChild(im); live.set(k, im);
          }
          im.style.transform = `translate(${Math.round(x * T - px + w / 2)}px,${Math.round(y * T - py + h / 2)}px)`;
        }
      }
      for (const [k, im] of live) if (!want.has(k)) { im.remove(); live.delete(k); }
      for (const p of pins) {
        const u = (p.lon + 180) / 360, v = (90 - p.lat) / 180, dx = ((u - cx + 1.5) % 1) - 0.5;
        p.el.style.transform = `translate(${Math.round(w / 2 + dx * WW())}px,${Math.round(h / 2 + (v - cy) * HH())}px)`;
      }
      const lat = 90 - cy * 180, lon = cx * 360 - 180, across = 2 * Math.PI * W.km * Math.cos(lat * Math.PI / 180) * Math.min(1, w / WW());
      posEl.textContent = `${Math.abs(lat).toFixed(1)}°${lat < 0 ? 'S' : 'N'} ${Math.abs(lon).toFixed(1)}°${lon < 0 ? 'W' : 'E'} · the map is about ${Math.round(across).toLocaleString()} km across · zoom ${z}/${W.max}`;
      stage.querySelector('.at-credit').textContent = 'Map: ' + credit + ', through NASA Solar System Treks.';
    }
    function zoom(dz, mx, my) {
      const nz = Math.max(1, Math.min(W.max, z + dz));
      if (nz === z) return;
      const w = host.clientWidth, h = host.clientHeight, ox = (mx === undefined ? w / 2 : mx) - w / 2, oy = (my === undefined ? h / 2 : my) - h / 2;
      const u = cx + ox / WW(), v = cy + oy / HH();
      z = nz; cx = u - ox / WW(); cy = v - oy / HH();
      live.forEach(im => im.remove()); live.clear();
      draw();
    }
    const at = e => { const r = host.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; };
    const ptr = new Map(); let pinch = 0, wheelT = 0;
    host.addEventListener('pointerdown', e => { if (e.target.closest('.at-mark, .at-zoom')) return; ptr.set(e.pointerId, [e.clientX, e.clientY]); try { host.setPointerCapture(e.pointerId); } catch (_) { /* fine */ } if (ptr.size === 2) { const [a, b] = [...ptr.values()]; pinch = Math.hypot(a[0] - b[0], a[1] - b[1]); } });
    host.addEventListener('pointermove', e => {
      const p = ptr.get(e.pointerId); if (!p) return;
      if (ptr.size === 1) { cx -= (e.clientX - p[0]) / WW(); cy -= (e.clientY - p[1]) / HH(); draw(); }
      ptr.set(e.pointerId, [e.clientX, e.clientY]);
      if (ptr.size === 2 && pinch) { const [a, b] = [...ptr.values()], d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (d > pinch * 1.5) { zoom(1); pinch = d; } else if (d < pinch / 1.5) { zoom(-1); pinch = d; } }
    });
    const up = e => { ptr.delete(e.pointerId); pinch = 0; };
    host.addEventListener('pointerup', up); host.addEventListener('pointercancel', up);
    host.addEventListener('wheel', e => { e.preventDefault(); const now = Date.now(); if (now - wheelT < 180) return; wheelT = now; const [x, y] = at(e); zoom(e.deltaY < 0 ? 1 : -1, x, y); }, { passive: false });
    host.addEventListener('dblclick', e => { const [x, y] = at(e); zoom(1, x, y); });
    host.addEventListener('keydown', e => { const k = e.key, s = 60; if (k === '+' || k === '=') zoom(1); else if (k === '-') zoom(-1); else if (k === 'ArrowLeft') cx -= s / WW(); else if (k === 'ArrowRight') cx += s / WW(); else if (k === 'ArrowUp') cy -= s / HH(); else if (k === 'ArrowDown') cy += s / HH(); else return; e.preventDefault(); draw(); });
    stage.querySelector('.at-in').addEventListener('click', () => zoom(1));
    stage.querySelector('.at-out').addEventListener('click', () => zoom(-1));
    stage.querySelectorAll('.at-site').forEach(b => b.addEventListener('click', () => { const p = pins[+b.dataset.i]; z = Math.min(W.max, Math.max(z, 5)); cx = (p.lon + 180) / 360; cy = (90 - p.lat) / 180; live.forEach(im => im.remove()); live.clear(); draw(); info(p); }));
    const alt = stage.querySelector('.at-alt');
    if (alt) alt.addEventListener('click', () => { const on = alt.classList.toggle('on'); path = on ? W.alt[1] : W.path; credit = on ? W.alt[2] : W.credit; live.forEach(im => im.remove()); live.clear(); draw(); });
    if (window.ResizeObserver) new ResizeObserver(() => { if (host.isConnected) draw(); }).observe(host);
    draw();
  }

  // ------------------------------------------------------------------ NASA photo search
  function photoMode(stage) {
    stage.innerHTML = `<div class="row at-bar"><input type="search" class="at-q" placeholder="Search NASA's photos: Saturn rings, Apollo 17, Webb…" aria-label="Search NASA's photo library"><button class="b primary at-find">Search</button></div>
      <div class="at-set">${QUICK.map(q => `<button class="b at-quick">${esc(q)}</button>`).join('')}</div>
      <div class="at-gallery"></div>
      <p class="small muted">More than 140,000 photos from 60 years of NASA missions, free to use (NASA Image and Video Library). More picture archives:
        <a href="https://esahubble.org/images/" target="_blank" rel="noopener">ESA/Hubble ↗</a> · <a href="https://esawebb.org/images/" target="_blank" rel="noopener">ESA/Webb ↗</a> ·
        <a href="https://www.eso.org/public/images/" target="_blank" rel="noopener">European Southern Observatory ↗</a> · <a href="https://www.esa.int/ESA_Multimedia/Images" target="_blank" rel="noopener">ESA ↗</a> ·
        <a href="https://jda.jaxa.jp/en/" target="_blank" rel="noopener">JAXA (Japan) ↗</a></p>`;
    const q = stage.querySelector('.at-q'), g = stage.querySelector('.at-gallery'), go = v => { if (v.trim()) { q.value = v; photos(g, v.trim()); } };
    stage.querySelector('.at-find').addEventListener('click', () => go(q.value));
    q.addEventListener('keydown', e => { if (e.key === 'Enter') go(q.value); });
    stage.querySelectorAll('.at-quick').forEach(b => b.addEventListener('click', () => go(b.textContent)));
  }

  // ------------------------------------------------------------------ the tab
  function open(mode) {
    const stage = view.querySelector('.at-stage');
    sky = null;
    view.querySelectorAll('.at-mode').forEach(b => b.classList.toggle('on', b.dataset.m === mode));
    LS.set('sl_atlas_mode', mode);
    info(null);
    if (mode === 'sky') skyMode(stage); else if (mode === 'photos') photoMode(stage); else worldMode(stage, mode);
    S.track('atlas/' + mode, true);
  }
  function render(el) {
    view = el;
    const modes = [['sky', '🌌 Night sky'], ...Object.entries(WORLDS).map(([k, w]) => [k, `${w.icon} ${w.name}`]), ['photos', '📷 NASA photos']];
    el.innerHTML = `<div class="card at-top"><h3>🗺️ Space atlas: a map app for space</h3>
        <p class="small">Drag to move, scroll or pinch to zoom. Fly to famous nebulas and galaxies, switch between kinds of light, see where this lab's finds are, or wander over the Moon and Mars to the landing sites.</p>
        <div class="at-set at-modes">${modes.map(m => `<button class="b at-mode" data-m="${m[0]}">${esc(m[1])}</button>`).join('')}</div></div>
      ${agreed() ? '' : `<div class="card at-ask"><p><b>Before the maps load:</b> the map pictures and photos come straight from NASA, the European Space Agency and the CDS astronomy data centre in France, and the sky-map viewer (Aladin Lite, open source) from the jsDelivr network. Like any website, those servers see your internet address. Space Lab sends them nothing else: your saved place and your lists stay in your browser.</p>
        <button class="b primary at-yes">Open the atlas</button></div>`}
      <div class="card at-stage"></div><div class="card at-info"></div>`;
    const start = () => { const m = LS.get('sl_atlas_mode', 'sky'); open(m === 'sky' || m === 'photos' || WORLDS[m] ? m : 'sky'); };
    el.querySelectorAll('.at-mode').forEach(b => b.addEventListener('click', () => { if (agreed()) open(b.dataset.m); else { LS.set('sl_atlas_mode', b.dataset.m); el.querySelector('.at-yes').focus(); } }));
    const yes = el.querySelector('.at-yes');
    if (yes) yes.addEventListener('click', () => { LS.set('sl_atlas_ok', true); el.querySelector('.at-ask').remove(); start(); });
    if (agreed()) start(); else { el.querySelector('.at-stage').innerHTML = '<p class="small muted">The atlas opens here.</p>'; info(null); }
  }
  S.addTab('atlas', 'Space atlas', render);
  window.SpaceLabAtlas = { PLACES, LAYERS, OVERLAYS, WORLDS, pinSets, placeItems, hms, dms, fovWords };
})();
