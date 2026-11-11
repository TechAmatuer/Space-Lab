"""Variable-star observatory, part 2: from the observatory's images to an AAVSO report file (see VARIABLE_STARS.md).

Every hour, for each new image in the observatory's image folder:
- which star (the FITS OBJECT name, else the pointing), which filter (mapped to an AAVSO filter code in settings),
  the mid-exposure time and the airmass;
- calibration with master bias, dark and flat frames when a calibration folder is set (the masters are built once and
  kept, so each hourly run and each worker reads them instead of rebuilding them);
- a frame check first (spacelab/vsqc.py): how many stars, trailing, sky brightness and satellite trails; clearly bad
  frames are set aside with the reason, borderline ones can be shown to the local vision model;
- where the stars are: the image's own plate solution (WCS) when it has one, otherwise a fit to Gaia stars;
- comparison stars from the AAVSO Variable Star Plotter (the chart for that star, kept for 30 days), minus the ones the
  comparison-star health check found variable;
- aperture photometry of the variable and every comparison star; the variable's magnitude from the ensemble of
  comparison stars, with the comparison star closest in brightness kept aside as the check star. Every program star in
  the frame is measured (two stars that share a field are planned as one pointing);
- checks: saturation and the camera's linear range, signal-to-noise, a neighbour inside the aperture (Gaia), a trail
  across the star, the check star against its chart value, too few comparison stars, high airmass;
- images of a standard field give the colour transformation coefficients instead (spacelab/vscal.py).
Measurements without a problem are ready; the others wait for the observer on the local Space Lab page. When a night is
over: the night's zero points against airmass (clouds, extinction), the overheads between exposures, the standard-field
coefficients; then its ready measurements become one file in the AAVSO Extended File Format: a filter sequence shares one
GROUP number, colour transformation is applied (measured coefficients, or the observer's own), and the file is checked against
the format's rules, with a plain-English list of what to look at before uploading. Each new measurement is also compared with
the star's recent behaviour, so an outburst or a fade is reported to the observer within the hour (spacelab/vsnews.py).
The observer uploads the file at WebObs: nothing is ever sent to the AAVSO from here.

Many images at once (a time series) are measured in parallel by worker processes (setting "workers", 0 = automatic); the
results are the same as one at a time.
"""
import hashlib
import json
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config, db, net, vscal, vsnews, vsqc
from .vsobs import angle, settings, vsx_object

VSP_API = 'https://app.aavso.org/vsp/api/chart/'
WEBOBS = 'https://www.aavso.org/webobs/file'
FITS_EXT = ('.fit', '.fits', '.fts', '.fit.gz', '.fits.gz')
AAVSO_FILTERS = {'U', 'B', 'V', 'R', 'I', 'J', 'H', 'K', 'TG', 'TB', 'TR', 'CV', 'CR', 'SU', 'SG', 'SR', 'SI', 'SZ', 'STU', 'STV', 'STB',
                 'STY', 'STHBW', 'STHBN', 'MA', 'MB', 'MI', 'ZS', 'Y', 'HA', 'HAC'}
FIELDS = ['NAME', 'DATE', 'MAG', 'MERR', 'FILT', 'TRANS', 'MTYPE', 'CNAME', 'CMAG', 'KNAME', 'KMAG', 'AMASS', 'GROUP', 'CHART', 'NOTES']
LIMITS = {'NAME': 30, 'DATE': 16, 'MAG': 8, 'MERR': 6, 'CNAME': 20, 'CMAG': 8, 'KNAME': 20, 'KMAG': 8, 'AMASS': 7, 'GROUP': 5, 'CHART': 20}
FOVS = [15, 30, 60, 120, 180]
VS = config.DATA / 'vstars'
CHARTS = VS / 'charts'
REPORTS = VS / 'reports'
GAIA = VS / 'gaia'
PLOTS = config.PLOTS / 'vstars'
OFFLINE = bool(os.environ.get('SPACELAB_OFFLINE'))     # tests: no catalogue, AAVSO-data or local-AI calls unless they are handed in

NEW_COLUMNS = {'vs_frames': {'t0': 'REAL', 'exp': 'REAL', 'obj': 'TEXT', 'filt': 'TEXT', 'qc': 'TEXT', 'night': 'TEXT'},
               'vs_meas': {'zp': 'REAL', 'comp_res': 'TEXT', 'notes': 'TEXT', 'qc': 'TEXT'}}


def ensure(con):
    con.execute('CREATE TABLE IF NOT EXISTS vs_frames (path TEXT PRIMARY KEY, mtime REAL, target TEXT, filter TEXT, jd REAL, '
                'status TEXT, note TEXT, at REAL)')
    con.execute('CREATE TABLE IF NOT EXISTS vs_meas (id INTEGER PRIMARY KEY, frame TEXT, target TEXT, filter TEXT, jd REAL, '
                'mag REAL, mag_raw REAL, err REAL, snr REAL, trans INTEGER DEFAULT 0, cname TEXT, cmag REAL, kname TEXT, kmag REAL, '
                'kdev REAL, ncomp INTEGER, comps TEXT, airmass REAL, grp INTEGER, chart TEXT, flags TEXT, status TEXT, report TEXT, '
                'ci_comp REAL, night TEXT, at REAL)')
    con.execute('CREATE TABLE IF NOT EXISTS vs_std (frame TEXT PRIMARY KEY, field TEXT, night TEXT, filter TEXT, jd REAL, airmass REAL, '
                'stars TEXT, chart TEXT, at REAL)')
    for table, cols in NEW_COLUMNS.items():
        have = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
        for c, typ in cols.items():
            if c not in have:
                try:
                    con.execute(f'ALTER TABLE {table} ADD COLUMN {c} {typ}')
                except Exception:
                    pass
    for sql in ('CREATE INDEX IF NOT EXISTS vs_meas_status ON vs_meas(status)',
                'CREATE INDEX IF NOT EXISTS vs_meas_target_night ON vs_meas(target, night)',
                'CREATE INDEX IF NOT EXISTS vs_meas_frame ON vs_meas(frame)',
                'CREATE INDEX IF NOT EXISTS vs_frames_target ON vs_frames(target, filter)',
                'CREATE INDEX IF NOT EXISTS vs_frames_night ON vs_frames(night)',
                'CREATE INDEX IF NOT EXISTS vs_std_night ON vs_std(night)'):
        con.execute(sql)


def slug(x):
    return ''.join(c if c.isalnum() else '_' for c in str(x)).strip('_')[:60] or 'star'


def night_of(jd, lon=0.0):
    """The observing night a time belongs to: the date of the evening it started (local noon to local noon)."""
    t = (jd - 2440587.5) * 86400 + lon / 15 * 3600 - 12 * 3600
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d')


# ------------------------------------------------------------------ images
def fits_files(root, newer_than=0.0):
    root = Path(str(root)).expanduser()
    if not root.is_dir():
        return []
    out = []
    for p in root.rglob('*'):
        if p.is_file() and p.name.lower().endswith(FITS_EXT) and not p.name.startswith('.'):
            try:
                if p.stat().st_mtime >= newer_than:
                    out.append(p)
            except OSError:
                continue
    return sorted(out)


def read(path):
    from astropy.io import fits
    with fits.open(str(path), memmap=False) as h:
        for hdu in h:
            if hdu.data is not None and getattr(hdu.data, 'ndim', 0) == 2:
                return np.asarray(hdu.data, dtype=np.float64), hdu.header.copy()
    raise ValueError('no image in this file')


def exptime(hdr):
    return float(hdr.get('EXPTIME') or hdr.get('EXPOSURE') or 0.0)


def filter_of(hdr, s):
    """The settings entry for the image's FILTER keyword (case ignored; 'aliases' adds other spellings), or None."""
    name = str(hdr.get('FILTER') or hdr.get('FILTER1') or '').strip().lower()
    for f in s.get('filters') or []:
        names = {str(f.get('fits') or '').lower(), str(f.get('aavso') or '').lower()} | {str(a).lower() for a in f.get('aliases') or []}
        if name and name in names:
            return f
    return None


def chart_band(s, aavso_code):
    for f in s.get('filters') or []:
        if f.get('aavso') == aavso_code:
            return f.get('chart') or aavso_code
    return {'R': 'Rc', 'I': 'Ic'}.get(aavso_code, aavso_code)


def jd_mid(hdr):
    """Julian Date at mid-exposure (DATE-OBS is the start, in UTC, as N.I.N.A. and most capture programs write it)."""
    exp = exptime(hdr)
    for k in ('JD-OBS', 'JD_UTC', 'JD'):
        if hdr.get(k):
            return float(hdr[k]) + exp / 2 / 86400
    d = str(hdr.get('DATE-OBS') or '').strip()
    if 'T' not in d and hdr.get('TIME-OBS'):
        d = d + 'T' + str(hdr['TIME-OBS'])
    t = datetime.fromisoformat(d.replace('Z', '')[:26]).replace(tzinfo=timezone.utc).timestamp()
    return t / 86400 + 2440587.5 + exp / 2 / 86400


