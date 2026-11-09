# Roadmap: fun features, Sky watch and Beyond astronomy

Everything here runs on the public page **and** inside Meridian (same files, same `SpaceLab` API; see [EXTRAS.md](EXTRAS.md)).
Front-end features live in `web/extras.js`, `web/beyond.js` and `web/extras.css`. Live data comes from the
"Sky watch & Beyond astronomy" job (`spacelab/beyond.py`, every 3 hours) through `publish.build_data`.

## The automatic checker
`tests/extras/check.js` opens the public page in a simulated browser with fake data (`tests/extras/fixture.json`).
```
cd tests/extras && npm install && cd ../..      (one time)
node tests/extras/check.js                      (everything)
node tests/extras/check.js 12                   (one group + the general safety checks)
```
Each `FAIL` line says exactly what to fix. GitHub runs the `G` (general safety) group on every push.

| Group | What it checks |
|---|---|
| G | no crashes, original card text untouched, nothing loaded from other sites, `sl_` storage keys only |
| 2, 3, 8, 9 | TESS cards: size picture, Listen, Postcard, Save |
| 5, 6, 7, 11 | Play tab: Planet or not?, asteroid review, find-the-period game, badges |
| 12 | Sky watch: Moon, meteor showers, ISS passes, space weather, Listen to the Sun, comets, gravitational waves, supernovae |
| 13 | Beyond astronomy: habitable worlds, spectrum explorer, rocket and orbit calculators, launches, Earth images, earthquakes |
| 14 | Share text and the `?card=<TIC>` embed |
| 15 | Create tab, My lab notes, change alerts, sky pointer |
| 16 | Asteroid "story behind this picture" and the asteroid race timer |

## Built (November 2026)

**TESS cards:** size vs Earth/Neptune/Jupiter · listen to the transit · save to My lab · postcard · share text ·
embed snippet (`?card=<TIC>`) · "see it in the sky" (ESASky) · star colour, type and distance · backyard visibility ·
community review counts · "new since your last visit".

**Play & review:** Planet or not? (answers go to GitHub) · is the asteroid there? · find the period · spot the dip ·
blink comparator (variable stars) · The Case of the Missing Light · asteroid race timer.

**Create:** postcard · light-curve art · phone wallpaper · what the stars look like · cosmic calendar.

**My lab:** candidate cards with notes · watchlist and change alerts · streaks · badges · observing log with photos
(and, in Meridian, "what clouds are these?" by the local vision AI) · sky pointer with phone compass · community
leaderboard · the lab's contributions · first-telescope guide.

**Sky watch:** Moon phase and planets · meteor showers · ISS passes for your location (worked out in your browser) ·
space weather (Kp, storm scales, aurora chance, flares, X-ray curve, radio flux) · listen to the Sun · fireballs ·
comets · gravitational-wave alerts · supernova candidates.

**Beyond astronomy:** biology (most Earth-like planets, extremophiles, where to look, biosignatures) · chemistry
(spectrum explorer, molecules in space, meteorite building blocks) · engineering (launches with countdowns, rocket
equation, orbit calculator) · Earth and planets (satellite images, aerosols, Earth at night, earthquakes, quakes on
other worlds).

**Asteroids tab:** the story behind each old sighting. **Orbit viewer:** pinch to zoom on phones.

**Meridian only (owner tools):** Community TOI draft for candidates you mark planet-like · eclipsing-binary list for
AAVSO VSX · papers that mention your own targets · nightly database backup · cache clean-up and disk use ·
alerts only after a job fails twice.

## Ideas not built yet (good next tasks)
- TESS mission tracker (which sector TESS is observing now): needs a dependable machine-readable pointing table.
- "Click the dot": tap the moving asteroid in the frames instead of choosing an answer.
- Live retraining of the planet model from public reviews (needs a review step by the owner first).
- Full augmented-reality star finder (the sky pointer + compass is the light version).
- Hardware projects from the fun list (water-quality sensors, a radio-telescope dongle), a bioblitz and a
  protein-folding game: outside Space Lab's data, so left out.

Skipped on purpose (owner's choice): "explain like I'm 10", glossary tooltips, Basic/Expert switch, walkthroughs,
mini-courses.
