"""NASA Exoplanet Watch pipeline.

You check out a MicroObservatory dataset from Exoplanet Watch (email checkout) and drop the zip into
inbox/exoplanet-watch/. This job unpacks it, works out which planet it is, finds the target and comparison stars
itself (Gaia + pattern matching), writes EXOTIC's inits.json, runs EXOTIC (NASA's official software) without
prompts, checks the result, and leaves you one task: upload the AAVSO file it made.
"""
import json
import math
import re
import shutil
import subprocess
import time
import zipfile
from pathlib import Path

import numpy as np

from . import config, db, net
from .llm import LLM

NEA_TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
CHECKOUT_URL = 'https://science.nasa.gov/citizen-science/exoplanet-watch/how-to-contribute/data-checkout/'
FITS_EXT = ('.fits', '.fit', '.fts', '.fits.gz', '.fit.gz')
PLANET_RE = re.compile(r'\b((?:WASP|HAT-P|HATS|XO|TrES|Qatar|KELT|KPS|K2|Kepler|TOI|CoRoT|GJ|HD|HIP|NGTS|MASCARA|WTS|KOI|TIC)'
                       r'[- ]?\d+(?:\.\d+)?)\s?([b-h])\b', re.I)


# ------------------------------------------------------------------ inbox
def is_fits(p):
    return p.is_file() and p.name.lower().endswith(FITS_EXT) and not p.name.startswith('._')


def fits_dir(root):
    best, n = None, 0
    for d in [root] + [p for p in root.rglob('*') if p.is_dir()]:
        if '__MACOSX' in d.parts or 'exotic_out' in d.parts:
            continue
        k = sum(1 for p in d.iterdir() if is_fits(p))
        if k > n:
            best, n = d, k
    return best, n


def scan_inbox(con, log):
    config.EXO_INBOX.mkdir(parents=True, exist_ok=True)
    added = 0
    for item in sorted(config.EXO_INBOX.iterdir()):
        if item.name.startswith('.'):
            continue
        name = re.sub(r'[^A-Za-z0-9_.-]+', '_', item.stem if item.is_file() else item.name)[:80]
        if db.one(con, 'SELECT id FROM exo WHERE name=?', (name,)):
            continue
        dest = config.EXO_WORK / name
        try:
            if item.is_file() and item.suffix.lower() == '.zip':
                dest.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(item) as z:
                    for m in z.infolist():
                        target = (dest / m.filename).resolve()
                        if not str(target).startswith(str(dest.resolve())):
                            continue  # ignore unsafe paths inside the zip
                        z.extract(m, dest)
                done = config.EXO_INBOX / 'processed'
                done.mkdir(exist_ok=True)
                shutil.move(str(item), str(done / item.name))
            elif item.is_dir() and item.name not in ('processed', 'failed'):
                shutil.move(str(item), str(dest))
            else:
                continue
        except Exception as e:
            log(f'could not unpack {item.name}: {e}')
            bad = config.EXO_INBOX / 'failed'
            bad.mkdir(exist_ok=True)
            try:
                shutil.move(str(item), str(bad / item.name))
            except Exception:
                pass
            continue
        d, n = fits_dir(dest)
        status = 'new' if n >= 10 else 'failed'
        con.execute('INSERT INTO exo(name,folder,nimg,status,error,created,updated) VALUES(?,?,?,?,?,?,?)',
                    (name, str(d or dest), n, status, None if n >= 10 else f'only {n} FITS images found', time.time(), time.time()))
        log(f'new dataset {name}: {n} images')
        added += 1
    return added


# ------------------------------------------------------------------ planet + header info
def find_planet_name(folder):
    texts = []
    for p in list(Path(folder).rglob('*')):
        if p.is_file() and p.suffix.lower() in ('.txt', '.md', '.rtf', '') and p.stat().st_size < 200_000 and 'exotic_out' not in p.parts:
            try:
                texts.append(p.read_text(errors='ignore'))
            except Exception:
                pass
    try:
        from astropy.io import fits
        f = sorted(p for p in Path(folder).iterdir() if is_fits(p))[0]
        h = fits.getheader(f)
        texts.append(' '.join(str(h.get(k, '')) for k in ('OBJECT', 'TARGET', 'TARGNAME')))
    except Exception:
        pass
    for t in texts:
        m = PLANET_RE.search(t)
        if m:
            return f'{m.group(1).upper().replace("TRES", "TrES").replace("QATAR", "Qatar")} {m.group(2).lower()}'.replace('  ', ' ')
    return None