def airmass(site, ra, dec, jd):
    """Airmass from the observatory's latitude and longitude (Hardie's formula); None below 3° altitude."""
    lst = (280.46061837 + 360.98564736629 * (jd - 2451545.0) + site['lon']) % 360
    h = math.radians((lst - ra) % 360)
    lat, d = math.radians(site['lat']), math.radians(dec)
    sinalt = math.sin(lat) * math.sin(d) + math.cos(lat) * math.cos(d) * math.cos(h)
    if sinalt <= 0.05:
        return None
    sec = 1 / sinalt - 1
    return round(1 + sec - 0.0018167 * sec - 0.002875 * sec ** 2 - 0.0008083 * sec ** 3, 4)


def pointing(hdr):
    """Where the telescope pointed (degrees), from RA/DEC (degrees) or OBJCTRA/OBJCTDEC (sexagesimal)."""
    ra, dec = hdr.get('RA'), hdr.get('DEC')
    if isinstance(ra, (int, float)) and isinstance(dec, (int, float)):
        return float(ra), float(dec)
    ra, dec = angle(hdr.get('OBJCTRA'), hours=True), angle(hdr.get('OBJCTDEC'))
    return (ra, dec) if ra is not None and dec is not None else (None, None)


def pixel_scale(hdr, s):
    """Arcseconds per pixel: the setting, else the header (SCALE/PIXSCALE, or pixel size and focal length)."""
    if s.get('pixel_scale'):
        return float(s['pixel_scale'])
    for k in ('PIXSCALE', 'SCALE', 'SECPIX'):
        if hdr.get(k):
            return float(hdr[k])
    if hdr.get('XPIXSZ') and hdr.get('FOCALLEN'):
        return 206.265 * float(hdr['XPIXSZ']) / float(hdr['FOCALLEN'])
    return None


# ------------------------------------------------------------------ calibration
_MASTERS = {}
MASTER_DIR = config.CACHE / 'vs_masters'


def _build_masters(files):
    out = {'bias': None, 'dark': None, 'flat': {}}
    kinds = {'bias': [], 'dark': [], 'flat': {}}
    for p in files:
        try:
            img, h = read(p)
        except Exception:
            continue
        t = (str(h.get('IMAGETYP') or h.get('FRAMETYP') or '') + ' ' + p.name).lower()
        if 'bias' in t or 'offset' in t or 'zero' in t:
            kinds['bias'].append(img)
        elif 'dark' in t:
            kinds['dark'].append((img, exptime(h)))
        elif 'flat' in t:
            kinds['flat'].setdefault(str(h.get('FILTER') or '').strip().lower(), []).append(img)
    if kinds['bias']:
        out['bias'] = np.median(np.stack(kinds['bias'][:50]), axis=0)
    b = out['bias'] if out['bias'] is not None else 0.0
    rates = [(d - b) / e for d, e in kinds['dark'][:50] if e > 0]
    if rates:
        out['dark'] = np.median(np.stack(rates), axis=0)
    for f, imgs in kinds['flat'].items():
        m = np.median(np.stack(imgs[:50]), axis=0) - b
        med = float(np.median(m))
        if med > 0:
            out['flat'][f] = m / med
    return out


def masters(calib_dir):
    """Master bias, dark (per second) and flats (per filter, normalised) from a folder of calibration frames. Built once for
    a given set of calibration files and kept in data/cache/vs_masters (later runs and worker processes map them from disk)."""
    key = str(calib_dir or '')
    if key in _MASTERS:
        return _MASTERS[key]
    files = fits_files(calib_dir)[:600] if calib_dir else []
    if not files:
        _MASTERS[key] = {'bias': None, 'dark': None, 'flat': {}}
        return _MASTERS[key]
    sig = hashlib.sha1(json.dumps([[str(p), p.stat().st_mtime, p.stat().st_size] for p in files]).encode()).hexdigest()[:16]
    d = MASTER_DIR / sig
    meta = d / 'meta.json'
    out = None
    if meta.exists():
        try:
            m = json.loads(meta.read_text())
            out = {'bias': np.load(d / 'bias.npy', mmap_mode='r') if m.get('bias') else None,
                   'dark': np.load(d / 'dark.npy', mmap_mode='r') if m.get('dark') else None,
                   'flat': {f: np.load(d / fn, mmap_mode='r') for f, fn in (m.get('flats') or {}).items()}}
        except Exception:
            out = None
    if out is None:
        out = _build_masters(files)
        try:
            d.mkdir(parents=True, exist_ok=True)
            flats = {}
            for i, (f, arr) in enumerate(out['flat'].items()):
                np.save(d / f'flat-{i}.npy', arr)
                flats[f] = f'flat-{i}.npy'
            if out['bias'] is not None:
                np.save(d / 'bias.npy', out['bias'])
            if out['dark'] is not None:
                np.save(d / 'dark.npy', out['dark'])
            meta.write_text(json.dumps({'bias': out['bias'] is not None, 'dark': out['dark'] is not None, 'flats': flats,
                                        'files': len(files), 'made': time.time()}))
            old = sorted((p for p in MASTER_DIR.iterdir() if p.is_dir() and p != d), key=lambda p: p.stat().st_mtime)
            for p in old[:-2]:                         # keep the two previous sets, drop older ones
                for f in p.iterdir():
                    f.unlink(missing_ok=True)
                p.rmdir()
        except OSError:
            pass
    _MASTERS[key] = out
    return out


def calibrate(img, hdr, m):
    done, exp = [], exptime(hdr)
    if m.get('bias') is not None and m['bias'].shape == img.shape:
        img = img - m['bias']
        done.append('bias')
    if m.get('dark') is not None and m['dark'].shape == img.shape and exp > 0:
        img = img - m['dark'] * exp
        done.append('dark')
    fl = (m.get('flat') or {}).get(str(hdr.get('FILTER') or '').strip().lower())
    if fl is not None and fl.shape == img.shape:
        img = img / np.where(fl > 0.05, fl, 1.0)
        done.append('flat')
    return img, done


# ------------------------------------------------------------------ where the stars are
def _solve(img_xy, cat_xy, scale_guess=None):
    """Similarity transform (with a possible mirror flip) from catalogue tangent-plane arcsec to pixels: (tf, flip, n)."""
    import astroalign as aa
    best = None
    for n in (25, 40, 60):
        if len(img_xy) < 8:
            break
        for k in (1.5, 2.0, 1.0, 3.0):
            for flip in (1.0, -1.0):
                try:
                    tf, (src, _dst) = aa.find_transform(cat_xy[:int(n * k)] * np.array([flip, 1.0]), img_xy[:n])
                except Exception:
                    continue
                sc = 1.0 / tf.scale if tf.scale else 0
                ok = (0.4 * scale_guess <= sc <= 2.5 * scale_guess) if scale_guess else (0.1 <= sc <= 30)
                if ok and len(src) >= 8 and (best is None or len(src) > best[2]):
                    best = (tf, flip, len(src))
        if best and best[2] >= 15:
            break
    return best


def sky_to_pixels(img, hdr, s, gaia_fn=None):
    """A function (ra, dec arrays) -> (x, y) pixel arrays, and in words how it was found: the image's WCS, else Gaia."""
    from astropy.wcs import WCS
    try:
        w = WCS(hdr)
        if w.has_celestial and 'RA' in str(hdr.get('CTYPE1', '')).upper():
            return (lambda ra, dec: w.all_world2pix(np.atleast_1d(ra), np.atleast_1d(dec), 0)), 'the image\'s own plate solution'
    except Exception:
        pass
    from .exowatch import detect, gaia_field, project
    ra0, dec0 = pointing(hdr)
    if ra0 is None:
        return None, 'no plate solution and no pointing (RA/DEC) in the image'
    xy, _flux, _peak = detect(img)
    scale = pixel_scale(hdr, s)
    radius = min(1.5, max(0.15, max(img.shape) * (scale or 1.5) / 3600 * 0.75))
    stars = sorted((gaia_fn or gaia_field)(ra0, dec0, radius, float(s.get('faint_limit', 16)) + 1.0), key=lambda r: r['g'])
    if len(stars) < 10 or len(xy) < 10:
        return None, 'too few stars to fit a plate solution'
    best = _solve(xy[:60], project(stars, ra0, dec0), scale)
    if not best:
        return None, 'could not match the image to Gaia stars (check the pixel scale in settings)'
    tf, flip, n = best

    def to_pix(ra, dec):
        p = project([{'ra': a, 'dec': d} for a, d in zip(np.atleast_1d(ra), np.atleast_1d(dec))], ra0, dec0)
        q = tf(np.column_stack([p[:, 0] * flip, p[:, 1]]))
        return q[:, 0], q[:, 1]
    return to_pix, f'fitted to {n} Gaia stars'


