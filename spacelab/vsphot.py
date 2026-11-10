"""Variable-star observatory, part 2: from the observatory's images to an AAVSO report file (see VARIABLE_STARS.md).

Every hour, for each new image in the observatory's image folder:
- which star (the FITS OBJECT name, else the pointing), which filter (mapped to an AAVSO filter code in settings),
  the mid-exposure time and the airmass;
- calibration with master bias, dark and flat frames when a calibration folder is set;
- where the stars are: the image's own plate solution (WCS) when it has one, otherwise a fit to Gaia stars;
- comparison stars from the AAVSO Variable Star Plotter (the chart for that star, kept for 30 days);
- aperture photometry of the variable and every comparison star; the variable's magnitude from the ensemble of
  comparison stars, with the comparison star closest in brightness kept aside as the check star;
- checks: saturation, signal-to-noise, the check star against its chart value, too few comparison stars, high airmass.
Measurements without a problem are ready; the others wait for the observer in Meridian. When a night is over, its ready
measurements become one file in the AAVSO Extended File Format: a filter sequence shares one GROUP number, colour
transformation is applied when the observer has given coefficients, and the file is checked against the format's
rules. The observer uploads it at WebObs: nothing is ever sent to the AAVSO from here.
"""
import json
import math
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import config, db, net
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
PLOTS = config.PLOTS / 'vstars'


def ensure(con):
    con.execute('CREATE TABLE IF NOT EXISTS vs_frames (path TEXT PRIMARY KEY, mtime REAL, target TEXT, filter TEXT, jd REAL, '
                'status TEXT, note TEXT, at REAL)')
    con.execute('CREATE TABLE IF NOT EXISTS vs_meas (id INTEGER PRIMARY KEY, frame TEXT, target TEXT, filter TEXT, jd REAL, '
                'mag REAL, mag_raw REAL, err REAL, snr REAL, trans INTEGER DEFAULT 0, cname TEXT, cmag REAL, kname TEXT, kmag REAL, '
                'kdev REAL, ncomp INTEGER, comps TEXT, airmass REAL, grp INTEGER, chart TEXT, flags TEXT, status TEXT, report TEXT, '
                'ci_comp REAL, night TEXT, at REAL)')


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


def masters(calib_dir):
    """Master bias, dark (per second) and flats (per filter, normalised) from a folder of calibration frames."""
    key = str(calib_dir or '')
    if key in _MASTERS:
        return _MASTERS[key]
    out = {'bias': None, 'dark': None, 'flat': {}}
    kinds = {'bias': [], 'dark': [], 'flat': {}}
    for p in (fits_files(calib_dir) if calib_dir else [])[:600]:
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
def chart(name, fov_arcmin, maglimit, fetch=None, max_age_days=30):
    """The AAVSO chart for this star: {'chartid', 'comps': [{auid, label, ra, dec, mags: {band: [mag, err]}}]}, kept 30 days."""
    CHARTS.mkdir(parents=True, exist_ok=True)
    p = CHARTS / (slug(name) + '.json')
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        return json.loads(p.read_text())
    fov = next((f for f in FOVS if f >= fov_arcmin), FOVS[-1])
    params = {'star': name, 'fov': fov, 'maglimit': round(float(maglimit), 1), 'format': 'json'}
    js = (fetch or (lambda q: net.fetch_json(VSP_API, params=q, timeout=90)))(params)
    comps = []
    for c in js.get('photometry') or []:
        mags = {str(b.get('band')): [float(b['mag']), float(b.get('error') or 0.0)] for b in c.get('bands') or [] if b.get('mag') is not None}
        r, d = angle(c.get('ra'), hours=True), angle(c.get('dec'))
        if r is not None and d is not None and mags:
            comps.append({'auid': str(c.get('auid') or ''), 'label': str(c.get('label') or ''), 'ra': r, 'dec': d, 'mags': mags})
    out = {'chartid': str(js.get('chartid') or 'na'), 'comps': comps, 'fov': fov}
    if comps:
        p.write_text(json.dumps(out))
    return out