def nea_planet(name):
    variants = {name, name.replace(' ', ''), re.sub(r'(\d)([b-h])$', r'\1 \2', name), name.replace('-', ' ')}
    cols = ('pl_name,hostname,ra,dec,pl_orbper,pl_orbpererr1,pl_tranmid,pl_tranmiderr1,pl_ratror,pl_ratdor,'
            'pl_orbincl,pl_trandur,sy_vmag')
    for v in variants:
        q = f"select {cols} from pscomppars where lower(pl_name)=lower('{v.replace(chr(39), '')}')"
        try:
            js = net.fetch_json(NEA_TAP, params={'query': q, 'format': 'json'}, timeout=90)
            if js:
                return js[0]
        except Exception:
            continue
    return None


CALIB = {'darks': ('dark',), 'flats': ('flat',), 'biases': ('bias', 'zero')}


def calib_dirs(folder):
    """Sub-folders with calibration frames (MicroObservatory datasets come with a 'darks' folder)."""
    out = {}
    for d in sorted(p for p in Path(folder).rglob('*') if p.is_dir()):
        if '__MACOSX' in d.parts or 'exotic_out' in d.parts:
            continue
        name = d.name.lower()
        for kind, keys in CALIB.items():
            if kind not in out and any(k in name for k in keys) and any(is_fits(p) for p in d.iterdir()):
                out[kind] = d
    return out


def _first_2d(path):
    from astropy.io import fits
    with fits.open(path) as hd:
        for h in hd:
            if h.data is not None and getattr(h.data, 'ndim', 0) == 2:
                return np.asarray(h.data, dtype=np.float64), h.header.copy()
    return None, None


def _time_key(path):
    from astropy.io import fits
    try:
        h = fits.getheader(path)
        return str(h.get('DATE-OBS') or h.get('DATE_OBS') or ''), path.name
    except Exception:
        return '', path.name


def read_frames(folder, nmax=5):
    """The FIRST image of the night (EXOTIC's reference frame for the star positions), dark-subtracted when darks exist.
    (A median of several frames smears the stars when the telescope drifts, and the positions end up off-target.)"""
    files = sorted((p for p in Path(folder).iterdir() if is_fits(p)), key=_time_key)
    img = hdr0 = None
    for p in files[:nmax]:
        img, hdr0 = _first_2d(p)
        if img is not None:
            break
    if img is None:
        raise RuntimeError('no 2-D images found in the FITS files')
    darks = calib_dirs(folder).get('darks')
    if darks:
        frames = [d for d in (_first_2d(p)[0] for p in sorted(darks.iterdir()) if is_fits(p)) if d is not None and d.shape == img.shape]
        if frames:
            img = img - np.median(np.stack(frames), axis=0)
    return img, hdr0, files


def obs_date(hdr):
    from datetime import datetime
    for k in ('DATE-OBS', 'DATE_OBS', 'DATE'):
        v = str(hdr.get(k, '') or '')
        m = re.match(r'(\d{4})-(\d{2})-(\d{2})', v)
        if m:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            return d.strftime('%B %d, %Y'), d.strftime('%Y-%m-%d')
    return 'Unknown', 'unknown'


# ------------------------------------------------------------------ star identification (unit-tested)
def detect(img, thresh=6.0):
    import sep
    d = np.ascontiguousarray(img, dtype=np.float64).copy()
    d[~np.isfinite(d)] = np.nanmedian(d)
    bkg = sep.Background(d)
    sub = d - bkg.back()
    objs = sep.extract(sub, thresh, err=bkg.globalrms, minarea=5)
    flux, _, _ = sep.sum_circle(sub, objs['x'], objs['y'], 5.0, err=bkg.globalrms)
    peak = objs['peak'] + np.median(bkg.back())
    o = np.argsort(-flux)
    return np.column_stack([objs['x'][o], objs['y'][o]]), flux[o], peak[o]