# ------------------------------------------------------------------ comparison stars (AAVSO Variable Star Plotter)
def parse_chart(js):
    """VSP answer -> {'chartid', 'comps': [{auid, label, ra, dec, mags: {band: [mag, err]}}]}."""
    comps = []
    for c in (js or {}).get('photometry') or []:
        mags = {str(b.get('band')): [float(b['mag']), float(b.get('error') or 0.0)] for b in c.get('bands') or [] if b.get('mag') is not None}
        r, d = angle(c.get('ra'), hours=True), angle(c.get('dec'))
        if r is not None and d is not None and mags:
            comps.append({'auid': str(c.get('auid') or ''), 'label': str(c.get('label') or ''), 'ra': r, 'dec': d, 'mags': mags})
    return {'chartid': str((js or {}).get('chartid') or 'na'), 'comps': comps}


def chart(name, fov_arcmin, maglimit, fetch=None, max_age_days=30):
    """The AAVSO chart for this star: {'chartid', 'comps': [{auid, label, ra, dec, mags: {band: [mag, err]}}]}, kept 30 days."""
    CHARTS.mkdir(parents=True, exist_ok=True)
    p = CHARTS / (slug(name) + '.json')
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        return json.loads(p.read_text())
    fov = next((f for f in FOVS if f >= fov_arcmin), FOVS[-1])
    params = {'star': name, 'fov': fov, 'maglimit': round(float(maglimit), 1), 'format': 'json'}
    js = (fetch or (lambda q: net.fetch_json(VSP_API, params=q, timeout=90)))(params)
    out = dict(parse_chart(js), fov=fov)
    if out['comps']:
        p.write_text(json.dumps(out))
    return out


def without(ch, gone):
    """The chart with the comparison stars the health check took out of the ensemble removed."""
    if not gone:
        return ch
    return dict(ch, comps=[c for c in ch['comps'] if (c.get('auid') or c.get('label')) not in gone])


# ------------------------------------------------------------------ photometry
def aperture(sky, s):
    """Typical star width (FWHM, pixels) and the aperture radius used for every star in the frame."""
    fwhm, _good = vsqc.fwhm_of(sky)
    return fwhm, min(max(float(s.get('aperture_fwhm', 1.6)) * fwhm, 2.0), 30.0)


def photometry(sky, hdr, s, x, y, fwhm, r, edge=None):
    """Aperture photometry at pixel positions (x, y): {index: {'m', 'e', 'snr', 'sat', 'peak', 'x', 'y'}} for the points far
    enough from the edge that have light. Magnitudes per second; the local sky from an annulus of 3 to 5 apertures."""
    import sep
    data, sub = sky['data'], sky['sub']
    h, w = data.shape
    exp = exptime(hdr) or 1.0
    gain = float(hdr.get('EGAIN') or hdr.get('GAIN_E') or s.get('gain_e_adu') or 1.0)
    edge = 5 * r + 3 if edge is None else edge
    x, y = np.asarray(x, float), np.asarray(y, float)
    inside = np.isfinite(x) & np.isfinite(y) & (x > edge) & (x < w - edge) & (y > edge) & (y < h - edge)
    idx = np.flatnonzero(inside)
    if not len(idx):
        return {}
    xw, yw, _f = sep.winpos(sub, x[idx], y[idx], fwhm / 2.3548)
    near = np.hypot(xw - x[idx], yw - y[idx]) < max(4.0, 1.5 * fwhm)      # centre on the star only if it is where it should be
    xs, ys = np.where(near, xw, x[idx]), np.where(near, yw, y[idx])
    flux, ferr, _ = sep.sum_circle(data, xs, ys, r, err=vsqc.rms_map(sky), gain=gain, bkgann=(3 * r, 5 * r))
    got = {}
    for k, i in enumerate(idx):
        if not flux[k] > 0:
            continue
        yy, xx, rr = int(round(ys[k])), int(round(xs[k])), int(math.ceil(r))
        peak = float(data[max(0, yy - rr):yy + rr + 1, max(0, xx - rr):xx + rr + 1].max())
        got[i] = {'m': -2.5 * math.log10(flux[k] / exp), 'e': 1.0857 * ferr[k] / flux[k], 'snr': float(flux[k] / max(ferr[k], 1e-9)),
                  'sat': peak >= sky['sat'], 'peak': peak, 'x': float(xs[k]), 'y': float(ys[k]), 'flux': float(flux[k])}
    return got


def neighbour_light(sky, o, r, fwhm):
    """Light of other detected stars falling inside the aperture at o, as a fraction of o's own light (0 when none)."""
    objs = sky['objs']
    if not len(objs):
        return 0.0
    d = np.hypot(objs['x'] - o['x'], objs['y'] - o['y'])
    sel = (d > max(1.5, 0.5 * fwhm)) & (d < r + fwhm)
    if not sel.any():
        return 0.0
    w = np.clip((r + fwhm - d[sel]) / (2 * fwhm), 0, 1)
    return float(np.sum(objs['flux'][sel] * w) / max(o['flux'], 1e-9))


def blend_fraction(star, near, r_arcsec, fwhm_arcsec, mag_hint=None):
    """Light of Gaia neighbours inside the variable's aperture, as a fraction of the variable's own (G band): 0 when none."""
    if not near:
        return 0.0
    cosd = math.cos(math.radians(star['dec']))
    dist = lambda g: math.hypot(((g['ra'] - star['ra'] + 180) % 360 - 180) * cosd, g['dec'] - star['dec']) * 3600
    pts = sorted(((dist(g), g['g']) for g in near), key=lambda x: x[0])
    own = next((g for d, g in pts if d < 2.0), None)
    ref = own if own is not None else mag_hint
    if ref is None:
        return 0.0
    frac = 0.0
    for d, g in pts:
        if d < 2.0 and g == own:
            continue
        if d >= r_arcsec + fwhm_arcsec:
            break
        w = min(1.0, max(0.0, (r_arcsec + fwhm_arcsec - d) / (2 * fwhm_arcsec)))
        frac += 10 ** (-0.4 * (g - ref)) * w
    return frac


def _colour(c):
    m = c['mags']
    if 'B' in m and 'V' in m:
        return m['B'][0] - m['V'][0]
    for b in ('Ic', 'I', 'Rc', 'R'):
        if 'V' in m and b in m:
            return m['V'][0] - m[b][0]
    return None


