# Contributing to Space Lab

The computer finds signals; people decide what they are. Every bit of help counts.

## 1. Review a planet candidate (2 minutes, no experience needed)

1. Open the [results page](https://techamatuer.github.io/Space-Lab/) and pick a candidate.
2. Click **Interactive light curve**:
   - Switch to **Folded at the period**.
   - Tick **odd / even dips**.
3. Look for these clues:
   - A real planet gives a **flat-bottomed, box-shaped** dip, and **odd and even dips match**.
   - **Different odd/even depths**, or a second dip halfway through the orbit, usually means **two stars eclipsing**.
   - If the **centroid test** says "off target", the dip probably comes from a **nearby star**.
4. Tell us what you think: open a [**candidate review**](../../issues/new/choose) issue, or reply in the candidate's Discord thread.

## 2. Detective work (5 minutes)

Look the star up on **ExoFOP**; each card has a link. Is it already a TESS Object of Interest (TOI)? Has anyone left notes? Is there a bright star nearby? Post what you find in the issue or thread.

## 3. Do NASA science yourself (free)

- **[NASA Exoplanet Watch](https://science.nasa.gov/citizen-science/exoplanet-watch/):** request free telescope data and measure a transit with EXOTIC (it runs in your browser via Google Colab). The **"timing has drifted most"** list on the results page tells you which planets need it most.
- **[Planet Hunters TESS](https://www.zooniverse.org/projects/nora-dot-eisner/planet-hunters-tess):** classify TESS light curves by eye on Zooniverse.

## 4. Own a smart telescope? (Seestar, Dwarf, Unistellar…)

Open an issue saying you can observe. Space Lab can list transits that are visible from your area. Your observations can go straight to the AAVSO Exoplanet Database, credited to you.

## 5. Code and science review

- Please **critique the method**. Is a check missing? Is something wrong? We'd rather hear it than post a false planet.
- Pull requests are welcome. Keep changes small, explain the science reason, and run the offline tests if you can.
- Never commit anything from `data/`, `logs/` or `inbox/`, or any settings, tokens or webhooks. `.gitignore` blocks them; please don't force-add them.
- Don't edit the `gh-pages` branch. It's regenerated automatically.

## Ground rules

Be kind, assume good faith, and remember everything here is preliminary. Credit goes to everyone whose review shaped a result.

## Code changes
Open a pull request against `main`. Run `python tests/test_offline.py` first; GitHub runs the same tests and a
secret scan on every pull request. If you use an AI coding agent, point it at [AGENTS.md](AGENTS.md).
