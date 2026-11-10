# Variable-star observatory (for AAVSO observers with an automated telescope)

This part of Space Lab is for an observer who already reports CCD or CMOS photometry to the AAVSO and runs an automated
observatory with N.I.N.A. and the Target Scheduler plugin. It takes over the two jobs that eat the observer's time:

1. **Choosing tonight's targets.** AAVSO alerts and campaigns come first. After them come the stars whose requested cadence
   is most overdue, so a campaign star with few recent observations rises to the top.
2. **Turning a night of images into a report.** Each image of each target is measured against the AAVSO's own comparison
   stars. When the night is over, Space Lab writes one AAVSO Extended Format file and checks it against the format's rules.

**Nothing is ever sent to the AAVSO from here.** You look at the file, upload it yourself at WebObs, and press
"I uploaded it". Space Lab does not control the telescope either: N.I.N.A. does.

Everything stays on the computer that runs Space Lab: your location, your Target Tool key and your measurements. None of it
goes on the public Space Lab website.

## How a day goes

| When | What happens | What you do |
|---|---|---|
| 15:30 (local time of the computer running Space Lab) | **Tonight's plan.** Targets come from the AAVSO Target Tool and your own watch list. Each one gets a need score, and the plan checks altitude, Moon and brightness. The result is written as a N.I.N.A. Target Scheduler CSV | Import the CSV into Target Scheduler (one import, see below) |
| Night | N.I.N.A. observes | Nothing |
| Every hour | **Measurement.** New FITS images are calibrated, plate-solved (or fitted to Gaia stars) and measured against the AAVSO chart's comparison stars | Look at the measurements that have a warning, and accept or reject each one |
| After local noon | **Report.** The finished night becomes one AAVSO Extended Format file, checked against the format's rules | Download it, upload it at [WebObs](https://www.aavso.org/webobs/file), then press "I uploaded it" |

Everything is on the **Variable-star observatory** tab in Meridian (under Research). Tasks appear on the Today tab when
there is something for you to do.

## What you need

- A Mac (or Linux computer) running Space Lab, see the [README](README.md). It has not been tested on Windows.
- The folder where N.I.N.A. saves the images, readable from that computer. A network share is fine. A synced folder works
  too (Syncthing, Dropbox or OneDrive, for example).
- An AAVSO observer code (free account).
- Optionally, your AAVSO Target Tool API key. You can find it on the Target Tool's settings page after you log in at
  targettool.aavso.org. Without a key, export a CSV from the Target Tool now and then and point Space Lab at it, or
  use only a watch list.

## Setting it up

### 1. Space Lab settings

Open Meridian → **Variable-star observatory** → **Settings** at the bottom of the page. Change the values and save.
Here is a shortened example; every number in it is made up:

```json
{
  "enabled": true,
  "site": {"lat": 35.0, "lon": -105.0, "elev_m": 1500},
  "obscode": "XYZ",
  "targettool_key": "paste your key here",
  "sections": ["ac", "cv", "eb"],
  "watchlist": [{"name": "SS Cyg", "cadence_days": 1}, {"name": "V Sge", "series_min": 120}],
  "min_alt": 30, "bright_limit": 8.0, "faint_limit": 16.0,
  "filters": [
    {"fits": "B", "aavso": "B", "chart": "B",  "exp_at_mag12": 40, "n": 1},
    {"fits": "V", "aavso": "V", "chart": "V",  "exp_at_mag12": 20, "n": 1},
    {"fits": "R", "aavso": "R", "chart": "Rc", "exp_at_mag12": 15, "n": 1},
    {"fits": "I", "aavso": "I", "chart": "Ic", "exp_at_mag12": 20, "n": 1}
  ],
  "images_dir": "/Volumes/Observatory/NINA/Images",
  "calib_dir": "/Volumes/Observatory/NINA/Masters",
  "pixel_scale": 1.1,
  "transform": {}
}
```

The four filters are not fixed. List whichever filters your wheel has:

- `fits` is the FILTER name N.I.N.A. writes in your images. `aliases` adds other spellings, e.g. `"aliases": ["Green"]`.
- `aavso` is the AAVSO filter code: B, V, R (Cousins R), I (Cousins I), SG, SR, SI, CV, TG and so on.
- `chart` is the band name on AAVSO charts: B, V, Rc, Ic, SG, SR, SI and so on.
- `exp_at_mag12` is your exposure in seconds for a 12th-magnitude star in that filter.

Every target gets the same signal: a star one magnitude fainter gets about 2.5 times the exposure. Exposures are rounded
to familiar values and kept between `exp_min` and `exp_max`. A filter with `"n": 3` takes three frames in each sequence.