def measure(img, hdr, to_pix, star, ch, band, s, cpair=None, sky=None, near=None):
    """The variable's standard magnitude in `band` from the ensemble of chart comparison stars, or {'error': words}."""
    sky = sky if sky is not None else vsqc.extract(img, hdr, s)
    data = sky['data']
    h, w = data.shape
    fwhm, r = aperture(sky, s)
    pts = [(None, star['ra'], star['dec'])] + [(c, c['ra'], c['dec']) for c in ch['comps']]
    x, y = to_pix(np.array([p[1] for p in pts]), np.array([p[2] for p in pts]))
    x, y = np.asarray(x, float), np.asarray(y, float)
    edge = 5 * r + 3
    if not (np.isfinite(x[0]) and np.isfinite(y[0]) and edge < x[0] < w - edge and edge < y[0] < h - edge):
        return {'error': 'the variable is outside the image or too close to its edge'}
    got = photometry(sky, hdr, s, x, y, fwhm, r, edge)
    t = got.get(0)
    if not t:
        return {'error': 'no light measured at the variable'}
    ident = lambda c: c['c']['auid'] or c['c']['label']
    segs = sky.get('streaks') or []
    notes, comps = [], []
    for i, o in got.items():
        c = pts[i][0]
        if i == 0 or band not in c['mags'] or o['sat'] or o['snr'] < float(s.get('snr_comp', 50)):
            continue
        if math.hypot(o['x'] - t['x'], o['y'] - t['y']) < 3 * r:          # blended with the variable
            continue
        cid = c['auid'] or c['label']
        if o['peak'] >= sky['lin']:
            notes.append(f"comparison star {cid} left out: above the camera's linear range")
            continue
        if segs and vsqc.near_streak(o['x'], o['y'], r, segs):
            notes.append(f'comparison star {cid} left out: a satellite or aircraft trail crosses it')
            continue
        if neighbour_light(sky, o, r, fwhm) > float(s.get('comp_blend_max', 0.03)):
            notes.append(f'comparison star {cid} left out: a neighbour inside its aperture')
            continue
        comps.append(dict(o, c=c, cat=c['mags'][band]))
    if not comps:
        return {'error': f'no usable comparison star with a {band} magnitude on AAVSO chart {ch["chartid"]}'}
    flags = []
    if t['sat']:
        flags.append('variable saturated')
    elif t['peak'] >= sky['lin']:
        flags.append(f"variable above the camera's linear range (peak {t['peak']:.0f} ADU)")
    if t['snr'] < float(s.get('snr_min', 20)):
        flags.append(f"low signal-to-noise ({t['snr']:.0f})")
    if segs and vsqc.near_streak(t['x'], t['y'], r, segs):
        flags.append('a satellite or aircraft trail crosses the variable')
    scale = pixel_scale(hdr, s)
    bl = blend_fraction(star, near, r * scale, fwhm * scale, star.get('mag_plan')) if near and scale else neighbour_light(sky, t, r, fwhm)
    if bl > float(s.get('blend_max', 0.05)):
        flags.append(f'a neighbouring star adds about {bl:.0%} to the variable\'s light')
    elif bl > 0.01:
        notes.append(f'a faint neighbour adds about {bl:.1%} to the variable\'s light')
    zp = np.array([c['cat'][0] - c['m'] for c in comps])
    keep = np.ones(len(comps), bool)
    for _ in range(3):                    # leave out comparison stars that disagree (a variable comp, a blend): median and MAD
        if keep.sum() < 3:
            break
        m0 = float(np.median(zp[keep]))
        sd = 1.4826 * float(np.median(np.abs(zp[keep] - m0)))
        new = keep & (np.abs(zp - m0) < max(3 * sd, float(s.get('comp_clip', 0.05))))
        if new.sum() == keep.sum():
            break
        keep = new
    good = [c for c, k in zip(comps, keep) if k]
    rough = t['m'] + float(np.median(zp[keep]))
    check = min(good, key=lambda c: abs(c['cat'][0] - rough)) if len(good) >= 3 else None     # kept aside, never in the ensemble
    used = [c for c in good if c is not check]
    zp = np.array([c['cat'][0] - c['m'] for c in used])
    wt = np.array([1 / (c['e'] ** 2 + c['cat'][1] ** 2 + 0.005 ** 2) for c in used])
    zp0 = float(np.average(zp, weights=wt))
    zerr = math.sqrt(1 / wt.sum())
    if len(used) > 1:
        zerr = max(zerr, float(np.std(zp, ddof=1) / math.sqrt(len(used))))
    for c, wi in zip(used, wt):
        c['w'] = float(wi)
    left = [ident(c) for c, k in zip(comps, keep) if not k]
    res = {'mag': t['m'] + zp0, 'err': math.hypot(t['e'], zerr), 'snr': t['snr'], 'fwhm': fwhm, 'ncomp': len(used),
           'comps': [ident(c) for c in used], 'left_out': left, 'flags': flags, 'notes': notes, 'cname': 'ENSEMBLE', 'cmag': None,
           'kname': None, 'kmag': None, 'kdev': None, 'zp': zp0, 'peak': t['peak']}
    if len(used) == 1:
        res.update(cname=ident(used[0]), cmag=used[0]['m'])
        flags.append('only one comparison star')
    if check:
        res.update(kname=ident(check), kmag=check['m'], kdev=check['m'] + zp0 - check['cat'][0])
        if abs(res['kdev']) > float(s.get('check_tol', 0.1)):
            flags.append(f"check star off by {res['kdev']:+.3f} mag")
    else:
        flags.append('no check star (fewer than 3 comparison stars)')
    if cpair:
        ci = [c['c']['mags'][cpair[0]][0] - c['c']['mags'][cpair[1]][0] for c in used if cpair[0] in c['c']['mags'] and cpair[1] in c['c']['mags']]
        res['ci_comp'] = float(np.mean(ci)) if ci else None
    res['comp_res'], res['colour_slope'] = vsqc.comp_residuals(comps, used, zp0, float(wt.sum()), float(np.sum(wt * zp)), ident,
                                                                lambda c: _colour(c['c']))
    return res


# ------------------------------------------------------------------ groups, transformation, the AAVSO file
def assign_groups(rows, gap_min=20.0):
    """A filter sequence shares one GROUP: a new group starts when a filter repeats or after a pause (rows of one star)."""
    g, seen, last = 0, set(), None
    for r in sorted(rows, key=lambda r: r['jd']):
        if last is None or r['filter'] in seen or (r['jd'] - last) * 1440 > gap_min:
            g, seen = g + 1, set()
        seen.add(r['filter'])
        last = r['jd']
        r['grp'] = g
    return rows


def transform(rows, coefs):
    """Colour transformation, e.g. {'V': ['B', 'V', 0.031]} or with the colour coefficient {'V': ['B', 'V', 0.031, 1.05]}: within one
    group, V = v + 0.031 x 1.05 x ((b-v) of the variable - (b-v) of the comparison stars), the instrumental colour difference made
    standard by the colour coefficient (taken as 1 when not given). Those rows get TRANS=YES."""
    for r in rows:
        r['mag'], r['trans'] = r['mag_raw'], 0
    by = {}
    for r in rows:
        by.setdefault(r['grp'], {})[r['filter']] = r
    for fs in by.values():
        for filt, spec in (coefs or {}).items():
            r = fs.get(filt)
            if not r or not isinstance(spec, (list, tuple)) or len(spec) not in (3, 4):
                continue
            a, b = fs.get(spec[0]), fs.get(spec[1])
            if a is None or b is None or r.get('ci_comp') is None:
                continue
            tc = float(spec[3]) if len(spec) == 4 else 1.0
            r['mag'] = r['mag_raw'] + float(spec[2]) * tc * ((a['mag_raw'] - b['mag_raw']) - r['ci_comp'])
            r['trans'] = 1
    return rows


def _f(v, nd):
    return 'na' if v is None else f'{v:.{nd}f}'


def _clean(x, n=None):
    x = str(x if x not in (None, '') else 'na').replace(',', ' ').replace('\n', ' ').strip() or 'na'
    return x[:n] if n else x


def extended(rows, obscode, obstype='CCD', software='Space Lab vstars (ensemble aperture photometry)'):
    """The AAVSO Extended File Format text for these rows (JD dates, comma delimiter, standardised magnitudes)."""
    head = ['#TYPE=EXTENDED', f'#OBSCODE={obscode}', f'#SOFTWARE={software}', '#DELIM=,', '#DATE=JD', f'#OBSTYPE={obstype}',
            '#' + ','.join(FIELDS)]
    lines = []
    for r in sorted(rows, key=lambda r: (r['target'], r['jd'])):
        comps = json.loads(r.get('comps') or '[]') if isinstance(r.get('comps'), str) else (r.get('comps') or [])
        note = f"ensemble of {len(comps)} comps: {' '.join(comps[:15])}" if r.get('cname') == 'ENSEMBLE' else 'na'
        lines.append(','.join([_clean(r['target'], 30), f"{r['jd']:.5f}", f"{r['mag']:.3f}", f"{min(max(r['err'], 0.001), 0.999):.3f}",
                               r['filter'], 'YES' if r.get('trans') else 'NO', 'STD', _clean(r.get('cname'), 20), _f(r.get('cmag'), 3),
                               _clean(r.get('kname'), 20), _f(r.get('kmag'), 3), _f(r.get('airmass'), 4),
                               _clean(r.get('grp'), 5), _clean(r.get('chart'), 20), _clean(note)]))
    return '\n'.join(head + lines) + '\n'


