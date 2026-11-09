# Building extras for the public page (for people and AI coding agents)

Fun and learning features for https://techamatuer.github.io/Space-Lab/ live in **two files only**:
`web/extras.js` and `web/extras.css`. Everything else is the core and is off-limits for extras work.

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
SpaceLab.addTab('id', 'Title', (el, data) => { el.innerHTML = '...'; })   // a new tab before "About"
```
Useful data (each TESS card is `<div class="card" id="tic-NNN">`): `data.tess.cands[]` (id, tic, period [days], t0 [BTJD days], depth [fraction, 0.001 = 0.1%], duration [days], rp [Earth radii], snr, flags, status, plot, lc,
ai.verdict, fpp.verdict), `data.asteroids.cands[]` (des, night, archive, gif, png, status), `data.asteroids.cad[]`,
`data.exowatch.stale[]`, `data.brief.papers[]`. Image/JSON paths like `files/...` load from the same site.

## Rules
1. Edit only `web/extras.js` and `web/extras.css`. Wrap each feature in its own function.
2. Escape every piece of data with `SpaceLab.esc()` before inserting it as HTML.
3. No network requests to other sites. No trackers, analytics, fonts or CDNs. Data already on the page is enough.
4. Storage: only `localStorage` keys starting with `sl_`, wrapped in try/catch (some browsers block it).
5. Honest wording: a TESS signal is a *candidate*, never a "discovery" or "new planet", unless its status says so.
6. Votes/reviews go to GitHub issues through links (templates `candidate-review.yml`, `asteroid-review.yml`). Never collect
   names, emails or locations.
7. Plain JavaScript, no libraries, no build step. Keep each feature under ~150 lines.
8. Works on a phone (narrow screen) and with dark colors from `style.css` variables.

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
python tests/test_offline.py
```