# ------------------------------------------------------------------ photometry
def measure(img, hdr, to_pix, star, ch, band, s, cpair=None):
    """The variable's standard magnitude in `band` from the ensemble of chart comparison stars, or {'error': words}."""
    import sep
    data = np.ascontiguousarray(img, dtype=np.float64)
    data[~np.isfinite(data)] = np.nanmedian(data)
    h, w = data.shape
    bkg = sep.Background(data)
    sub = data - bkg.back()
    sat = float(hdr.get('SATURATE') or s.get('saturation_adu', 60000)) * 0.95
    objs = sep.extract(sub, 10, err=bkg.globalrms, minarea=6)
    good = objs[(objs['flag'] == 0) & (objs['peak'] < sat * 0.8)] if len(objs) else objs
    fwhm = float(np.median(2.3548 * np.sqrt((good['a'] ** 2 + good['b'] ** 2) / 2))) if len(good) >= 3 else 4.0
    fwhm = min(max(fwhm, 1.5), 20.0)
    r = min(max(float(s.get('aperture_fwhm', 1.6)) * fwhm, 2.0), 30.0)
    exp = exptime(hdr) or 1.0
    gain = float(hdr.get('EGAIN') or hdr.get('GAIN_E') or s.get('gain_e_adu') or 1.0)
    pts = [(None, star['ra'], star['dec'])] + [(c, c['ra'], c['dec']) for c in ch['comps']]
    x, y = to_pix(np.array([p[1] for p in pts]), np.array([p[2] for p in pts]))
    x, y = np.asarray(x, float), np.asarray(y, float)
    edge = 5 * r + 3
    inside = np.isfinite(x) & np.isfinite(y) & (x > edge) & (x < w - edge) & (y > edge) & (y < h - edge)
    if not inside[0]:
        return {'error': 'the variable is outside the image or too close to its edge'}
    idx = np.flatnonzero(inside)
    xw, yw, _f = sep.winpos(sub, x[idx], y[idx], fwhm / 2.3548)
    near = np.hypot(xw - x[idx], yw - y[idx]) < max(4.0, 1.5 * fwhm)      # centre on the star only if it is where it should be
    xs, ys = np.where(near, xw, x[idx]), np.where(near, yw, y[idx])
    flux, ferr, _ = sep.sum_circle(data, xs, ys, r, err=bkg.rms(), gain=gain, bkgann=(3 * r, 5 * r))
    got = {}
    for k, i in enumerate(idx):
        if not flux[k] > 0:
            continue
        yy, xx, rr = int(round(ys[k])), int(round(xs[k])), int(math.ceil(r))
        peak = float(data[max(0, yy - rr):yy + rr + 1, max(0, xx - rr):xx + rr + 1].max())
        got[i] = {'m': -2.5 * math.log10(flux[k] / exp), 'e': 1.0857 * ferr[k] / flux[k], 'snr': float(flux[k] / max(ferr[k], 1e-9)),
                  'sat': peak >= sat, 'x': float(xs[k]), 'y': float(ys[k])}
    t = got.get(0)
    if not t:
        return {'error': 'no light measured at the variable'}
    comps = []
    for i, o in got.items():
        c = pts[i][0]
        if i == 0 or band not in c['mags'] or o['sat'] or o['snr'] < float(s.get('snr_comp', 50)):
            continue
        if math.hypot(o['x'] - t['x'], o['y'] - t['y']) < 3 * r:          # blended with the variable
            continue
        comps.append(dict(o, c=c, cat=c['mags'][band]))
    if not comps:
        return {'error': f'no usable comparison star with a {band} magnitude on AAVSO chart {ch["chartid"]}'}
    flags = []
    if t['sat']:
        flags.append('variable saturated')
    if t['snr'] < float(s.get('snr_min', 20)):
        flags.append(f"low signal-to-noise ({t['snr']:.0f})")
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
    left = [c['c']['auid'] or c['c']['label'] for c, k in zip(comps, keep) if not k]
    ident = lambda c: c['c']['auid'] or c['c']['label']
    res = {'mag': t['m'] + zp0, 'err': math.hypot(t['e'], zerr), 'snr': t['snr'], 'fwhm': fwhm, 'ncomp': len(used),
           'comps': [ident(c) for c in used], 'left_out': left, 'flags': flags, 'cname': 'ENSEMBLE', 'cmag': None, 'kname': None, 'kmag': None, 'kdev': None}
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
    """Colour transformation with the observer's coefficients, e.g. {'V': ['B', 'V', 0.031]}: within one group,
    V = v + 0.031 x ((B-V) of the variable - (B-V) of the comparison stars). Those rows get TRANS=YES."""
    for r in rows:
        r['mag'], r['trans'] = r['mag_raw'], 0
    by = {}
    for r in rows:
        by.setdefault(r['grp'], {})[r['filter']] = r
    for fs in by.values():
        for filt, spec in (coefs or {}).items():
            r = fs.get(filt)
            if not r or not isinstance(spec, (list, tuple)) or len(spec) != 3:
                continue
            a, b = fs.get(spec[0]), fs.get(spec[1])
            if a is None or b is None or r.get('ci_comp') is None:
                continue
            r['mag'] = r['mag_raw'] + float(spec[2]) * ((a['mag_raw'] - b['mag_raw']) - r['ci_comp'])
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