def validate(text):
    """What a WebObs upload would reject or that looks wrong: [(line number, 'error'|'warning', words)]."""
    out, params, n = [], {}, 0
    for i, ln in enumerate(text.splitlines(), 1):
        if ln.startswith('#'):
            if '=' in ln:
                k, v = ln[1:].split('=', 1)
                params[k.strip().upper()] = v.strip()
            continue
        if not ln.strip():
            continue
        n += 1
        p = ln.split(params.get('DELIM', ','))
        if len(p) != len(FIELDS):
            out.append((i, 'error', f'{len(p)} fields instead of {len(FIELDS)}'))
            continue
        d = dict(zip(FIELDS, p))
        out += [(i, 'error', f'{k} is longer than {lim} characters') for k, lim in LIMITS.items() if len(d[k]) > lim]
        try:
            if not 2400000 < float(d['DATE']) < 2500000:
                out.append((i, 'error', 'DATE is not a Julian Date'))
        except ValueError:
            out.append((i, 'error', 'DATE is not a number'))
        try:
            m, e = float(d['MAG']), float(d['MERR'])
            if not -2 < m < 25:
                out.append((i, 'warning', f'magnitude {m} looks wrong'))
            if not 0 < e < 1:
                out.append((i, 'warning', f'uncertainty {e} looks wrong'))
        except ValueError:
            out.append((i, 'error', 'MAG or MERR is not a number'))
        if d['FILT'] not in AAVSO_FILTERS:
            out.append((i, 'error', f'{d["FILT"]} is not an AAVSO filter code'))
        if d['TRANS'] not in ('YES', 'NO') or d['MTYPE'] not in ('STD', 'DIF'):
            out.append((i, 'error', 'TRANS must be YES or NO and MTYPE STD or DIF'))
        if d['CNAME'] == 'ENSEMBLE' and d['CMAG'] != 'na':
            out.append((i, 'error', 'an ensemble needs CMAG=na'))
    head = []
    miss = {'TYPE', 'OBSCODE', 'SOFTWARE', 'DELIM', 'DATE', 'OBSTYPE'} - set(params)
    if miss:
        head.append((0, 'error', 'missing header line(s): ' + ', '.join(sorted(miss))))
    if params.get('TYPE', '').upper() != 'EXTENDED':
        head.append((0, 'error', 'TYPE must be EXTENDED'))
    if not params.get('OBSCODE'):
        head.append((0, 'error', 'no AAVSO observer code'))
    if not n:
        head.append((0, 'error', 'no observations'))
    return head + out


def plot(con, target, night, lon=0.0):
    """Light curve of one star in one night, all filters (rejected points left out)."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    rows = [r for r in db.rows(con, "SELECT jd, mag, err, filter FROM vs_meas WHERE target=? AND night=? AND status != 'rejected' ORDER BY jd",
                               (target, night))]
    if not rows:
        return None
    path = PLOTS / f'{slug(target)}-{night}.png'
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(8, 3.6), dpi=100)
    t0 = math.floor(rows[0]['jd'] - 0.5) + 0.5
    for filt in sorted({r['filter'] for r in rows}):
        rr = [r for r in rows if r['filter'] == filt]
        ax.errorbar([(r['jd'] - t0) * 24 for r in rr], [r['mag'] for r in rr], [r['err'] for r in rr], fmt='o', ms=3, label=filt)
    ax.invert_yaxis()
    ax.set_xlabel(f"UT hours on {datetime.fromtimestamp((t0 - 2440587.5) * 86400, timezone.utc):%Y-%m-%d}")
    ax.set_ylabel('magnitude')
    ax.set_title(f'{target}, night of {night}', fontsize=10)
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)
    return path


def ci_pair(s, active, code):
    """The colour pair (chart bands) whose comparison-star colour is kept with each measurement, for the transformation:
    the pair of the transformation in use for this filter, else the usual pair for it among the observatory's filters."""
    spec = (active or {}).get('spec', {}).get(code)
    if isinstance(spec, (list, tuple)) and len(spec) in (3, 4):
        return chart_band(s, spec[0]), chart_band(s, spec[1])
    have = {f.get('aavso') for f in s.get('filters') or []}
    for a, b in vscal.PAIRS.get(code, []):
        if a in have and b in have:
            return chart_band(s, a), chart_band(s, b)
    return None


def make_reports(con, s, log, obscode, now=None, nights=None, llm=None):
    """One AAVSO file per finished night, from that night's ready measurements not yet in a file, with what to check first."""
    now = now or time.time()
    lon = (s.get('site') or {}).get('lon') or 0.0
    tonight = night_of(now / 86400 + 2440587.5, lon)
    rows = db.rows(con, "SELECT * FROM vs_meas WHERE status='ok' AND report IS NULL ORDER BY jd")
    by = {}
    for r in rows:
        if r['night'] < tonight or (nights and r['night'] in nights):
            by.setdefault(r['night'], []).append(r)
    REPORTS.mkdir(parents=True, exist_ok=True)
    made, reports = [], db.kv_get(con, 'vs_reports') or []
    act = vscal.active(con, s)
    events = db.kv_get(con, 'vs_events') or []
    for nt, rs in sorted(by.items()):
        for tgt in {r['target'] for r in rs}:
            mine = [r for r in rs if r['target'] == tgt]
            transform(assign_groups(mine, float(s.get('group_gap_min', 20))), act.get('spec'))
        txt = extended(rs, obscode, s.get('obstype') or 'CCD')
        probs = validate(txt)
        k, name = 1, f'aavso-{nt}.txt'
        while (REPORTS / name).exists():
            k += 1
            name = f'aavso-{nt}-{k}.txt'
        (REPORTS / name).write_text(txt)
        for r in rs:
            con.execute("UPDATE vs_meas SET report=?, status='filed', grp=?, trans=?, mag=? WHERE id=?",
                        (name, r['grp'], r['trans'], r['mag'], r['id']))
        info = {'file': name, 'night': nt, 'rows': len(rs), 'stars': len({r['target'] for r in rs}),
                'errors': sum(1 for p in probs if p[1] == 'error'), 'warnings': sum(1 for p in probs if p[1] == 'warning'),
                'problems': [list(p) for p in probs[:20]], 'path': f'vstars/reports/{name}', 'made': now, 'uploaded': None,
                'transform': {k2: act.get(k2) for k2 in ('source', 'since', 'latest', 'sets')}}
        stars = {r['target'] for r in rs}
        checks = vsqc.report_checks(con, s, info, rs, act, [e for e in events if e['star'] in stars])
        draft = vsqc.summary_text(checks)
        info['checks'] = [list(c) for c in checks]
        info['summary'], info['summary_by'] = vsqc.polish(llm, '\n'.join(w for _lv, w in checks), draft,
                                                          'Rewrite this check-list for a variable-star observer in two or three plain sentences: '
                                                          'what is ready, and what to look at before uploading.')
        made.append(info)
        log(f'  AAVSO file for the night of {nt}: {len(rs)} measurements of {info["stars"]} stars; '
            + (f'{info["errors"]} format errors' if info['errors'] else 'format checked'))
    if made:
        db.kv_set(con, 'vs_reports', (made[::-1] + reports)[:200])
    return made


def remake(con, s, name, log=print, now=None):
    """Make a not-yet-uploaded file again (after a measurement in it was rejected or accepted later)."""
    reports = db.kv_get(con, 'vs_reports') or []
    rep = next((x for x in reports if x['file'] == name), None)
    if not rep or rep.get('uploaded'):
        return None
    con.execute("UPDATE vs_meas SET status='ok', report=NULL WHERE report=? AND status='filed'", (name,))
    (REPORTS / name).unlink(missing_ok=True)
    db.kv_set(con, 'vs_reports', [x for x in reports if x['file'] != name])
    obscode = (s.get('obscode') or config.load().get('aavso_code') or '').strip()
    return make_reports(con, s, log, obscode, now, nights={rep['night']})


# ------------------------------------------------------------------ the job
def known_targets(con):
    """Names and positions this observatory knows: tonight's plan and the VSX look-ups."""
    out = {}
    for nm, o in (db.kv_get(con, 'vs_vsx') or {}).items():
        out[nm.upper()] = dict(o, name=nm)
    for t in (db.kv_get(con, 'vs_plan') or {}).get('targets') or []:
        out[t['name'].upper()] = t
    return out


def which_star(hdr, known):
    nm = str(hdr.get('OBJECT') or '').strip()
    if nm.upper() in known:
        return known[nm.upper()]
    ra, dec = pointing(hdr)
    if ra is not None and known:
        sep = lambda t: math.hypot(((t['ra'] - ra + 180) % 360 - 180) * math.cos(math.radians(dec)), t['dec'] - dec)
        best = min(known.values(), key=sep)
        if sep(best) < 0.25:
            return best
    return {'name': nm} if nm else None


def frame_info(path):
    """What an image's header says, read without loading the image."""
    from astropy.io import fits
    with fits.open(str(path), memmap=False) as h:
        for hdu in h:
            if int(hdu.header.get('NAXIS') or 0) == 2:
                return hdu.header.copy()
        return h[0].header.copy()


def start_time(hdr):
    """Exposure start (unix seconds) from DATE-OBS, or None."""
    d = str(hdr.get('DATE-OBS') or '').strip()
    if not d:
        return None
    if 'T' not in d and hdr.get('TIME-OBS'):
        d = d + 'T' + str(hdr['TIME-OBS'])
    try:
        return datetime.fromisoformat(d.replace('Z', '')[:26]).replace(tzinfo=timezone.utc).timestamp()
    except ValueError:
        return None


def has_wcs(hdr):
    return 'RA' in str(hdr.get('CTYPE1', '')).upper() and hdr.get('CRVAL1') is not None