def gaia_field(ra, dec, radius_deg=0.75, gmax=14.5):
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.vizier import Vizier
    from astroquery.vizier import conf
    res = None
    for server in ('vizier.cds.unistra.fr', 'vizier.cfa.harvard.edu'):
        try:
            conf.server = server
            conf.timeout = 90
            v = Vizier(columns=['RA_ICRS', 'DE_ICRS', 'Gmag', 'VarFlag'], column_filters={'Gmag': f'<{gmax}'}, row_limit=4000)
            res = v.query_region(SkyCoord(ra * u.deg, dec * u.deg), radius=radius_deg * u.deg, catalog='I/355/gaiadr3')
            break
        except Exception:
            continue
    if res:
        t = res[0]
        return [{'ra': float(r['RA_ICRS']), 'dec': float(r['DE_ICRS']), 'g': float(r['Gmag']),
                 'var': str(r['VarFlag'] if 'VarFlag' in t.colnames else '')} for r in t]
    return gaia_esa(ra, dec, radius_deg, gmax)


def gaia_esa(ra, dec, radius_deg=0.75, gmax=14.5):
    """Fallback when VizieR is down: ESA's own Gaia archive (TAP, CSV)."""
    import csv
    import io
    q = (f'SELECT TOP 4000 ra, dec, phot_g_mean_mag, phot_variable_flag FROM gaiadr3.gaia_source '
         f'WHERE 1=CONTAINS(POINT(ra, dec), CIRCLE({ra}, {dec}, {radius_deg})) AND phot_g_mean_mag < {gmax}')
    try:
        txt = net.fetch_text('https://gea.esac.esa.int/tap-server/tap/sync',
                             params={'REQUEST': 'doQuery', 'LANG': 'ADQL', 'FORMAT': 'csv', 'QUERY': q}, timeout=120)
    except Exception:
        return []
    out = []
    for r in csv.DictReader(io.StringIO(txt)):
        try:
            out.append({'ra': float(r['ra']), 'dec': float(r['dec']), 'g': float(r['phot_g_mean_mag']),
                        'var': str(r.get('phot_variable_flag') or '')})
        except (KeyError, ValueError):
            continue
    return out


def project(stars, ra0, dec0):
    ra = np.array([s['ra'] for s in stars])
    dec = np.array([s['dec'] for s in stars])
    dra = (ra - ra0 + 180) % 360 - 180
    return np.column_stack([dra * math.cos(math.radians(dec0)) * 3600, (dec - dec0) * 3600])


def solve_field(img_xy, cat_xy, n=40):
    """Find the similarity transform (rotation, scale, shift, plus a possible mirror flip) from catalog
    tangent-plane coordinates (arcsec) to image pixels. Returns (transform, flip, inliers) or None."""
    import astroalign as aa
    best = None
    for flip in (1.0, -1.0):
        src = cat_xy[:n] * np.array([flip, 1.0])
        try:
            tf, (s_m, d_m) = aa.find_transform(src, img_xy[:n])
        except Exception:
            continue
        scale_arcsec_px = 1.0 / tf.scale if tf.scale else 0
        if not (0.8 <= scale_arcsec_px <= 20) or len(s_m) < 6:
            continue
        if best is None or len(s_m) > best[2]:
            best = (tf, flip, len(s_m), scale_arcsec_px)
    return best