### 2. N.I.N.A. Target Scheduler, once

The CSV's **Type** column carries an exposure class: `VS short`, `VS medium`, `VS long` and `VS series` (time series).
In Target Scheduler:

1. Make a project for AAVSO targets. Give it the priority you want compared with your other projects.
2. Add four template targets named after the classes. Give each one an exposure plan for your filters:
   - `VS short`: exposures up to 15 s (bright stars)
   - `VS medium`: up to 60 s
   - `VS long`: longer exposures
   - `VS series`: a long block for time-series targets

   The class limits are in `classes` in the settings. The exact per-filter exposure for each star is in the plan table on
   the tab, if you want to fine-tune it.
3. **Test with one target first.** Import a CSV that holds only one star. Use the Type filter so the template's exposure
   plan is copied. Check that the coordinates, the name and the exposure plan came through correctly.

Each afternoon, import that day's CSV. You can download it from the tab. If you set `nina_import_dir` to a folder the
N.I.N.A. computer can see, a copy named `spacelab-tonight.csv` lands there every day.

### 3. What the images need

N.I.N.A. writes these FITS keywords by default; Space Lab needs them:

- `OBJECT`: the target name, as in the CSV
- `FILTER`
- `DATE-OBS`: the start of the exposure, in UTC
- `EXPTIME`
- `IMAGETYP`: LIGHT. Bias, dark and flat frames in the image folder are skipped.
- `RA`/`DEC` or `OBJCTRA`/`OBJCTDEC`

If an image has no OBJECT name, the pointing is matched against tonight's targets. A star that is not in the plan is
looked up in AAVSO VSX by its OBJECT name.

### 4. Calibration (optional)

Set `calib_dir` to a folder with bias, dark and flat frames. Masters are fine, and so are single frames, which are combined
with a median. The frame type is read from IMAGETYP or the file name. Flats are matched to the image by FILTER, and darks
are scaled by exposure time. If your images are already calibrated, leave `calib_dir` empty.

### 5. Plate solution

If an image carries its own plate solution (a WCS in the header), that solution is used. Otherwise Space Lab matches the
stars in the image to Gaia DR3 around the pointing. To do that it needs the pixel scale:

- `pixel_scale` in the settings, in arcseconds per pixel; or
- `XPIXSZ` and `FOCALLEN` in the header, which N.I.N.A. usually writes.

## How the targets are chosen

Each candidate gets a need score, and the reasons are shown in words on the tab:

- **Overdue:** days since the last AAVSO data point, divided by the requested cadence. This counts up to 6 points.
- **Alert or campaign:** +2. **High priority** in the Target Tool: +1.5. **On your watch list:** +1.
- **Measured here recently**, within 80% of the cadence: −3.

A star is left out when:

- it does not reach `min_alt` while the Sun is below `sun_alt`;
- the Moon is closer than `moon_sep_min`;
- it is brighter than `bright_limit` or fainter than `faint_limit` (judged by its last magnitude measured here, or else the
  middle of its catalogue range);
- the Target Tool marks it as visual or spectroscopy only, or as near the Sun.

The tab lists every star left out and why. Chosen stars are placed as close to their highest point as the free time allows.
Each sequence covers every filter, plus the readout, filter-change and slew overheads. A time-series target gets a block of
`series_min` minutes. That covers watch-list stars with `series_min`, and Target Tool stars whose notes ask for a time series.

## How a measurement is made

1. **Comparison stars.** These come from the AAVSO Variable Star Plotter (VSP) chart for the star, in the chart band for the
   filter. The chart is kept for 30 days. Its chart ID goes into the CHART field.
2. **Aperture photometry.** The aperture is `aperture_fwhm` × the measured FWHM. Background comes from an annulus 3 to 5
   aperture radii out. Stars are re-centred when they are within 1.5 FWHM of where the plate solution puts them.
   Comparison stars are left out when they are:
   - saturated (`saturation_adu`, or SATURATE in the header);
   - below `snr_comp`;
   - too close to the variable.
3. **Ensemble.** Comparison stars that disagree with the others are left out: anything off the median zero point by more
   than 3 robust sigma, or `comp_clip` mag. That catches a variable comparison star or a blend. The comparison star
   closest in brightness to the variable becomes the **check star**; it is kept out of the ensemble. The zero point is
   the weighted mean of the rest. The uncertainty combines the variable's photon noise with the ensemble's scatter.