def gaia_stars(ra0, dec0, radius, gmax, gaia_fn=None):
    """Gaia stars around a pointing for the plate fit, kept on disk per pointing (a time series asks once, not per frame)."""
    from .exowatch import gaia_field
    if gaia_fn is not None:
        return gaia_fn(ra0, dec0, radius, gmax)
    GAIA.mkdir(parents=True, exist_ok=True)
    p = GAIA / f'field-{ra0:.2f}_{dec0:+.2f}_{radius:.3f}_{gmax:.1f}.json'
    if p.exists() and time.time() - p.stat().st_mtime < 60 * 86400:
        return json.loads(p.read_text())
    stars = gaia_field(ra0, dec0, radius, gmax)
    if stars:
        p.write_text(json.dumps(stars))
    return stars


def gaia_near(star, gmax, gaia_fn=None):
    """Gaia stars within one arcminute of a variable (for the blend check), kept 60 days; None when unavailable."""
    if gaia_fn is None and OFFLINE:
        return None
    from .exowatch import gaia_field
    GAIA.mkdir(parents=True, exist_ok=True)
    p = GAIA / f"near-{slug(star['name'])}.json"
    if gaia_fn is None and p.exists() and time.time() - p.stat().st_mtime < 60 * 86400:
        return json.loads(p.read_text())
    try:
        stars = (gaia_fn or gaia_field)(star['ra'], star['dec'], 1 / 60, gmax)
    except Exception:
        return None
    cosd = math.cos(math.radians(star['dec']))
    stars = [g for g in stars or [] if math.hypot(((g['ra'] - star['ra'] + 180) % 360 - 180) * cosd, g['dec'] - star['dec']) < 1 / 60]
    if gaia_fn is None:
        p.write_text(json.dumps(stars))
    return stars


def in_footprint(known, hdr, s, primary):
    """Other program stars that should be in this frame (from the pointing and the field size); None if the size is unknown."""
    ra0, dec0 = pointing(hdr)
    scale = pixel_scale(hdr, s)
    nx, ny = int(hdr.get('NAXIS1') or 0), int(hdr.get('NAXIS2') or 0)
    if ra0 is None or not scale or not nx or not ny:
        return []
    rad = 0.5 * math.hypot(nx, ny) * scale / 3600
    out = []
    for t in known.values():
        if t.get('ra') is None or t['name'].upper() == primary['name'].upper():
            continue
        if math.hypot(((t['ra'] - ra0 + 180) % 360 - 180) * math.cos(math.radians(dec0)), t['dec'] - dec0) < rad:
            out.append(t)
    return out


def prepare(con, s, path, known, ctx):
    """Read the header and gather what measuring the image needs (charts, positions): a job for analyse(), or a final
    (status, words) for images that are not measured."""
    hdr = frame_info(path)
    info = {'t0': start_time(hdr), 'exp': exptime(hdr), 'obj': str(hdr.get('OBJECT') or '').strip(), 'filt': str(hdr.get('FILTER') or '').strip()}
    kind = str(hdr.get('IMAGETYP') or hdr.get('FRAMETYP') or 'light').lower()
    if not any(k in kind for k in ('light', 'object', 'science')):
        return None, ('skipped', f'not a light frame ({kind})'), {}
    lon = (s.get('site') or {}).get('lon') or 0.0
    if info['t0']:
        info['night'] = night_of(info['t0'] / 86400 + 2440587.5, lon)
    f = filter_of(hdr, s)
    if not f:
        return None, ('skipped', f"filter {hdr.get('FILTER')!r} is not in the settings"), info
    code = f['aavso']
    jd = jd_mid(hdr)
    ra0, dec0 = pointing(hdr)
    job = {'path': str(path), 's': s, 'code': code, 'band': f.get('chart') or chart_band(s, code), 'jd': jd, 'info': info,
           'override': str(path) in ctx['override']}
    if not has_wcs(hdr) and ra0 is not None:
        scale = pixel_scale(hdr, s)
        n = max(int(hdr.get('NAXIS1') or 0), int(hdr.get('NAXIS2') or 0))
        radius = min(1.5, max(0.15, n * (scale or 1.5) / 3600 * 0.75))
        try:
            job['gaia_stars'] = gaia_stars(ra0, dec0, radius, float(s.get('faint_limit', 16)) + 1.0, ctx.get('gaia_fn'))
        except Exception:
            job['gaia_stars'] = []
    field = vscal.which_field(hdr, s, ra0, dec0)
    fov = math.hypot(int(hdr.get('NAXIS1') or 0), int(hdr.get('NAXIS2') or 0)) * (pixel_scale(hdr, s) or 1.5) / 60
    if 'camera' not in ctx and hdr.get('NAXIS1') and hdr.get('NAXIS2') and pixel_scale(hdr, s):
        ctx['camera'] = {'w': int(hdr['NAXIS1']), 'h': int(hdr['NAXIS2']), 'scale': float(pixel_scale(hdr, s))}   # for shared-field planning
    if field:
        try:
            job['std'] = {'field': field['name'], 'chart': vscal.std_chart(field, fov, float(s.get('faint_limit', 16)), ctx.get('vsp'))}
        except Exception as e:
            return None, ('failed', f'standard field {field["name"]}: AAVSO chart not available ({type(e).__name__})'), info
        return job, None, info
    star = which_star(hdr, known)
    if not star:
        return None, ('skipped', 'no OBJECT name and no known star at the pointing'), info
    if star.get('ra') is None:
        try:
            o = vsx_object(star['name'], (lambda n=star['name']: ctx['vsx'](n)) if ctx.get('vsx') else None)
        except Exception:
            o = None
        if not o:
            return None, ('failed', f"{star['name']}: not found in AAVSO VSX"), info
        cache = db.kv_get(con, 'vs_vsx') or {}
        cache[star['name']] = o
        db.kv_set(con, 'vs_vsx', cache)
        star = known[star['name'].upper()] = dict(o, name=star['name'])
    stars = [star] + (in_footprint(known, hdr, s, star) if s.get('measure_all_program_stars', True) else [])
    job['stars'] = []
    for k, st in enumerate(stars):
        try:
            ch = ctx['charts'].get(st['name']) or chart(st['name'], fov, float(s.get('faint_limit', 16)) + 1.0, ctx.get('vsp'))
        except Exception as e:
            if k == 0:
                return None, ('failed', f'AAVSO chart not available ({type(e).__name__})'), info
            continue
        ctx['charts'][st['name']] = ch
        if ctx.get('catalog') and ch.get('chartid') not in ctx['checked']:
            ctx['checked'].add(ch.get('chartid'))
            for fl in vsqc.catalog_check(con, ch, st['name'], ctx['catalog'] if callable(ctx['catalog']) else None):
                ctx['flags'].append(fl)
                if fl.get('excluded'):
                    ctx['excluded'][fl['auid']] = fl['why']
        near = ctx['near'].get(st['name'])
        if st['name'] not in ctx['near']:
            near = ctx['near'][st['name']] = gaia_near(st, float(s.get('faint_limit', 16)) + 4, ctx.get('gaia_fn'))
        job['stars'].append({'star': {k2: st.get(k2) for k2 in ('name', 'ra', 'dec', 'type', 'max_mag', 'min_mag', 'mag_plan')},
                             'chart': without(ch, ctx['excluded']), 'cpair': ci_pair(s, ctx['active'], code), 'near': near, 'primary': k == 0})
    key = (star['name'], code)
    if key not in ctx.setdefault('refs', {}):
        ctx['refs'][key] = vsqc.reference(con, star['name'], code)
    job['ref'] = ctx['refs'][key]
    return job, None, info


def analyse(job):
    """One image measured, without the database (runs in a worker process): the frame check, then the star(s) or the
    standard stars. Returns a plain dict."""
    s, path = job['s'], job['path']
    try:
        img, hdr = read(path)
        img, done = calibrate(img, hdr, masters(s.get('calib_dir')))
        sky = vsqc.extract(img, hdr, s)
        qc = vsqc.metrics(sky, hdr, s)
        sky['streaks'] = qc['streaks']
        verdict, reasons = ('ok', []) if job.get('override') else vsqc.screen(qc, job.get('ref'), s)
        qc.update(verdict=verdict, reasons=reasons)
        out = {'path': path, 'qc': qc, 'done': done, 'jd': job['jd'], 'code': job['code'], 'info': job['info']}
        if verdict == 'bad':
            return dict(out, status='rejected', note='frame check: ' + '; '.join(reasons))
        stars = job.get('gaia_stars')
        gfn = (lambda *_a: stars) if stars is not None else job.get('gaia_fn')
        to_pix, how = sky_to_pixels(sky['data'], hdr, s, gfn)
        if not to_pix:
            return dict(out, status='failed', note=how)
        out['how'] = how
        if verdict == 'borderline' and s.get('qc_ai', True):
            try:
                out['thumb'] = str(vsqc.thumbnail(sky['data'], vsqc.THUMBS / (slug(Path(path).stem) + '.png')))
            except Exception:
                pass
        if job.get('std'):
            got = vscal.measure_field(sky, hdr, to_pix, job['std']['chart'], s)
            return dict(out, status='standard', std={'field': job['std']['field'], 'stars': got, 'chart': job['std']['chart'].get('chartid')},
                        note=f"standard field {job['std']['field']}: {len(got)} standard stars measured ({how})")
        meas = []
        for st in job['stars']:
            r = measure(img, hdr, to_pix, st['star'], st['chart'], job['band'], s, st.get('cpair'), sky=sky, near=st.get('near'))
            if r.get('error') and not st.get('primary'):
                continue
            meas.append({'star': st['star'], 'chart': st['chart']['chartid'], 'res': r, 'primary': st.get('primary')})
        return dict(out, meas=meas)
    except Exception as e:
        return {'path': path, 'status': 'failed', 'note': f'{type(e).__name__}: {str(e)[:160]}', 'info': job.get('info') or {}}