def choose_stars(img, target_radec, stars, xy_det, flux_det, peak_det):
    """Return dict with target [x,y], comps [[x,y],...], pixel scale; or raise ValueError."""
    ra0, dec0 = target_radec
    cat = sorted(stars, key=lambda s: s['g'])
    cat_xy = project(cat, ra0, dec0)
    sol = solve_field(xy_det, cat_xy)
    if not sol:
        raise ValueError('could not match the star pattern to the Gaia catalog')
    tf, flip, ninl, scale = sol
    h, w = img.shape
    tree_xy = xy_det

    def to_pix(c):
        return tf(np.atleast_2d(c) * np.array([flip, 1.0]))[0]

    def snap(p, r=6.0):
        d = np.hypot(tree_xy[:, 0] - p[0], tree_xy[:, 1] - p[1])
        k = int(np.argmin(d))
        return (k, d[k]) if d[k] <= r else (None, d[k])

    tpix = to_pix([0.0, 0.0])
    k, dist = snap(tpix)
    if k is None:
        raise ValueError(f'target position found but no star detected there (nearest {dist:.1f} px)')
    target = [int(round(tree_xy[k, 0])), int(round(tree_xy[k, 1]))]
    sat = 0.85 * float(np.nanmax(img))
    tgt_g = None
    for s, c in zip(cat, cat_xy):
        if abs(c[0]) < 3 * scale and abs(c[1]) < 3 * scale:
            tgt_g = s['g']
            break
    tgt_g = tgt_g or cat[0]['g']
    comps = []
    for s, c in zip(cat, cat_xy):
        if not (tgt_g - 1.5 <= s['g'] <= tgt_g + 1.2):
            continue
        if 'VARIABLE' in s['var'].upper():
            continue
        p = to_pix(c)
        if p[0] < 25 or p[1] < 25 or p[0] > w - 26 or p[1] > h - 26:
            continue
        if math.hypot(p[0] - target[0], p[1] - target[1]) < 25:
            continue
        crowded = any(o is not s and o['g'] < s['g'] + 2.5 and math.hypot(*(c - oc)) < 25
                      for o, oc in zip(cat, cat_xy))
        if crowded:
            continue
        kk, dd = snap(p, 4.0)
        if kk is None or peak_det[kk] >= sat:
            continue
        comps.append((abs(s['g'] - tgt_g), [int(round(tree_xy[kk, 0])), int(round(tree_xy[kk, 1]))]))
    comps.sort(key=lambda z: z[0])
    comps = [c for _, c in comps[:5]]
    if len(comps) < 2:
        raise ValueError(f'only {len(comps)} good comparison stars found')
    return {'target': target, 'comps': comps, 'scale': round(scale, 3), 'matched': ninl}


def save_preview(img, path, stars=None):
    from PIL import Image, ImageDraw
    v = img[np.isfinite(img)]
    lo, hi = np.percentile(v, 2), np.percentile(v, 99.7)
    a = np.sqrt(np.clip((np.nan_to_num(img, nan=lo) - lo) / max(hi - lo, 1e-9), 0, 1))
    im = Image.fromarray((a * 255).astype(np.uint8)).convert('RGB')   # row 0 at the top = EXOTIC's y
    if stars:
        dr = ImageDraw.Draw(im)
        x, y = stars['target']
        dr.ellipse([x - 9, y - 9, x + 9, y + 9], outline=(255, 90, 54), width=2)
        for x, y in stars['comps']:
            dr.ellipse([x - 8, y - 8, x + 8, y + 8], outline=(77, 179, 211), width=2)
    im.save(path)
    return im.size


# ------------------------------------------------------------------ EXOTIC
def site_for(ds, cfg):
    """Observatory details: MicroObservatory for NASA datasets, your own location/camera for telescope datasets."""
    if ds.get('source') == 'scope':
        sc = cfg.get('telescope') or {}
        if sc.get('lat') in ('', None) or sc.get('lon') in ('', None):
            return None
        lat, lon = float(sc['lat']), float(sc['lon'])
        return {'lat': f'{lat:+.4f}', 'long': f'{lon:+.4f}', 'elev': int(float(sc.get('elev') or 0)), 'filter': 'TG',
                'camera': 'CCD', 'binning': '2x2',
                'notes': f'{sc.get("model") or "Smart telescope"} one-shot-colour frames, green channel (2x2 superpixel); '
                         'reduced automatically with Space Lab + EXOTIC.'}
    ew = dict(cfg['exowatch'])
    ew['notes'] = 'MicroObservatory data from NASA Exoplanet Watch; reduced automatically with Space Lab + EXOTIC.'
    return ew


