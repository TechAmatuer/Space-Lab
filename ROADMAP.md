# Roadmap: fun and learning features (built by a local AI, reviewed by a human)

Ranked by **value ÷ effort**, with a bonus for features that produce real science (volunteer reviews).
Every task below is built in `web/extras.js` + `web/extras.css` only. Read [EXTRAS.md](EXTRAS.md) first.
Do them **in order, one task per branch**. Tick the box when it is merged.

## The loop (for each task)
1. `git pull` on `main`, then `git checkout -b extras-<task number>`.
2. Paste the task card below into the agent with this prefix:
   > Read AGENTS.md and EXTRAS.md. Implement only this task, only in web/extras.js and web/extras.css.
   > Do not touch any other file. When done, run `node --check web/extras.js` and show me the diff.
3. Preview it (EXTRAS.md → "Try it on your Mac"), on desktop and with the browser narrowed to phone width.
4. Run `python tests/test_offline.py`, commit, push the branch, open a pull request.
5. Wait for the green ✓ on GitHub, read the diff once, merge. Your Mac picks it up on the next `git pull`.
6. If the agent fails twice on the same task, or the task seems to need another file, stop and ask Claude.

---

## Tier 1: quick wins (do first)

### [ ] 1. Glossary tooltips
**Why:** jargon is the #1 barrier for newcomers. **Effort:** small.
Build a glossary object (term → one plain-English sentence) for: transit, light curve, period, depth, ppm, SNR, BLS,
centroid, false-positive probability (FPP), eclipsing binary, TESS, TIC, ExoFOP, precovery, near-Earth asteroid, Palermo scale,
Torino scale, lunar distance (LD), mid-transit time, O–C, ephemeris, airmass, arXiv, Earth radii.
Use `SpaceLab.onRender`: walk text nodes in the view (skip `a, button, input, select, textarea, script, style, abbr`),
wrap the **first** occurrence of each term per card in `<abbr class="sl-gl" tabindex="0" title="...">`. Case-insensitive,
whole words only. On a phone, tapping the term shows the definition in a small popup.
**Done when:** terms are underlined dotted, hover/tap shows the meaning, no term is wrapped twice in a card, links still work.

### [ ] 2. "How big is it?" size comparison
**Why:** "2.3× Earth" means nothing to most people; a picture does. **Effort:** small.
On the TESS tab, for each candidate with `rp`, add an inline SVG under the stats line: Earth (blue circle), the candidate
(orange circle), Neptune (3.9 R⊕) and Jupiter (11.2 R⊕) as faint outlines, all to scale, with labels. Cap the drawing at
120 px tall (scale everything down together if the candidate is huge). Caption: "if it is a planet".
**Done when:** every card with a radius shows it, sizes are to scale, nothing overflows on a phone.

### [ ] 3. Listen to a transit
**Why:** shareable, memorable, accessible to blind/low-vision visitors. **Effort:** small.
Add a "🔊 Listen" button next to "Interactive light curve" on each periodic candidate. Web Audio only: a soft steady tone
for 8 seconds; at every transit the volume dips. Time is compressed so one orbit = 1 second (min 0.3 s, max 2 s);
dip length = duration/period of that second; dip amount = depth × 50, capped at 80% (say "exaggerated" in the tooltip).
Button toggles play/stop; only one sound at a time.
**Done when:** it plays on desktop and phone, stops cleanly, never auto-plays.

### [ ] 4. Basic / Expert switch
**Why:** beginners see the story, experts keep the numbers. **Effort:** small.
A toggle in the header ("Basic · Expert"), saved in `localStorage` key `sl_level`, sets `document.body.dataset.level`.
In Basic mode hide (with CSS only): `.kv` tables, the centroid/FPP notes, `details` blocks and `table.compact` columns after
the second one. Default: Basic for first-time visitors.
**Done when:** switching is instant, the choice persists, Expert looks exactly like today.

## Tier 2: real citizen science (highest value, medium effort)

