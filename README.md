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
| 📰 **Space brief** | daily | New astro-ph papers ranked with bge-m3 embeddings, summarized by the local AI |

## Repository layout

- `spacelab/`: the pipelines and a small local web server (Python 3.12)
- `web/`: the local dashboard, plus the public results page (`public.html`, `public.js`)
- the **`gh-pages` branch**: the published results page, generated automatically. Please don't edit it by hand.

## Run your own copy (macOS or Linux)

```
git clone https://github.com/techAmatuer/Space-Lab.git
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

NASA TESS / MAST, NASA Exoplanet Archive, ExoFOP-TESS, JPL Small-Body Database, Sentry, Scout and Horizons, CADC SSOIS, Pan-STARRS1 (STScI), ESA Gaia, AAVSO, arXiv.

MIT License.