def write_inits(ds, planet, stars, cfg, hdr, folder, out_dir):
    ew = site_for(ds, cfg)
    cal = calib_dirs(folder)
    _dir = lambda d: str(d) if d else None
    long_date, _ = obs_date(hdr)
    exp = hdr.get('EXPTIME') or hdr.get('EXPOSURE')
    p = planet
    inits = {
        'user_info': {
            'Directory with FITS files': str(folder),
            'Directory to Save Plots': str(out_dir),
            'Directory of Flats': _dir(cal.get('flats')), 'Directory of Darks': _dir(cal.get('darks')), 'Directory of Biases': _dir(cal.get('biases')),
            'AAVSO Observer Code (blank if none)': cfg.get('aavso_code') or '',
            'Secondary Observer Codes (blank if none)': '',
            'Observation date': long_date,
            'Obs. Latitude': ew['lat'], 'Obs. Longitude': ew['long'], 'Obs. Elevation (meters)': ew['elev'],
            'Camera Type (CCD or DSLR)': ew['camera'], 'Pixel Binning': ew['binning'],
            'Filter Name (aavso.org/filters)': ew['filter'],
            'Observing Notes': ew['notes'],
            'Plate Solution? (y/n)': 'n',
            'Add Comparison Stars from AAVSO? (y/n)': 'n',
            'Target Star X & Y Pixel': stars['target'],
            'Comparison Star(s) X & Y Pixel': stars['comps'],
            'Demosaic Format': None, 'Demosaic Output': None,
        },
        'planetary_parameters': {
            'Target Star RA': _sexa(p['ra'] / 15.0), 'Target Star Dec': _sexa(p['dec'], sign=True),
            'Planet Name': p['pl_name'], 'Host Star Name': p['hostname'],
            'Orbital Period (days)': p.get('pl_orbper'), 'Orbital Period Uncertainty': p.get('pl_orbpererr1'),
            'Published Mid-Transit Time (BJD-UTC)': p.get('pl_tranmid'), 'Mid-Transit Time Uncertainty': p.get('pl_tranmiderr1'),
            'Ratio of Planet to Stellar Radius (Rp/Rs)': p.get('pl_ratror'),
            'Ratio of Planet to Stellar Radius (Rp/Rs) Uncertainty': None,
            'Ratio of Distance to Stellar Radius (a/Rs)': p.get('pl_ratdor'),
            'Ratio of Distance to Stellar Radius (a/Rs) Uncertainty': None,
            'Orbital Inclination (deg)': p.get('pl_orbincl'), 'Orbital Inclination (deg) Uncertainty': None,
        },
        'optional_info': {
            'Pixel Scale (Ex: 5.21 arcsecs/pixel)': stars.get('scale'),
            'Exposure Time (s)': float(exp) if exp else None,
        },
    }
    path = Path(out_dir) / 'inits.json'
    path.write_text(json.dumps(inits, indent=2))
    return path


def _sexa(v, sign=False):
    s = '-' if v < 0 else '+'
    v = abs(v)
    d = int(v)
    m = int((v - d) * 60)
    sec = (v - d - m / 60) * 3600
    out = f'{d:02d}:{m:02d}:{sec:05.2f}'
    return (s + out) if sign else out


def run_exotic(inits_path, out_dir, cfg, log):
    exe = config.EXOTIC_BIN
    if not exe.exists():
        raise RuntimeError('EXOTIC is not installed yet (close Meridian and start it again; it repairs Space Lab)')
    logf = Path(out_dir) / 'exotic.log'
    with open(logf, 'w') as fh:
        p = subprocess.run([str(exe), '-red', str(inits_path), '-nea'], cwd=str(out_dir), stdin=subprocess.DEVNULL,
                           stdout=fh, stderr=subprocess.STDOUT, env=config.job_env(),
                           timeout=cfg['exowatch']['timeout_min'] * 60)
    tail = logf.read_text(errors='ignore')[-1500:]
    if p.returncode != 0 and 'FINAL PLANETARY PARAMETERS' not in tail:
        hint = 'EXOTIC stopped to ask a question' if 'EOF' in tail else 'EXOTIC failed'
        raise RuntimeError(f'{hint} (see exotic.log): ' + tail[-300:].replace('\n', ' '))
    return logf