def make_reports(con, s, log, obscode, now=None, nights=None):
    """One AAVSO file per finished night, from that night's ready measurements not yet in a file."""
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
    for nt, rs in sorted(by.items()):
        for tgt in {r['target'] for r in rs}:
            mine = [r for r in rs if r['target'] == tgt]
            transform(assign_groups(mine, float(s.get('group_gap_min', 20))), s.get('transform'))
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
                'problems': [list(p) for p in probs[:20]], 'path': f'vstars/reports/{name}', 'made': now, 'uploaded': None}
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


def process(con, s, path, known, vsp=None, gaia_fn=None, vsx=None):
    """One image -> one measurement (or the reason why not): (status, words, row id or None)."""
    img, hdr = read(path)
    kind = str(hdr.get('IMAGETYP') or hdr.get('FRAMETYP') or 'light').lower()
    if not any(k in kind for k in ('light', 'object', 'science')):
        return 'skipped', f'not a light frame ({kind})', None
    f = filter_of(hdr, s)
    if not f:
        return 'skipped', f"filter {hdr.get('FILTER')!r} is not in the settings", None
    star = which_star(hdr, known)
    if not star:
        return 'skipped', 'no OBJECT name and no known star at the pointing', None
    if star.get('ra') is None:
        try:
            o = vsx_object(star['name'], (lambda n=star['name']: vsx(n)) if vsx else None)
        except Exception:
            o = None
        if not o:
            return 'failed', f"{star['name']}: not found in AAVSO VSX", None
        cache = db.kv_get(con, 'vs_vsx') or {}
        cache[star['name']] = o
        db.kv_set(con, 'vs_vsx', cache)
        star = known[star['name'].upper()] = dict(o, name=star['name'])
    jd = jd_mid(hdr)
    img, done = calibrate(img, hdr, masters(s.get('calib_dir')))
    to_pix, how = sky_to_pixels(img, hdr, s, gaia_fn)
    if not to_pix:
        return 'failed', how, None
    fov = math.hypot(*img.shape) * (pixel_scale(hdr, s) or 1.5) / 60          # the image's diagonal, arcmin
    try:
        ch = chart(star['name'], fov, float(s.get('faint_limit', 16)) + 1.0, vsp)
    except Exception as e:
        return 'failed', f'AAVSO chart not available ({type(e).__name__})', None
    code = f['aavso']
    spec = (s.get('transform') or {}).get(code)
    cpair = (chart_band(s, spec[0]), chart_band(s, spec[1])) if isinstance(spec, (list, tuple)) and len(spec) == 3 else None
    res = measure(img, hdr, to_pix, star, ch, f.get('chart') or chart_band(s, code), s, cpair)
    if res.get('error'):
        return 'failed', res['error'], None
    site = s.get('site') or {}
    am = airmass(site, star['ra'], star['dec'], jd) if site.get('lat') is not None else None
    if am and am > float(s.get('airmass_max', 2.5)):
        res['flags'].append(f'high airmass ({am:.2f})')
    if s.get('calib_dir') and not done:
        res['flags'].append('not calibrated (no matching calibration frames)')
    status = 'review' if res['flags'] or s.get('review_all') else 'ok'
    cur = con.execute('INSERT INTO vs_meas (frame, target, filter, jd, mag, mag_raw, err, snr, cname, cmag, kname, kmag, kdev, ncomp, comps, '
                      'airmass, chart, flags, status, ci_comp, night, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                      (str(path), star['name'], code, jd, res['mag'], res['mag'], res['err'], res['snr'], res['cname'], res['cmag'],
                       res['kname'], res['kmag'], res['kdev'], res['ncomp'], json.dumps(res['comps']), am, ch['chartid'],
                       json.dumps(res['flags']), status, res.get('ci_comp'), night_of(jd, site.get('lon') or 0.0), time.time()))
    words = (f"{star['name']} {code} = {res['mag']:.3f} ± {res['err']:.3f} ({how}; {res['ncomp']} comparison stars"
             + (f"; check star {res['kdev']:+.3f}" if res['kdev'] is not None else '') + ')')
    return status, words + (': ' + '; '.join(res['flags']) if res['flags'] else ''), cur.lastrowid