4. **Warnings** send a measurement to review instead of straight into the report:
   - the variable is saturated;
   - its signal-to-noise is below `snr_min`;
   - the check star is off its chart value by more than `check_tol`;
   - there are fewer than 3 comparison stars;
   - the airmass is above `airmass_max`;
   - calibration frames were missing.

   With `review_all` on, every measurement waits for you.
5. **Groups and transformation.** A filter sequence of one star shares a GROUP number: a new group starts when a filter
   repeats or after `group_gap_min` minutes. If you give transformation coefficients, they are applied within each group.
   For example, `{"V": ["B", "V", 0.031]}` gives V + 0.031 × ((B−V) of the variable − (B−V) of the comparison stars).
   Those lines get TRANS=YES; all others say NO.
6. **The file.** It uses the AAVSO Extended Format with JD dates and MTYPE=STD:
   - for an ensemble, CNAME=ENSEMBLE and CMAG=na, as the format asks; Space Lab lists the comparison stars' AUIDs in NOTES;
   - KNAME is the check star's AUID, and KMAG is its instrumental magnitude;
   - AMASS is the airmass, from the observatory's location.

   Before you see the file it is checked for the header, the field count, field lengths, JD dates, filter codes and the
   ensemble rules.

A measurement that is already in an uploaded file cannot be changed here. Change it at WebObs. Rejecting or accepting
a measurement in a file that has not been uploaded makes that night's file again.

## Before you trust it

This pipeline was tested on synthetic images. It has not yet been tested on your images. Before your first upload:

- For your first few nights, measure some stars both ways: with your usual method (VPhot, AstroImageJ or similar) and
  with Space Lab. Compare the two.
- Watch the check-star column. Its scatter over a night tells you how good the night and the photometry were.
- The AAVSO's *Guide to CCD/CMOS Photometry* is the reference for good practice, including how to find your
  transformation coefficients.

## Settings reference

| Setting | Default | Meaning |
|---|---|---|
| `enabled` | false | Turns the two jobs on |
| `site` | blank | Observatory latitude, longitude (east positive) and height. Blank means Setup's telescope location |
| `obscode` | blank | AAVSO observer code. Blank means the one in Setup |
| `targettool_key` / `targettool_csv` | blank | Target Tool API key, or a CSV exported from the Target Tool (blank means `inbox/vstars/targettool.csv`) |
| `sections` | `["ac"]` | Target Tool sections: ac, ep, cv, eb, spp, lpv, yso, het, misc |
| `watchlist` | `[]` | Your own stars: `name`, optional `cadence_days`, `priority`, `series_min`, `note` |
| `sun_alt`, `min_alt`, `moon_sep_min` | −12, 30, 15 | Darkness, horizon and Moon limits (degrees) |
| `bright_limit`, `faint_limit` | 8, 16 | Magnitudes the camera handles |
| `filters` | B V R I | See above |
| `exp_min`, `exp_max` | 3, 300 | Exposure limits (s) |
| `readout_s`, `filter_change_s`, `overhead_s` | 5, 10, 120 | Time per frame, per filter change and per target (slew, centring, focus) |
| `max_targets`, `series_min` | 60, 60 | Targets per night; minutes for a time-series block |
| `classes` | 15 / 60 / longer | Exposure classes written as the CSV Type |
| `nina_import_dir` | blank | Also copy tonight's CSV here |
| `images_dir`, `calib_dir`, `lookback_days` | blank, blank, 30 | Folders, and how far back to look for new images |
| `pixel_scale`, `saturation_adu`, `gain_e_adu` | header, 60000, header | Camera |
| `aperture_fwhm`, `snr_min`, `snr_comp`, `check_tol`, `comp_clip`, `airmass_max` | 1.6, 20, 50, 0.1, 0.05, 2.5 | Photometry and warnings |
| `group_gap_min`, `transform`, `review_all`, `obstype` | 20, {}, false, CCD | Report file |

## Other tools, and where this fits

| Tool | What it does | How it relates |
|---|---|---|
| N.I.N.A. Target Scheduler | Decides each night which targets in its database to image, and runs them | Space Lab feeds it targets; the scheduling and imaging stay in N.I.N.A. |
| N.I.N.A. ExoPlanets plugin (Nick Hardy) | Inside N.I.N.A., plans and runs exoplanet and variable-star sequences, and works with AAVSO Target Tool exports | Its idea of scaling exposures to a star's brightness is used here for the plan |
| EXOTIC, ExoClock | Exoplanet transit reduction and timing | Space Lab already uses EXOTIC for its Exoplanet Watch part; not used for variable stars |
| AutoPhOT, Photometry Pipeline, Astra | Automated photometry pipelines from research groups | Possible alternatives for the measurement step; Space Lab keeps to AAVSO charts and the AAVSO file format |