def parse_pm(s):
    m = re.search(r'([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)\s*\+/-\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)', str(s or ''))
    return (float(m.group(1)), float(m.group(2))) if m else (None, None)


def read_results(out_dir):
    out = Path(out_dir)
    fp = sorted((out / 'temp').glob('FinalParams_*.json')) if (out / 'temp').exists() else []
    aavso = sorted(p for p in out.glob('AAVSO_*.txt') if not p.name.startswith('AID_'))
    plot = sorted(out.glob('FinalLightCurve_*.png'))
    if not fp:
        raise RuntimeError('EXOTIC finished but wrote no FinalParams file')
    params = json.loads(fp[-1].read_text()).get('FINAL PLANETARY PARAMETERS', {})
    tmid, tmid_e = parse_pm(params.get('Mid-Transit Time (Tmid)'))
    depth, depth_e = parse_pm(params.get('Transit depth (Rp/Rs)^2'))
    dur, dur_e = parse_pm(params.get('Transit Duration (day)'))
    m = re.search(r'([\d.]+)', str(params.get('Scatter in the residuals of the lightcurve fit is', '')))
    return {'tmid': tmid, 'tmid_err': tmid_e, 'depth_pct': depth, 'depth_err': depth_e, 'dur_d': dur,
            'scatter_pct': float(m.group(1)) if m else None, 'raw': params,
            'aavso': str(aavso[-1]) if aavso else None, 'plot': str(plot[-1]) if plot else None}


def o_minus_c(res, planet):
    T0, P = planet.get('pl_tranmid'), planet.get('pl_orbper')
    if not (res.get('tmid') and T0 and P):
        return None
    n = round((res['tmid'] - T0) / P)
    oc = (res['tmid'] - (T0 + n * P)) * 1440
    pe = math.hypot(planet.get('pl_tranmiderr1') or 0, n * (planet.get('pl_orbpererr1') or 0)) * 1440
    return {'epoch': n, 'oc_min': round(oc, 2), 'pred_err_min': round(pe, 2)}


def quality(res, oc, planet=None):
    notes, good = [], True
    exp = (planet or {}).get('pl_ratror')
    depth = res.get('depth_pct')
    if depth is not None and exp and depth < 0.3 * (exp ** 2) * 100:
        notes.append(f'no clear transit: measured depth {depth:.2f}% vs about {exp ** 2 * 100:.1f}% expected')
        res['usable'] = False
        good = False
    if res.get('scatter_pct') and res['scatter_pct'] > 15:
        notes.append('the light curve is mostly noise (star too faint or too low in the sky)')
        res['usable'] = False
        good = False
    if res.get('tmid_err') is None or res['tmid_err'] * 1440 > 10:
        notes.append('timing uncertainty above 10 minutes'); good = False
    if res.get('scatter_pct') and res['scatter_pct'] > 3:
        notes.append(f'noisy light curve ({res["scatter_pct"]}% scatter)'); good = False
    if oc and abs(oc['oc_min']) > 30:
        notes.append(f'transit {oc["oc_min"]:+.0f} min from the prediction: check the plot')
    return good, notes


