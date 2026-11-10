# 🔭 Space Lab

**Citizen science from a home computer, using only open data.**

📊 **Results page:** https://techamatuer.github.io/Space-Lab/
🙋 **Want to help?** See [CONTRIBUTING.md](CONTRIBUTING.md). No astronomy or coding experience is needed.

Space Lab runs on a home Mac. It works through public data from NASA and other observatories, and a local AI model on the same machine explains the results in plain English. A person reviews every result before anything is reported to the AAVSO or the Minor Planet Center.

## What it does

| Project | How often | What it does |
|---|---|---|
| 🪐 **TESS planet hunter** | every 3 h | Nearby cool dwarf stars (≤150 pc): SPOC 2-min or full-frame (TESS-SPOC/QLP) light curves → GPU box-least-squares search (Apple MLX) → standard vetting (odd/even depths, secondary eclipse, duration vs. stellar density, size) → TESScut difference-image centroid test → first look by a local vision model → score from a small model trained on human decisions |
| ☄️ **Asteroid precovery** | every 6 h | Short-arc Sentry objects and newly announced NEOs, searched in Pan-STARRS1 DR2 images (CADC SSOIS + JPL Horizons). A sighting needs consistent motion in two same-night images and must be absent from the deep stack |
| 🛰️ **NEO confirmation watch** | hourly | JPL Scout scores for every object on the MPC NEO Confirmation Page |
| 🌍 **Exoplanet Watch** | when data arrives | MicroObservatory (or smart-telescope) transits reduced with EXOTIC for NASA Exoplanet Watch / the AAVSO Exoplanet Database; ranks planets whose predicted transit times have drifted most |
| 🗿 **Old-plate precovery** | daily | The 2026 ED138 method: newly found distant objects bright enough for old plates are predicted on every scanned SuperCOSMOS plate (1949–2003) at 3σ; each plate is re-solved against Gaia DR3 stars moved to the plate epoch, nearby stars are modelled and removed, the archive date is checked at −1/0/+1 day and against known asteroids (SkyBoT), another date must show nothing there, and a detection must clearly beat the plate's own noise (the strongest blob where the object would be on a wrong date). Plate times follow the stars: moving the archive's date by a day is 23 h 56 min, which matched the MPC's times to a second on four plates. Plates already reported to the MPC become accuracy tests (0.1–0.24″ on 2026 ED138); new ones wait for your look, then a draft ADES (mode PHO) can be tested and sent from Meridian by you |
| 🎯 **CHEOPS answer-key test** | every 3 days | Public ESA CHEOPS light curves of planets that are already known are searched blind for the most significant dip; the result is compared with the published time and depth. Hits and misses are both shown |
| 🗺️ **Space atlas** | live | A map app for space on the public page: the whole sky in six kinds of light (Aladin Lite in a sandboxed frame), famous places, this lab's finds as pins, the Moon, Mars, Mercury, Vesta and Ceres (NASA Solar System Treks) and NASA's photo library |
| ⭐ **Variable stars** | every 12 h | Every TESS light curve is also searched for repeating brightness changes (Lomb-Scargle, before the planet search flattens it). Clear signals are checked against VSX, Gaia DR3 variability and ASAS-SN, and against every Gaia neighbour within two TESS pixels. New ones get a folded light curve, a type (BY/ROT, EA/EW, +UV), the local AI's first look and the fields of the VSX New Star form. You review and submit them yourself; nothing is sent from here |
| 🔭 **Variable-star observatory** | daily + hourly | For an AAVSO observer with an automated N.I.N.A. observatory (off unless set up, see [VARIABLE_STARS.md](VARIABLE_STARS.md)): tonight's targets from the AAVSO Target Tool, alerts and overdue campaign stars first, as a Target Scheduler CSV; new images measured against AAVSO chart stars (ensemble, check star, any filters); one checked AAVSO Extended file per night for the observer to upload. Nothing is sent from here |
| 🪐 **Solar system, live** | daily | Where the planets, some dwarf planets and comets, and about 25 spacecraft are right now (Voyagers, Parker Solar Probe, BepiColombo, JUICE, Europa Clipper, Webb...), from NASA/JPL Horizons. The page works out every second in between, so distances and signal times tick live; trails show where each spacecraft has been since launch. An object Horizons does not confirm by name is left out |
| 🌌 **3D universe** | monthly | About 43,500 real galaxies of the 2MASS Redshift Survey in a 3D map you can orbit, fly through and search. Each galaxy sits at its measured distance when a precise one exists (Cosmicflows-4), otherwise where its redshift puts it after our own motion and the pull of Virgo, the Great Attractor and Shapley are taken out (Mould et al. 2000); group members share their group's speed (Tully 2015). Every card says which kind of distance it shows |
| 📰 **Space brief** | daily | New astro-ph papers ranked with bge-m3 embeddings, summarized by the local AI |

## Repository layout

- `spacelab/`: the pipelines and a small local web server (Python 3.12)
- `web/`: the local dashboard, plus the public results page (`public.html`, `public.js`)
- the **`gh-pages` branch**: the published results page, generated automatically. Please don't edit it by hand.

## Run your own copy (macOS or Linux)

```
git clone https://github.com/TechAmatuer/Space-Lab.git
cd Space-Lab
bash setup.sh
.venv/bin/python -m spacelab.server
```

Then open http://localhost:8790.

- **Optional:** [Ollama](https://ollama.com) with `gemma4:26b` and `bge-m3` for the plain-English explanations and AI first looks. Without it, everything else still runs.
- **Your settings stay private.** Your name, email, AAVSO code, webhooks and location are stored in `data/settings.json`, which is never committed.

## Status and honesty

Everything on the results page is **preliminary**. Most TESS signals turn out to be eclipsing binaries, blends or noise; that's normal. Nothing here is a confirmed discovery unless it's confirmed elsewhere (ExoFOP, the MPC, the literature).

## Data credits

NASA TESS / MAST, NASA Exoplanet Archive, ExoFOP-TESS, JPL Small-Body Database, Sentry, Scout and Horizons, CADC SSOIS, Pan-STARRS1 (STScI), ESA Gaia, AAVSO, arXiv, ESA CHEOPS through DACE (University of Geneva). Old plates: SuperCOSMOS Sky Surveys (WFAU, University of Edinburgh); plate search, cutouts and star removal with [SpaceRockSuite](https://github.com/eagnespuerto/spacerocksuite) by Ella Agnes Puerto (MIT); known asteroids from SkyBoT (IMCCE). Space atlas: sky surveys through ESA's ESASky and the CDS (Strasbourg), shown with Aladin Lite (CDS, LGPL-3.0, loaded from jsDelivr); planetary maps from NASA Solar System Treks; photos from the NASA Image and Video Library. Solar system: positions from NASA/JPL Horizons. 3D universe: 2MASS Redshift Survey (Huchra et al. 2012), 2MASS galaxy groups (Tully 2015) and Cosmicflows-4 distances (Tully et al. 2023) through VizieR (CDS; Harvard mirror), names through SIMBAD/Sesame, flow model of Mould et al. (2000); the idea comes from [Know the Universe](https://knowtheuniverse.com).

MIT License.
