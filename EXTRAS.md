# Building extras for the public page (for people and AI coding agents)

Fun and learning features for https://techamatuer.github.io/Space-Lab/ live in **six files only**:
`web/extras.js` (games, creative tools, My lab), `web/beyond.js` (Sky watch and Beyond astronomy tabs),
`web/home.js` (Home page, candidate of the day, radar, Results, the TESS big picture), `web/stargazing.js` (Stargazing tab:
place by ZIP code, dark-sky finder, events and calendar reminders, "What's that in the sky?", the header location chip and
"Tonight from your place"), `web/asteroids.js` (the asteroid guide) and `web/extras.css`. Everything else is the core and is off-limits for extras work.
`web/atlas.js` + `web/atlas-frame.html` (the Space atlas) are core files too: they are the only place that shows maps and photos from other sites (NASA, ESA, CDS), only after the visitor opens the atlas, with the third-party sky viewer locked inside a sandboxed frame.
`web/universe.js` (the 3D universe) is a core file as well: plain WebGL, no libraries, and it reads only this site's own files (`galaxies.bin`, `galaxies.txt`, built by `spacelab/universe.py`).

The same files run **on the local Space Lab page** too: `web/app.js` provides the same `SpaceLab` API there (with the same public
data from `api/public-data`), so whatever you build appears on both the public page and the owner's dashboard.

## Why this setup
- A bug in `extras.js` cannot break the page: every hook runs inside try/catch.
- Extras only *read* the data already loaded (`SpaceLab.data`), so they cannot change the data format or leak anything.
- No Python, no `publish.py`, no schema changes needed.

## The API (provided by `web/public.js`)
```js
SpaceLab.data                       // the parsed data.json (read-only; see publish.SCHEMA for its shape)
SpaceLab.esc(text)                  // escape text before putting it into innerHTML (ALWAYS use it)
SpaceLab.num(x, digits)             // format a number
SpaceLab.onRender((tab, el, data) => { ... })   // after any tab draws; tab = 'today','brief','exowatch','tess','asteroids','about' or your own
SpaceLab.addTab('id', 'Title', (el, data) => { el.innerHTML = '...'; })   // a new tab before "About" (local page: before "Setup")
SpaceLab.local                      // true on the local Space Lab page, undefined on the public page
SpaceLab.load(path)                 // load a file from this same site (e.g. files/plots/beyond/zip.txt); other sites are refused
SpaceLab.weather(lat, lon)          // cloud forecast from Open-Meteo: only call it when the visitor asks (it sends the rounded place)
SpaceLab.track(path, isEvent)       // count a view or event with the optional visitor counter (no-op on the local page / when off)
SpaceLab.results                    // local page only: {list(), save(id, entry), remove(id)} for the owner's results log
SpaceLab.cloudCheck(dataUrl)        // local page only: the local vision AI names clouds in a sky photo (check it exists first)
```
On the local page, `onRender` reports the Space brief tab as `'brief'` (its local name is `arxiv`), so one hook works on both.
Useful data (each TESS card has `data-tic="NNN"`; each old asteroid sighting card has `data-pc="<id>"`): `data.tess.cands[]` (id, tic, period [days], t0 [BTJD days], depth [fraction, 0.001 = 0.1%], duration [days], rp [Earth radii], snr, flags, status, plot, lc,
ai.verdict, fpp.verdict), `data.asteroids.cands[]` (des, night, archive, gif, png, status), `data.asteroids.cad[]`,
`data.exowatch.stale[]` (with ra, dec), `data.brief.papers[]`, `data.tess.cands[].star` (teff, radius_sun, dist_pc, tmag),
`data.community` (GitHub review leaderboard and per-candidate votes), `data.contributions`, and `data.beyond`
(space_weather, fireballs, comets, gw, supernovae, launches, habitable, sky, earth, quakes, iss), `data.daily` (candidate of
the day: date, cand, blurb, votes, prev), `data.radar` (items ranked by the local AI, digest), `data.results` and `data.site.url`. Newer: `data.tess.cands[].star.ra/dec` (sky position), `data.tess.cands[].gaia_ruwe`, `data.tess.crosscheck` (CHEOPS answer-key test: tested, recovered, items[]), `data.beyond.missions` (space telescopes with dates) and `data.beyond.orbits` (orbit elements of the closest flybys, keyed by designation). Image/JSON paths like
`files/...` load from the same site.

## Rules
1. Edit only `web/extras.js`, `web/beyond.js`, `web/home.js`, `web/stargazing.js`, `web/asteroids.js` and `web/extras.css`. Wrap each feature in its own function.
2. Escape every piece of data with `SpaceLab.esc()` before inserting it as HTML.
3. No network requests to other sites. No trackers, analytics, fonts or CDNs. Data already on the page is enough.
   Links that open in a new tab are fine (the checker allows the project, its page and ESASky in code; links that come
   from the data are fine too). New live data goes into `spacelab/beyond.py` + `publish.build_data`, not the browser.
   A visitor's location may be used only inside their browser (ISS passes, sky pointer, dark skies) and is kept only in
   their own localStorage (`sl_place`), never sent anywhere. Load same-site files with `SpaceLab.load`, never `fetch`.
4. Storage: only `localStorage` keys starting with `sl_`, wrapped in try/catch (some browsers block it).
5. Honest wording: a TESS signal is a *candidate*, never a "discovery" or "new planet", unless its status says so.
6. Votes/reviews go to GitHub issues through links (templates `candidate-review.yml`, `asteroid-review.yml`). Never collect
   names, emails or locations.
7. Plain JavaScript, no libraries, no build step. Keep each feature under ~150 lines.
8. Works on a phone (narrow screen) and with dark colors from `style.css` variables.
9. Every element you add to an existing card gets a class starting with `sl-` (the checker strips them to verify the
   original text is untouched). Inside your own tabs use `sl-` (extras.js), `bx-` (beyond.js), `hm-` (home.js), `sg-` (stargazing.js) or `as-` (asteroids.js). Every box gets a "Suggest an improvement" footer automatically.
10. Never add anything to `nav.tabs` except through `SpaceLab.addTab` (every button there is treated as a tab).
11. In loops, use `const`/`let` or `forEach` so each button remembers ITS OWN card (a `var` in a loop makes every button use the last card).

## Try it on your Mac before committing
```
cd ~/Desktop/"Claude Projects"/SpaceLab-dev/web
curl -s -o data.json https://techamatuer.github.io/Space-Lab/data.json
python3 -m http.server 8800
```
Open http://localhost:8800/public.html (images won't show locally; that's normal). Stop the server with Ctrl+C.
`web/data.json` is ignored by git.

## Checks before every commit
```
node --check web/extras.js
node tests/extras/check.js          (one-time setup: cd tests/extras && npm install)
python tests/test_offline.py
```
