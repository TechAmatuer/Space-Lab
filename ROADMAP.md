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
| 17 | Home page, candidate of the day (vote, streak, yesterday's result) and grouped navigation |
| 18 | Results tab, the radar in the Space brief, `#tic-<TIC>` links and share pages |
| 19 | Stargazing: ZIP code place, events with calendar reminders, "What's that in the sky?", dark-sky finder, ISS with saved place |
| 20 | Review progress, "you've seen them all", the TESS big picture and next predicted dips, learning path, shareable summary |
| 21 | Header location chip, "Tonight from your place", suggestions on every box, research-news explorer, live pictures, finding habitable worlds |
| 22 | Asteroid guide: sizes, speeds and distances pictured, orbit links, famous asteroids with Wikipedia summaries and citations |

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

**Website (November 2026, part 2):** Home page for visitors · candidate of the day with no-login votes, streaks and
yesterday's result · space-science radar (the local AI reads ~35 sources every 6 hours and ranks the news) · Results log
(what was sent where, and what happened) · link previews with a daily preview picture · a share page per candidate ·
Atom feed, sitemap, app icon (add to home screen) · optional GoatCounter visitor counter (no cookies) · grouped tabs
(Explore / Research / Join in) · own web address support.

**Stargazing and contributions (November 2026, part 3):** Stargazing tab (your place by ZIP code or GPS, kept in the
browser; dark skies within driving distance from NASA's night-lights map; meteor showers, eclipses, Moon phases, launches
and comets with "Remind me" calendar files and a subscribable calendar; "What's that in the sky?") · review progress bar
and "you've seen them all" summary · "you reviewed this" on cards · the big-picture funnel and "not every dip is a planet" ·
next predicted dips with countdowns · learning path (Stargazer → Contributor) · shareable contribution summary ·
estimate-the-depth game · satellite counts · GoatCounter on.

**People first (November 2026, part 4):** "Your Space Lab" on Home · a location chip on every page and "Tonight from your
place" (darkness, planets with times, Moon, meteors, ISS, aurora odds, optional cloud forecast) · "Suggest an improvement"
on every box (GitHub or Discord; suggestions shown back on the box) · filterable research news · live pictures of the Sun,
Earth and aurora · habitable worlds with constellation and "where is it now?" · asteroid guide with Wikipedia citations ·
the local AI runs whenever the Mac is idle for 5 minutes.

## Ideas not built yet (good next tasks)
- TESS mission tracker (which sector TESS is observing now): needs a dependable machine-readable pointing table.
- "Click the dot": tap the moving asteroid in the frames instead of choosing an answer.
- Automatic posts to the public Discord channel (candidate of the day, aurora alerts).
- A small server (e.g. Cloudflare Workers) for votes and comments without GitHub, once there are regular visitors.
- Quality control for votes like reCAPTCHA: mix known answers (confirmed planets, known binaries) into the review game to
  weigh each reviewer's votes, then publish the labelled set as an open dataset (e.g. on Zenodo).
- Live retraining of the planet model from public reviews (needs a review step by the owner first).
- Full augmented-reality star finder (the sky pointer + compass is the light version).
- Hardware projects from the fun list (water-quality sensors, a radio-telescope dongle), a bioblitz and a
  protein-folding game: outside Space Lab's data, so left out.

Skipped on purpose (owner's choice): "explain like I'm 10", glossary tooltips, Basic/Expert switch, walkthroughs,
mini-courses.