# ------------------------------------------------------------------ main
def process(ctx, ds, llm):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    folder = Path(ds['folder'])
    out_dir = config.EXO_WORK / ds['name'] / 'exotic_out'
    out_dir.mkdir(parents=True, exist_ok=True)

    def upd(**kw):
        kw['updated'] = time.time()
        con.execute('UPDATE exo SET ' + ', '.join(f'{k}=?' for k in kw) + ' WHERE id=?', (*kw.values(), ds['id']))

    planet = json.loads(ds['planet']) if ds.get('planet') and ds['planet'].startswith('{') else None
    if ds.get('source') == 'scope' and not site_for(ds, cfg):
        upd(status='needs_planet', error='add your telescope location (latitude/longitude) in Setup, then click Re-run')
        db.add_task(con, 'scope:location', 'setup', 'setup', 'Add your telescope location in Setup',
                    'EXOTIC needs where you observed from. It stays on your Mac and is never shared.', {'tab': 'setup'}, 1, reopen=True)
        return 'needs telescope location'
    if not planet:
        pname = (ds.get('planet') or '').strip() or find_planet_name(folder)
        if not pname and ds.get('source') == 'scope':
            from . import telescope
            info = db.kv_get(con, f'scope_info:{ds["name"]}') or {}
            pname = telescope.match_planet(info.get('ra'), info.get('dec'), db.kv_get(con, 'ephem_nea'))
        if not pname:
            upd(status='needs_planet', error='could not tell which planet this is')
            db.add_task(con, f'exo:planet:{ds["id"]}', 'exowatch', 'input', f'Tell Space Lab which planet dataset "{ds["name"]}" is',
                        'Open the README that came with the data and type the planet name (e.g. WASP-12 b).', {'tab': 'exowatch', 'id': ds['id']}, 1)
            return 'needs planet name'
        planet = nea_planet(pname)
        if not planet:
            upd(status='needs_planet', error=f'"{pname}" not found in the NASA Exoplanet Archive')
            return 'planet not found'
        upd(planet=json.dumps(planet))
    db.close_task(con, f'exo:planet:{ds["id"]}')
    img, hdr, files = read_frames(folder)
    long_date, iso_date = obs_date(hdr)
    upd(obs_date=iso_date)
    prev = config.PLOTS / 'exowatch'
    prev.mkdir(parents=True, exist_ok=True)
    prev_path = prev / f'{ds["name"]}_field.png'
    stars = json.loads(ds['stars']) if ds.get('stars') else None
    if not stars:
        try:
            xy, fl, pk = detect(img)
            gaia = gaia_field(planet['ra'], planet['dec'])
            if not gaia:
                save_preview(img, prev_path)
                upd(status='new', preview=str(prev_path.relative_to(config.DATA)),
                    error='the Gaia star catalog could not be reached (VizieR and ESA); it will try again on the next run')
                return 'star catalog unreachable, will retry'
            stars = choose_stars(img, (planet['ra'], planet['dec']), gaia, xy, fl, pk)
            stars['auto'] = True
            log(f'  stars found automatically: target {stars["target"]}, {len(stars["comps"])} comparisons, {stars["scale"]}"/px')
        except Exception as e:
            save_preview(img, prev_path)
            upd(status='needs_stars', preview=str(prev_path.relative_to(config.DATA)), error=f'automatic star finding failed: {e}')
            db.add_task(con, f'exo:stars:{ds["id"]}', 'exowatch', 'input',
                        f'Click the target and comparison stars for {planet["pl_name"]}',
                        'Space Lab could not match the star field automatically. One click on the target, then 2-5 similar stars.',
                        {'tab': 'exowatch', 'id': ds['id']}, 1)
            return 'needs star clicks'
    save_preview(img, prev_path, stars)
    upd(stars=json.dumps(stars), preview=str(prev_path.relative_to(config.DATA)), status='running', error=None)
    db.close_task(con, f'exo:stars:{ds["id"]}')
    inits = write_inits(ds, planet, stars, cfg, hdr, folder, out_dir)
    log(f'  running EXOTIC on {len(files)}+ images of {planet["pl_name"]} ...')
    run_exotic(inits, out_dir, cfg, log)
    res = read_results(out_dir)
    oc = o_minus_c(res, planet)
    good, notes = quality(res, oc, planet)
    res.update({'oc': oc, 'good': good, 'notes': notes})
    plot_rel = None
    if res.get('plot'):
        dst = prev / f'{ds["name"]}_lightcurve.png'
        shutil.copy(res['plot'], dst)
        plot_rel = str(dst.relative_to(config.DATA))
    facts = (f"Planet {planet['pl_name']}, observed {iso_date}. Measured mid-transit time {res['tmid']} ± {res['tmid_err']} days "
             f"(BJD_TDB), i.e. ±{(res['tmid_err'] or 0) * 1440:.1f} minutes. Transit depth {res['depth_pct']}%. Light-curve scatter "
             f"{res['scatter_pct']}%. Compared with the published prediction it came {oc['oc_min'] if oc else 'n/a'} minutes "
             f"early(-)/late(+); prediction uncertainty {oc['pred_err_min'] if oc else 'n/a'} min. Quality notes: {'; '.join(notes) or 'none'}.")
    text = llm.chat('Explain this Exoplanet Watch result to a 12th grader in 3 short sentences: what was measured, '
                    'whether it looks trustworthy, and why the timing helps telescopes like JWST. Do not invent numbers.\n\n' + facts,
                    system='You are a friendly NASA Exoplanet Watch scientist.', max_tokens=220) or facts
    upd(status='done', result=json.dumps(res), aavso_file=res.get('aavso'), plot=plot_rel, explain=text)
    if res.get('usable') is False:
        db.add_task(con, f'exo:check:{ds["id"]}', 'exowatch', 'review',
                    f'{planet["pl_name"]} ({iso_date}): no usable transit, don\'t upload',
                    'Space Lab could not see the transit in this dataset: ' + '; '.join(notes) +
                    '. Check out a different dataset (a brighter star, higher in the sky) instead.', {'tab': 'exowatch', 'id': ds['id']}, 2)
    elif res.get('aavso'):
        db.add_task(con, f'exo:upload:{ds["id"]}', 'exowatch', 'upload',
                    f'Upload your {planet["pl_name"]} transit ({iso_date}) to the AAVSO Exoplanet Database',
                    ('Looks good. ' if good else 'Check the plot first: ' + '; '.join(notes) + '. ') +
                    'Log in at aavso.org, open the Exoplanet section and upload the AAVSO file shown on the Exoplanet Watch tab.',
                    {'tab': 'exowatch', 'id': ds['id'], 'file': res['aavso']}, 1 if good else 2)
    return f'{planet["pl_name"]}: Tmid ±{(res["tmid_err"] or 0) * 1440:.1f} min'


