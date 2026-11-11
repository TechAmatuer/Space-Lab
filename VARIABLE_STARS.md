# Variable-star observatory (for AAVSO observers with an automated telescope)

This part of Space Lab is for an observer who already reports CCD or CMOS photometry to the AAVSO and runs an automated
observatory with N.I.N.A. and the Target Scheduler plugin. It takes over the two jobs that eat the observer's time:

1. **Choosing tonight's targets.** AAVSO alerts and campaigns come first, read straight from the AAVSO campaign pages
   (with or without a Target Tool key). After them come the stars whose requested cadence is most overdue, so a campaign
   star with few recent observations rises to the top. A star that just went into outburst here moves up too.
2. **Turning a night of images into a report.** Each image is checked, then each target is measured against the AAVSO's own
   comparison stars. When the night is over, Space Lab writes one AAVSO Extended Format file, checks it against the format's
   rules, and tells you in plain English what to look at before you upload it.

Along the way it watches the things that usually go wrong: clouded or trailed frames, satellite trails, saturated or
non-linear stars, neighbours inside the aperture, comparison stars that vary or sit off their chart value, transformation
coefficients going stale, and slews eating the night. See [What it checks for you](#what-it-checks-for-you).

**Nothing is ever sent to the AAVSO from here.** You look at the file, upload it yourself at WebObs, and press
"I uploaded it". Space Lab does not control the telescope either: N.I.N.A. does.

Everything stays on the computer that runs Space Lab: your location, your Target Tool key and your measurements. None of it
goes on the public Space Lab website.

## How a day goes

| When | What happens | What you do |
|---|---|---|
| 15:30 (local time of the computer running Space Lab) | **Tonight's plan.** Targets come from the AAVSO Target Tool, the AAVSO campaign pages and your own watch list. Each one gets a need score, and the plan checks altitude, Moon and brightness. Stars that share a field become one pointing; once a month a standard field is added. The result is written as a N.I.N.A. Target Scheduler CSV | Import the CSV into Target Scheduler (one import, see below) |
| Night | N.I.N.A. observes | Nothing |
| Every hour, also at night | **Measurement.** New FITS images are checked, calibrated, plate-solved (or fitted to Gaia stars) and measured against the AAVSO chart's comparison stars. Each new magnitude is compared with the star's recent AAVSO data | Look at the measurements that have a warning, and accept or reject each one. An outburst or fade reaches you as a notification |
| After local noon | **Report.** The finished night's clouds, extinction and overheads are worked out, then it becomes one AAVSO Extended Format file, checked, with a list of what to look at | Read the list, download the file, upload it at [WebObs](https://www.aavso.org/webobs/file), then press "I uploaded it" |

Everything is on the **Variable-star observatory** tab of the local Space Lab page, http://localhost:8790 (under Research). Tasks appear on the Today tab when
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

Open Space Lab (double-click **Start Space Lab.command**) → **Variable-star observatory** → **Settings** at the bottom of the page. Change the values and save.
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

The CSV's **Type** column carries an exposure class: `VS short`, `VS medium`, `VS long`, `VS series` (time series) and
`VS standard` (a standard field for the colour transformation).
In Target Scheduler:

1. Make a project for AAVSO targets. Give it the priority you want compared with your other projects.
2. Add four template targets named after the classes. Give each one an exposure plan for your filters:
   - `VS short`: exposures up to 15 s (bright stars)
   - `VS medium`: up to 60 s
   - `VS long`: longer exposures
   - `VS series`: a long block for time-series targets
   - `VS standard`: three frames in each filter, exposed for stars of magnitude 10 to 14 (once a month)

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

## What it checks for you

### AAVSO campaigns and alert notices
Space Lab reads the AAVSO's list of active campaigns (apps.aavso.org/v2/campaigns) once a day and each campaign page at most
once a week. The page's fixed fields (dates, targets table, filters) are read directly. The free text ("nightly, several
times a night in outburst; DSLR and visual not needed") is read by the local AI, and its answer is checked: a target must
appear on the page, filters must be real filters, dates real dates. Without the local AI, simple rules read it.
Campaign stars go into the plan with the campaign's filters (only those, or the nearest you have: Sloan r → R), cadence and
time-series request, until the campaign's end date. This works without a Target Tool key. The **AAVSO campaigns** list on
the tab shows what was read and how.

### Outbursts and fades
Every new magnitude is compared with the star's last 30 days of AAVSO data (from the AAVSO International Database), or with
this observatory's own measurements when there are none, using what kind of star it is: a dwarf nova a magnitude brighter is
in outburst, an R CrB star a magnitude fainter is in a dip, a VY Scl star that fades is in a low state; eclipsing and
pulsating stars are expected to change and are left alone. Two agreeing points with a good check star are needed. You get a
Mac notification (and a phone push if `alerts.ntfy_topic` is set), the star gets priority for `boost_days`, and a dwarf
nova gets a time-series block. Nothing is posted anywhere for you.

### Frame check
Before an image is measured it is compared with the earlier images of the same field: how many stars it shows, their width
and elongation, and the sky level. A frame with almost no stars (cloud, dew, a closed roof) or trailed stars is set aside
with the reason and listed under **Night quality**, with a **Measure anyway** button. A borderline frame (thin cloud, a
bright sky, slightly elongated stars) is measured with a note; if the local vision AI is running it looks at a preview, and
when it is confident the frame is bad the measurement waits for you.

### Satellite trails, the linear range, neighbours
- A satellite or aircraft trail across the variable flags that measurement; a trail across a comparison star removes only
  that star from that frame.
- A comparison star above the camera's linear range is left out, and a variable above it is flagged. Set `linear_adu` from
  your camera's linearity test; without it, 85% of saturation is used.
- A neighbour inside the variable's aperture is found in Gaia DR3 (the stars within an arcminute, looked up once per star);
  above `blend_max` (5% extra light) the measurement is flagged. A comparison star with a detected neighbour inside its
  aperture is left out.

### Comparison-star health and CHET reports
Every comparison star's agreement with the others is kept for every frame, with any colour trend removed (that is a colour
term, not a bad star), and compared night after night. A star whose magnitude changes from night to night, or scatters
within a night, leaves the ensemble at once, and the measurements not yet in a file that used it are measured again. A star
that sits well off its chart value is watched (and left out when it is far off). Each chart's comparison stars are also
looked up in AAVSO VSX (a listed variable is left out) and in Gaia DR3 (a flagged one is watched). For each finding a report
for the AAVSO sequence team is drafted on the tab: read it, adjust it and send it yourself through
[CHET](https://www.aavso.org/chet-help). You can keep using a star the check flagged ("Keep using it").

### Clouds and extinction
When a night is over, the zero points of all frames (chart magnitude minus instrumental magnitude of the comparison stars)
are fitted against airmass, filter by filter. That gives the night's extinction and scatter. A frame that lost more than half
its light to cloud is flagged; a smaller loss is noted. A night with a large scatter is called not photometric: the
differential magnitudes are still fine, but the check-star values deserve a look.

### Colour transformation, measured on standard fields
With no coefficients of your own in `transform`, Space Lab measures them: once a month (and on clear nights until there is a
first set) the plan includes a standard field, M67 or NGC 7790, or one you add to `standard_fields`. Its standard stars come
from the AAVSO plotter's standard-field charts, and each night's coefficients are fitted the way the AAVSO's Transform
Generator does it (Tbv, Tv_bv, Tb_bv, Tvr, Tr_vr, Tvi, Ti_vi, ...). Two nights that agree start the set; later nights that
agree are averaged in. A night that disagrees (new filters, camera or optics?) waits for you to accept or reject it under
**Colour transformation**. With your own coefficients set, they stay in charge unless `transform_auto` is true; with
`transform_auto` false nothing is measured. The colour coefficient is applied too: `{"V": ["B", "V", 0.031, 1.05]}`.

### Overheads and shared fields
The gaps between your exposures are measured from the images' own time stamps: downloads, filter changes and slews. The
planner uses them, so tonight's plan fits the night as it really runs, and **Night quality** shows how much of the night the
shutter was open. Stars close enough to share a field (and similar in brightness) are planned as one pointing, named after
the star that needs it most; every program star in a frame is measured. That saves a slew, a plate solve and a settle for
each extra star. Set `fov_arcmin` if your images do not tell Space Lab the field size.

### Before you upload
Each night's file comes with a short list: the format check, whether the night was photometric, frames that lost light,
how well the check stars agree, transformed lines, high airmass, low signal-to-noise, and any outburst or fade in the file.
With the local AI running it is worded as two or three sentences; the AI may not add a number that is not in the checks,
or the plain list is used instead.

### Sharing coverage with other observers (optional, off)
With `share_coverage` on, the stars you measured in the last three weeks (names, filters and nights only) and tonight's
plan go on your public Space Lab page. Other Space Lab observatories that list your page in their `peers` give those stars a
lower priority on the nights you cover them, and you can list theirs. Nothing else about the observatory is published.

### Speed
Many images at once (a time series) are measured by several worker processes (`workers`, 0 = automatic); the results are
identical to measuring them one at a time. The calibration masters are built once and kept on disk instead of being rebuilt
every hour. The variable-star jobs run in a lane of their own, so they never wait behind a long TESS or library run.

## Before you trust it

This pipeline was tested on synthetic images (made-up stars, fields and campaign pages). It has not yet been tested on your
images. Before your first upload:

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
| `workers` | 0 | Images measured at a time (0 = automatic from the CPU cores) |
| `ai` | true | The local AI reads campaign pages, looks at borderline frames and words the pre-upload list |
| `campaigns`, `campaign_cadence_days` | true, 1 | Read the AAVSO campaign pages; cadence when a campaign does not say |
| `outburst_days`, `outburst_min_points`, `boost_days` | 30, 2, 7 | Outburst and fade check |
| `qc_stars_bad`, `qc_stars_low`, `qc_elong_max`, `qc_elong_bad` | 0.15, 0.45, 1.6, 2.2 | Frame check (fractions of the field's usual star count; star elongation) |
| `qc_cloud_mag`, `qc_cloud_bad`, `qc_photometric_scatter` | 0.25, 0.75, 0.08 | Cloud note, cloud flag, photometric night (magnitudes) |
| `qc_ai`, `qc_ai_max`, `qc_ai_confidence` | true, 30, 0.6 | The vision AI for borderline frames: on, per run, how sure it must be |
| `linear_adu`, `blend_max`, `comp_blend_max` | 85% of saturation, 0.05, 0.03 | Linear range; neighbour light that flags the variable or drops a comparison star |
| `catalog_check`, `comp_var_max`, `comp_offset_max` | true, 0.04, 0.08 | Comparison-star health |
| `transform_auto`, `transform_every_days`, `standard_fields`, `standard_frames` | auto, 30, [], 3 | Coefficients from standard fields |
| `group_fields`, `fov_arcmin`, `group_dmag` | true, from the images, 2.5 | Stars that share a field become one pointing |
| `auto_overhead` | true | Plan with the overheads measured from your images |
| `measure_all_program_stars` | true | Measure every planned or watched star that falls in a frame |
| `share_coverage`, `peers` | false, [] | Coverage on the public page; other Space Lab pages to read |

## Other tools, and where this fits

| Tool | What it does | How it relates |
|---|---|---|
| N.I.N.A. Target Scheduler | Decides each night which targets in its database to image, and runs them | Space Lab feeds it targets; the scheduling and imaging stay in N.I.N.A. |
| N.I.N.A. ExoPlanets plugin (Nick Hardy) | Inside N.I.N.A., plans and runs exoplanet and variable-star sequences, and works with AAVSO Target Tool exports | Its idea of scaling exposures to a star's brightness is used here for the plan |
| EXOTIC, ExoClock | Exoplanet transit reduction and timing | Space Lab already uses EXOTIC for its Exoplanet Watch part; not used for variable stars |
| AutoPhOT, Photometry Pipeline, Astra | Automated photometry pipelines from research groups | Possible alternatives for the measurement step; Space Lab keeps to AAVSO charts and the AAVSO file format |
| VPhot, Transform Generator, TransformApplier | The AAVSO's own photometry, coefficient and transformation tools | Space Lab fits coefficients the way Transform Generator does and applies them like TransformApplier, automatically |
| Asterism (First Light Observatory Systems) | A central server that hands observation requests to N.I.N.A. observatories | Access needs credentials from its makers, so Space Lab does not connect to it; the optional coverage sharing above is the open, simple alternative |
| Built on | astropy, sep (Source Extractor), astroalign, astroquery (VizieR: Gaia DR3, AAVSO VSX), scikit-image, Pillow | Used as libraries, so their fixes and improvements come with each update |