def store(con, s, res, ai=None):
    """Write one analysed image to the database: (status, words, [new measurement rows]). ai: the local vision model's opinion
    of a borderline frame (asked before, outside the database transaction)."""
    site = s.get('site') or {}
    if res.get('status') in ('failed', 'rejected'):
        return res['status'], res['note'], []
    if res.get('status') == 'standard':
        st = res['std']
        am = None
        if site.get('lat') is not None:
            f = next((x for x in vscal.fields(s) if x['name'] == st['field']), None)
            am = airmass(site, f['ra'], f['dec'], res['jd']) if f else None
        con.execute('INSERT OR REPLACE INTO vs_std (frame, field, night, filter, jd, airmass, stars, chart, at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (res['path'], st['field'], night_of(res['jd'], site.get('lon') or 0.0), res['code'], res['jd'], am, json.dumps(st['stars']),
                     st['chart'], time.time()))
        return 'standard', res['note'], []
    meas = res.get('meas') or []
    prim = next((m for m in meas if m.get('primary')), None)
    if not prim or prim['res'].get('error'):
        return 'failed', (prim or {}).get('res', {}).get('error') or 'nothing measured', []
    have = {r['target'] for r in db.rows(con, 'SELECT target FROM vs_meas WHERE frame=?', (res['path'],))}
    meas = [m for m in meas if m['star']['name'] not in have]      # measured before (in a file, or rejected): never twice
    if not meas:
        return 'skipped', 'already measured (in an AAVSO file, or rejected)', []
    qc = res['qc']
    extra_flags, extra_notes = [], []
    if qc.get('verdict') == 'borderline':
        if ai:
            qc['ai'] = ai
        if ai and ai['verdict'] != 'good' and ai['confidence'] >= float(s.get('qc_ai_confidence', 0.6)):
            extra_flags.append(f"AI frame check: {ai['verdict'].replace('_', ' ')} ({ai['confidence']:.0%}): {ai['why']}")
        elif ai:
            extra_notes.append(f"frame check: {'; '.join(qc['reasons'])} (the local AI looked: {ai['verdict'].replace('_', ' ')})")
        else:
            extra_notes.append('frame check: ' + '; '.join(qc['reasons']))
    new, words, statuses = [], [], []
    for m in meas:
        r, star = m['res'], m['star']
        if r.get('error'):
            continue
        am = airmass(site, star['ra'], star['dec'], res['jd']) if site.get('lat') is not None else None
        flags, notes = list(r['flags']) + extra_flags, list(r.get('notes') or []) + extra_notes
        if am and am > float(s.get('airmass_max', 2.5)):
            flags.append(f'high airmass ({am:.2f})')
        if s.get('calib_dir') and not res.get('done'):
            flags.append('not calibrated (no matching calibration frames)')
        status = 'review' if flags or s.get('review_all') else 'ok'
        night = night_of(res['jd'], site.get('lon') or 0.0)
        cur = con.execute('INSERT INTO vs_meas (frame, target, filter, jd, mag, mag_raw, err, snr, cname, cmag, kname, kmag, kdev, ncomp, comps, '
                          'airmass, chart, flags, status, ci_comp, night, at, zp, comp_res, notes, qc) '
                          'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (res['path'], star['name'], res['code'], res['jd'], r['mag'], r['mag'], r['err'], r['snr'], r['cname'], r['cmag'],
                           r['kname'], r['kmag'], r['kdev'], r['ncomp'], json.dumps(r['comps']), am, m['chart'], json.dumps(flags), status,
                           r.get('ci_comp'), night, time.time(), r.get('zp'), json.dumps(r.get('comp_res') or {}), json.dumps(notes),
                           qc.get('verdict')))
        new.append({'id': cur.lastrowid, 'target': star['name'], 'filter': res['code'], 'night': night, 'mag': r['mag'], 'kdev': r['kdev'],
                    'flags': flags, 'jd': res['jd']})
        statuses.append(status)
        words.append(f"{star['name']} {res['code']} = {r['mag']:.3f} ± {r['err']:.3f} ({res.get('how')}; {r['ncomp']} comparison stars"
                     + (f"; check star {r['kdev']:+.3f}" if r['kdev'] is not None else '') + ')' + (': ' + '; '.join(flags) if flags else ''))
    status = 'review' if 'review' in statuses else 'ok'
    return status, ' | '.join(words), new


def process(con, s, path, known, vsp=None, gaia_fn=None, vsx=None):
    """One image -> its measurement(s) or the reason why not: (status, words, row id or None). (One at a time; run() uses
    prepare / analyse / store directly so many images can be measured in parallel.)"""
    ctx = {'charts': {}, 'near': {}, 'excluded': vsqc.excluded(con), 'override': set(), 'vsp': vsp, 'vsx': vsx, 'gaia_fn': gaia_fn,
           'active': vscal.active(con, s), 'checked': set(), 'flags': [], 'catalog': None}
    job, final, _info = prepare(con, s, path, known, ctx)
    if final:
        return final[0], final[1], None
    job['gaia_fn'] = gaia_fn
    st, words, new = store(con, s, analyse(job))
    return st, words, (new[0]['id'] if new else None)


def _workers(s, n):
    w = int(s.get('workers') or 0)
    if w <= 0:
        w = max(1, min(6, (os.cpu_count() or 2) - 4))
    return max(1, min(w, n))


def analyse_all(jobs, s, log, deadline):
    """analyse() every job, in parallel worker processes when there are enough images; yields (job, result)."""
    w = _workers(s, len(jobs))
    if w == 1 or len(jobs) < 8 or any(j.get('gaia_fn') is not None for j in jobs):
        for j in jobs:
            if time.time() > deadline:
                log('  out of time; the rest next hour')
                return
            yield j, analyse(j)
        return
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed
    log(f'  measuring {len(jobs)} images, {w} at a time')
    with ProcessPoolExecutor(max_workers=w, mp_context=mp.get_context('spawn')) as ex:
        pending = list(jobs)
        running = {}
        while pending or running:
            while pending and len(running) < 2 * w and time.time() < deadline:
                j = pending.pop(0)
                running[ex.submit(analyse, j)] = j
            if not running:
                if pending:
                    log('  out of time; the rest next hour')
                break
            done = next(as_completed(running))
            j = running.pop(done)
            try:
                yield j, done.result()
            except Exception as e:
                yield j, {'path': j['path'], 'status': 'failed', 'note': f'{type(e).__name__}: {str(e)[:160]}', 'info': j.get('info') or {}}


def finish_nights(con, s, cfg, log, now):
    """Nights that are over: zero points and clouds, overheads, standard-field coefficients (once per night)."""
    lon = (s.get('site') or {}).get('lon') or 0.0
    tonight = night_of(now / 86400 + 2440587.5, lon)
    done = db.kv_get(con, 'vs_nights_done') or {}
    if isinstance(done, list):
        done = {}
    count = {}
    for r in db.rows(con, 'SELECT night, COUNT(*) AS n FROM vs_meas WHERE night < ? GROUP BY night', (tonight,)):
        count[r['night']] = r['n']
    for r in db.rows(con, 'SELECT night, COUNT(*) AS n FROM vs_std WHERE night < ? GROUP BY night', (tonight,)):
        count[r['night']] = count.get(r['night'], 0) + 1000 * r['n']
    held = []
    for nt in sorted(n for n, c in count.items() if n and done.get(n) != c):
        q = vsqc.night_quality(con, nt, s)
        if q['bands']:
            log(f"  night of {nt}: {'photometric' if q['photometric'] else 'not photometric'}"
                + ''.join(f"; {b} extinction {v['k']:.2f}" for b, v in q['bands'].items() if v.get('k') is not None)
                + (f"; {q['cloud']} frame(s) lost light to cloud" if q['cloud'] else ''))
        o = vsqc.save_overheads(con, nt)
        if o and o.get('slews'):
            log(f"  night of {nt}: shutter open {o['open']:.0%} of {o['hours']} h; {o['slews']} slews, median {o['slew_s']} s")
        sets = vscal.night_sets(con, nt, s)
        if sets:
            held += vscal.record(con, s, sets)
            for st in sets:
                log(f"  standard field {st['field']} ({nt}): {len(st['coefs'])} coefficients from {st['stars']} stars ({st['state']})")
        done[nt] = count[nt]
    db.kv_set(con, 'vs_nights_done', dict(sorted(done.items())[-120:]))
    for st in held:
        db.add_task(con, f"vs:transform:{st['night']}:{st['field']}", 'vstars', 'transform',
                    f"Transformation coefficients changed ({st['field']}, night of {st['night']})",
                    'They disagree with the ones in use: new filters, camera or optics? Accept or reject them on the Variable star observatory tab.',
                    {'tab': 'vstars'}, 2)
    return held