def run(ctx, vsp=None, gaia_fn=None, vsx=None, now=None):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    s = settings(cfg)
    if not s.get('enabled'):
        return 'variable-star observatory not set up (see VARIABLE_STARS.md)'
    if not s.get('images_dir'):
        return 'variable-star observatory: no image folder in settings (vstars.images_dir)'
    ensure(con)
    now = now or time.time()
    known = known_targets(con)
    seen = {r['path']: r['mtime'] for r in db.rows(con, 'SELECT path, mtime FROM vs_frames')}
    files = fits_files(s['images_dir'], now - float(s.get('lookback_days', 30)) * 86400)
    new = [p for p in files if seen.get(str(p)) != p.stat().st_mtime and now - p.stat().st_mtime > 20]   # skip files still being written
    got, touched = {}, set()
    stop = time.time() + float(s.get('time_budget_min', 50)) * 60          # a backlog is worked through an hour at a time
    for p in new:
        if ctx.time_left() < 60 or time.time() > stop:
            log('  out of time; the rest next hour')
            break
        try:
            st, note, rid = process(con, s, p, known, vsp, gaia_fn, vsx)
        except Exception as e:
            st, note, rid = 'failed', f'{type(e).__name__}: {str(e)[:160]}', None
        row = db.one(con, 'SELECT target, filter, jd, night FROM vs_meas WHERE id=?', (rid,)) if rid else {}
        con.execute('INSERT OR REPLACE INTO vs_frames VALUES (?,?,?,?,?,?,?,?)',
                    (str(p), p.stat().st_mtime, row.get('target'), row.get('filter'), row.get('jd'), st, note, time.time()))
        got[st] = got.get(st, 0) + 1
        if row:
            touched.add((row['target'], row['night']))
        if st != 'skipped':
            log(f'  {p.name}: {st}: {note}')
    for tgt, nt in sorted(touched):
        try:
            plot(con, tgt, nt)
        except Exception as e:
            log(f'  light curve of {tgt} failed: {e}')
    obscode = (s.get('obscode') or cfg.get('aavso_code') or '').strip()
    made = make_reports(con, s, log, obscode, now) if obscode else []
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
                    f'{m["rows"]} measurements of {m["stars"]} stars. Download it from the Variable star observatory tab and upload it at '
                    'AAVSO WebObs (nothing is sent from here).', {'tab': 'vstars', 'url': WEBOBS}, 2)
    return (f"{len(new)} new images: {got.get('ok', 0)} measured and ready, {got.get('review', 0)} to look at, "
            f"{got.get('failed', 0)} failed, {got.get('skipped', 0)} skipped" + (f'; {len(made)} AAVSO file(s) made' if made else ''))