### [ ] 5. "Planet or not?" review game (real candidates)
**Why:** turns visitors into reviewers; their votes are genuinely useful. **Effort:** medium.
New tab "Review" (`SpaceLab.addTab('review', 'Review', ...)`). Show one TESS candidate at a time (its `plot` image, period,
depth, size). Four buttons: Planet-like · Two stars (eclipsing binary) · Noise / glitch · Not sure. After answering, reveal
what the AI and the automatic checks said (`ai.verdict`, `flags`, `fpp.verdict`) and a short explanation of the checks.
Button "Send my review" opens
`https://github.com/TechAmatuer/Space-Lab/issues/new?template=candidate-review.yml&tic=<tic>&verdict=<answer>` in a new tab
(the visitor needs a free GitHub account; say so). Keep the visitor's own answers in `localStorage` `sl_reviews` and skip
candidates already reviewed. Include a 3-step "how to judge a plot" intro (shown once).
**Done when:** you can go through all candidates, answers persist, the GitHub link opens prefilled.

### [ ] 6. "Is the asteroid there?" image review
**Why:** precovery sightings need human eyes; this is real, useful work. **Effort:** medium.
In the Review tab, a second section using `data.asteroids.cands[]`: show the `gif` (animated frames) large, with a note
"the asteroid should be the dot that moves, near the centre". Buttons: Moving dot (real) · Nothing there · Star/defect · Not sure.
"Send my review" → `.../issues/new?template=asteroid-review.yml&candidate=<des>, <night>, <archive> (#<id>)&verdict=<answer>`.
If there are no candidates, say "No sightings waiting right now" (don't show anything fake).
**Done when:** works with 0, 1 and many candidates.

### [ ] 7. Find-the-period practice game
**Why:** teaches what the pipeline does, so visitors review better. **Effort:** medium.
In the Review tab (or a "Learn" tab): generate a fake light curve in the browser (random period 1–8 d, depth 0.5–2%,
noise 0.3–1%, 27 days of points every 30 min), draw it on a `<canvas>`. A period slider (0.5–10 d) folds the data and redraws
the folded view live; when the period is within 1% of the true one, show "You found it! Period = X days" and how many tries.
Button "New star". No real data needed. Label clearly: practice with made-up data.
**Done when:** folding is visibly right at the true period and wrong elsewhere; smooth on a phone.

## Tier 3: nice to have

### [ ] 8. Postcard / light-curve art
Canvas 1080×1080 PNG download for a candidate: folded light curve drawn as a glowing ring, text "Planet candidate TIC …,
orbit every … days, about …× Earth if real", footer "Space Lab · citizen science". Word it as a candidate.

### [ ] 9. My list (favorites)
☆ button on each TESS candidate and asteroid; "My list" tab shows saved ones with their current status. `localStorage` only.

### [ ] 10. "How we checked this signal" walkthrough
On each TESS card, a "Walk me through it" button opening a 5-step guide over that candidate's own plot: the dips, the period
search, the folded dip, odd/even, centroid. Text only plus the existing plot image; no new data.

### [ ] 11. Personal badges (local only)
Badges for "first review", "10 reviews", "found the period in under 5 tries", etc., stored in `localStorage`. Shareable as text.
Only after tasks 5–7 exist (badges need something to reward).

---

## Not now (and why)
| Idea | Why not |
|---|---|
| Leaderboards, shared achievements, streaks, collaborative vetting with accounts | Needs a server and user accounts; the public page is static. GitHub issues already give shared, public reviews. Revisit if 20+ people review regularly. |
| AR star tracker, "what's in the sky tonight", build-a-telescope guide | Great apps already do this (Stellarium, SkySafari, Heavens-Above). Link to them instead of rebuilding. |
| "Send to NASA" one-click | AAVSO upload needs your own login; it must stay a manual step you do. |
| Auto tweets "Just found TIC …" | Overclaims: these are candidates, most turn out to be binaries or noise. Share only validated results, worded carefully. |
| "Spot the dip, first to flag it wins" | Needs a server to know who was first; task 7 covers the learning part. |
| "This asteroid was first seen 80 years ago" stories | The image archives Space Lab searches start around 2010. Tell real stories from your own precoveries instead. |
| "Would this planet be visible from Earth?" | No telescope can see these planets as dots; it would mislead. A correct version: "could a backyard telescope catch the transit?" (needs a proper noise formula; ask Claude). |
| TESS sector map, cosmic calendar, star colour renderings | Fine but low value for the effort; later. |
| Water/air sensors, radio (SDR), seismology, camera traps, protein folding, clouds | Separate projects with their own hardware and data; they would dilute Space Lab. Good ideas for a future "Science Lab". |
| Mini-courses | Valuable, but AI-written lessons must be fact-checked line by line; do after the review tools. |