def run(ctx, vsp=None, gaia_fn=None, vsx=None, now=None, vizier=None, aid=None, llm=None, sink=None):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    s = settings(cfg)
    if not s.get('enabled'):
        return 'variable-star observatory not set up (see VARIABLE_STARS.md)'
    if not s.get('images_dir'):
        return 'variable-star observatory: no image folder in settings (vstars.images_dir)'
    ensure(con)
    now = now or time.time()
    if llm is None and not OFFLINE and s.get('ai', True):
        from .llm import LLM
        llm = LLM(cfg, log)
    # 1. comparison stars that went bad since the last run: out of the ensemble, their measurements done again, a CHET draft
    flags = vsqc.comp_health(con, s, now)
    gone = [f['auid'] for f in flags if f.get('excluded')]
    if gone:
        n = vsqc.requeue_for(con, gone)
        log(f"  comparison star(s) {', '.join(gone)} left out from now on; {n} image(s) to measure again")
    known = known_targets(con)
    seen = {r['path']: r['mtime'] for r in db.rows(con, 'SELECT path, mtime FROM vs_frames')}
    files = fits_files(s['images_dir'], now - float(s.get('lookback_days', 30)) * 86400)
    new = [p for p in files if seen.get(str(p)) != p.stat().st_mtime and now - p.stat().st_mtime > 20]   # skip files still being written
    override = set(db.kv_get(con, 'vs_qc_override') or [])
    pctx = {'charts': {}, 'near': {}, 'excluded': vsqc.excluded(con), 'override': override, 'vsp': vsp, 'vsx': vsx, 'gaia_fn': gaia_fn,
            'active': vscal.active(con, s), 'checked': set(), 'flags': list(flags),
            'catalog': vizier if vizier is not None else (None if OFFLINE or not s.get('catalog_check', True) else True)}
    got, touched, new_rows, jobs = {}, set(), [], []
    stop = time.time() + float(s.get('time_budget_min', 50)) * 60          # a backlog is worked through an hour at a time
    deadline = min(stop, time.time() + max(0.0, ctx.time_left() - 90))

    def frame_row(p, st, note, info, target=None, filt=None, jd=None, qc=None):
        con.execute('INSERT OR REPLACE INTO vs_frames (path, mtime, target, filter, jd, status, note, at, t0, exp, obj, filt, qc, night) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                    (str(p), Path(p).stat().st_mtime, target, filt, jd, st, note, time.time(), (info or {}).get('t0'), (info or {}).get('exp'),
                     (info or {}).get('obj'), (info or {}).get('filt'), json.dumps(qc) if qc else None, (info or {}).get('night')))
        got[st] = got.get(st, 0) + 1
        if st != 'skipped':
            log(f'  {Path(p).name}: {st}: {note}')
    for p in new:
        if time.time() > deadline:
            break
        try:
            job, final, info = prepare(con, s, p, known, pctx)
        except Exception as e:
            job, final, info = None, ('failed', f'{type(e).__name__}: {str(e)[:160]}'), {}
        if final:
            frame_row(p, final[0], final[1], info)
            continue
        if gaia_fn is not None:
            job['gaia_fn'] = gaia_fn
        jobs.append(job)
    ai_left = int(s.get('qc_ai_max', 30))
    for job, res in analyse_all(jobs, s, log, deadline):
        ai = None
        if (res.get('qc') or {}).get('verdict') == 'borderline' and llm and res.get('thumb') and ai_left > 0 and res.get('status') is None:
            ai_left -= 1                                  # the vision model can take seconds: asked before the transaction opens
            ai = vsqc.ask_vision(llm, res['thumb'], res['qc']['reasons'])
        con.execute('BEGIN')                              # one short transaction per image: the database is never held while measuring
        try:
            con.execute('SAVEPOINT img')
            try:
                st, note, rows = store(con, s, res, ai)
                con.execute('RELEASE img')
            except Exception as e:
                con.execute('ROLLBACK TO img')            # nothing half-written for this image
                con.execute('RELEASE img')
                st, note, rows = 'failed', f'{type(e).__name__}: {str(e)[:160]}', []
            prim = rows[0] if rows else {}
            frame_row(job['path'], st, note, res.get('info') or job.get('info'),
                      prim.get('target') or ((job.get('stars') or [{}])[0].get('star') or {}).get('name'), job['code'], job['jd'], res.get('qc'))
            con.execute('COMMIT')
        except Exception:
            con.execute('ROLLBACK')
            raise
        new_rows += rows
        for r in rows:
            touched.add((r['target'], r['night']))
    if override:
        db.kv_set(con, 'vs_qc_override', sorted(override - {j['path'] for j in jobs}))
    if pctx.get('camera'):
        db.kv_set(con, 'vs_camera', pctx['camera'])
    for fl in pctx['flags']:
        if fl.get('kind') == 'catalog' and fl.get('excluded'):
            n = vsqc.requeue_for(con, [fl['auid']])
            if n:
                log(f"  {fl['auid']} is a known variable ({fl['why']}): {n} image(s) to measure again")
        vsqc.save_chet(con, fl, pctx['charts'].get(fl.get('star')) or None)
    for tgt, nt in sorted(touched):
        try:
            plot(con, tgt, nt)
        except Exception as e:
            log(f'  light curve of {tgt} failed: {e}')
    events = vsnews.outbursts(con, s, cfg, new_rows, known, aid if aid is not None else (False if OFFLINE else None), now, sink, log) \
        if new_rows else []
    finish_nights(con, s, cfg, log, now)
    obscode = (s.get('obscode') or cfg.get('aavso_code') or '').strip()
    made = make_reports(con, s, log, obscode, now, llm=llm) if obscode else []
    if not obscode:
        log('  no AAVSO observer code in settings yet: the report files wait for it')
    review = con.execute("SELECT COUNT(*) FROM vs_meas WHERE status='review'").fetchone()[0]
    if review:
        db.add_task(con, 'vs:review', 'vstars', 'review', f'{review} variable-star measurements to look at',
                    'They have a warning (check star, signal, saturation...). Accept or reject them on the Variable star observatory tab.',
                    {'tab': 'vstars'}, 2, reopen=True)
    else:
        db.close_task(con, 'vs:review')
    for m in made:
        db.add_task(con, f'vs:upload:{m["file"]}', 'vstars', 'report', f'Upload the AAVSO file for the night of {m["night"]}',
                    f'{m["rows"]} measurements of {m["stars"]} stars. {m.get("summary") or ""} Download it from the Variable star observatory tab '
                    'and upload it at AAVSO WebObs (nothing is sent from here).', {'tab': 'vstars', 'url': WEBOBS}, 2)
    for fl in pctx['flags']:
        db.add_task(con, f"vs:chet:{fl['auid']}:{fl['band']}", 'vstars', 'comp', f"Comparison star {fl['auid']}: {fl['kind']}",
                    f"{fl['why']}. A report for the AAVSO sequence team (CHET) is drafted on the Variable star observatory tab; read it and send "
                    'it yourself if you agree.', {'tab': 'vstars'}, 3)
    n_new = sum(got.values())
    rest = ''.join(f', {got[k]} {w}' for k, w in (('rejected', 'set aside by the frame check'), ('standard', 'standard-field frames')) if got.get(k))
    return (f"{n_new} new images: {got.get('ok', 0)} measured and ready, {got.get('review', 0)} to look at, "
            f"{got.get('failed', 0)} failed, {got.get('skipped', 0)} skipped" + rest
            + (f'; {len(events)} outburst/fade alert(s)' if events else '') + (f'; {len(made)} AAVSO file(s) made' if made else ''))