def run(ctx):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    llm = LLM(cfg, log)
    added = scan_inbox(con, log)
    try:
        from . import telescope
        added += telescope.scan_inbox(con, log)
    except Exception as e:
        log(f'telescope inbox: {e}')
    

    if not cfg.get('aavso_code'):
        db.add_task(con, 'exo:aavso', 'exowatch', 'setup', 'One-time: create a free AAVSO account and enter your observer code in Setup',
                    'Your observer code goes into every result file so Exoplanet Watch can credit you on its papers.',
                    {'tab': 'setup', 'url': 'https://www.aavso.org/'}, 3)
    else:
        db.close_task(con, 'exo:aavso')
    
    todo = db.rows(con, "SELECT * FROM exo WHERE status IN ('new','ready')")
    if not todo and added == 0:
        log('No new datasets. Check one out at ' + CHECKOUT_URL + ' and drop the zip into the inbox.')
    results = []
    for ds in todo:
        if ctx.time_left() < 600:
            break
        try:
            results.append(process(ctx, ds, llm))
        except subprocess.TimeoutExpired:
            con.execute("UPDATE exo SET status='failed', error=?, updated=? WHERE id=?", ('EXOTIC took too long', time.time(), ds['id']))
            results.append('timed out')
        except Exception as e:
            log(f'{ds["name"]}: {type(e).__name__}: {e}')
            con.execute("UPDATE exo SET status='failed', error=?, updated=? WHERE id=?", (str(e)[:500], time.time(), ds['id']))
            results.append('failed')
    pending = con.execute("SELECT COUNT(*) FROM exo WHERE status IN ('new','ready','running','needs_stars','needs_planet')").fetchone()[0]
    if pending == 0 and not any(config.EXO_INBOX.glob('*.zip')):
        week = time.strftime('%G-W%V')
        db.close_tasks_like(con, 'exo:checkout:%', status='dismissed', except_key=f'exo:checkout:{week}')
        db.add_task(con, f'exo:checkout:{week}', 'exowatch', 'get-data', 'Check out a new Exoplanet Watch dataset',
                    f'Request a dataset by email at the Exoplanet Watch Data Checkout page, then drop the zip into '
                    f'"{config.EXO_INBOX}". Space Lab does the rest.', {'tab': 'exowatch', 'url': CHECKOUT_URL}, 3)
    else:
        db.close_tasks_like(con, 'exo:checkout:%')
    return f'{added} new datasets; processed {len(results)}: ' + ('; '.join(results) if results else 'nothing to do')
