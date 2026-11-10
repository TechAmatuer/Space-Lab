"""Old-plate precovery of distant objects: the method used for 2026 ED138, run end to end by Space Lab.

For a distant object with a short observed arc (most new Rubin discoveries), old photographic survey plates can extend the
arc by decades. This job:
  1. picks targets: distant objects (perihelion beyond Jupiter) bright enough for plates, whose first observation is recent;
  2. predicts where each was at the epoch of every scanned SuperCOSMOS plate (UKST, ESO Schmidt, POSS-I, POSS-II;
     1949-2003) with the 3-sigma uncertainty (JPL Horizons), using SpaceRockSuite (E. A. Puerto, MIT licence);
  3. asks the Minor Planet Center what is already reported, so nobody's work is duplicated;
  4. measures each plate: cutout, the archive date checked at -1/0/+1 day (SuperCOSMOS dates are often one day early),
     a fresh plate solution fitted to Gaia DR3 stars moved to the plate epoch with their proper motions, nearby stars
     modelled and subtracted, the object's centroid, its shape against the direction of motion, and a check that nothing
     sits at that spot on a plate from another date;
  5. where the MPC already has a position from the same plate, compares with it (a direct accuracy test);
  6. otherwise files a candidate for a person to review, with a draft ADES report. Space Lab never submits by itself.
The local AI writes plain-English notes and gives each stamp a first look; all numbers come from the code.
"""
import io
import json
import math
import os
import re
import time
from datetime import datetime, timezone

import numpy as np

from . import config, db, net

SBDB_Q = 'https://ssd-api.jpl.nasa.gov/sbdb_query.api'
GAIA_TAP = 'https://gea.esac.esa.int/tap-server/tap/sync'
SKYBOT = 'https://ssp.imcce.fr/webservices/skybot/api/conesearch.php'
MPC_OBS = 'https://data.minorplanetcenter.net/api/get-obs'
ARCSEC = 206264.80624709636
FLOOR = 0.25            # arcsec: systematic floor for Schmidt survey plates (as adopted in the 2026 ED138 work)
J2016 = 2457389.0       # Gaia DR3 reference epoch (2016.0, TCB ~ JD)
SITES = {               # MPC code: observers, telescope design, aperture (m)
    '260': ('UK Schmidt Telescope Unit', 'Schmidt', '1.2'),
    '809': ('ESO Schmidt survey', 'Schmidt', '1.0'),
    '675': ('Palomar Observatory Sky Survey', 'Schmidt', '1.2'),
}


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def jd_to_year(jd):
    return 2000.0 + (jd - 2451545.0) / 365.25


def iso_z(jd):
    from astropy.time import Time
    return Time(jd, format='jd', scale='utc').isot[:21] + 'Z'


# ================================================================== targets
def v_estimate(h, q):
    """Brightest likely V: at perihelion, at opposition (r = q, distance from Earth = q - 1)."""
    return h + 5 * math.log10(q * max(q - 1.0, 0.5))


def pick_targets(rows, done, now_year, plate_limit=21.0, first_after=1995, want=3, manual=()):
    """Rank distant objects for an old-plate search: bright enough to show on plates, first seen after the plates were
    taken (so plates would extend the arc), not searched recently. Manual targets always come first."""
    out = [{'des': m, 'name': m, 'why': 'on your list'} for m in manual if m not in done]
    cand = []
    for r in rows:
        h, q, first = _f(r.get('H')), _f(r.get('q')), str(r.get('first_obs') or '')
        a = _f(r.get('a')) or q
        des = r.get('pdes') or r.get('full_name')
        if not des or des in done or h is None or q is None or q < 5 or len(first) < 4:
            continue
        fy = int(first[:4])
        v = v_estimate(h, a or q)          # at its typical distance (perihelion is too optimistic for eccentric orbits)
        if fy <= first_after or v > plate_limit:
            continue
        gain = fy - 1950
        cand.append({'des': des, 'name': (r.get('full_name') or des).strip(), 'h': h, 'q': q, 'first': first, 'v': round(v, 1),
                     'why': f'first seen {fy}; about V {v:.1f} at best, plates reach ~{plate_limit:g}',
                     'score': gain + 3 * (plate_limit - v)})
    cand.sort(key=lambda c: -c['score'])
    return (out + cand)[:max(want, len(out))]


def sbdb_targets(log, h_max=8.0, limit=400):
    js = net.fetch_json(SBDB_Q, params={'fields': 'pdes,full_name,H,q,a,e,i,first_obs,data_arc,n_obs_used,condition_code',
                                         'sb-kind': 'a', 'sb-cdata': json.dumps({'AND': ['q|GT|5', f'H|LT|{h_max}']}),
                                         'limit': str(limit)}, timeout=120)
    f = js.get('fields') or []
    rows = [dict(zip(f, r)) for r in js.get('data') or []]
    log(f'  {len(rows)} distant objects (perihelion beyond 5 au, H < {h_max}) in JPL\'s database')
    return rows


# ================================================================== geometry and the plate solution
def tangent(ra, dec, ra0, dec0):
    """Standard coordinates (xi, eta) in arcsec of (ra, dec) about (ra0, dec0)."""
    r, d, r0, d0 = (np.radians(np.asarray(v, float)) for v in (ra, dec, ra0, dec0))
    cosc = np.sin(d0) * np.sin(d) + np.cos(d0) * np.cos(d) * np.cos(r - r0)
    xi = np.cos(d) * np.sin(r - r0) / cosc
    eta = (np.cos(d0) * np.sin(d) - np.sin(d0) * np.cos(d) * np.cos(r - r0)) / cosc
    return xi * ARCSEC, eta * ARCSEC


def untangent(xi, eta, ra0, dec0):
    x, y = np.asarray(xi, float) / ARCSEC, np.asarray(eta, float) / ARCSEC
    d0, r0 = math.radians(dec0), math.radians(ra0)
    ra = r0 + np.arctan2(x, math.cos(d0) - y * math.sin(d0))
    dec = np.arctan2((math.sin(d0) + y * math.cos(d0)) * np.cos(ra - r0), math.cos(d0) - y * math.sin(d0))
    return np.degrees(ra) % 360, np.degrees(dec)


def propagate(ra, dec, pmra, pmdec, jd):
    """Gaia DR3 positions (epoch 2016.0) moved to the plate epoch with their proper motions (mas/yr; pmra includes cos dec).
    Stars without a proper motion keep their 2016 position."""
    dt = (jd - J2016) / 365.25
    ra, dec = np.asarray(ra, float), np.asarray(dec, float)
    pmra = np.nan_to_num(np.asarray(pmra, float)); pmdec = np.nan_to_num(np.asarray(pmdec, float))
    dec2 = dec + pmdec * dt / 3.6e6
    ra2 = ra + pmra * dt / 3.6e6 / np.cos(np.radians(dec))
    return ra2 % 360, dec2


def _terms(x, y, order):
    x, y = np.asarray(x, float), np.asarray(y, float)
    t = [np.ones_like(x), x, y]
    if order >= 2:
        t += [x * x, x * y, y * y]
    if order >= 3:
        t += [x ** 3, x * x * y, x * y * y, y ** 3]
    return np.column_stack(t)


def fit_plate(x, y, xi, eta, order=None, clip=3.0, iters=5):
    """Pixel (x, y) -> standard coordinates. Order 1 (6 constants) for few stars, 2 or 3 when there are plenty.
    Iterative sigma clipping. Returns a dict, or None with too few stars."""
    x, y, xi, eta = (np.asarray(v, float) for v in (x, y, xi, eta))
    n = len(x)
    if order is None:
        order = 1 if n < 25 else 2 if n < 60 else 3
    need = {1: 3, 2: 6, 3: 10}[order]
    if n < need + 3:
        if order > 1:
            return fit_plate(x, y, xi, eta, order - 1, clip, iters)
        return None
    cx, cy, s = x.mean(), y.mean(), max(np.ptp(x), np.ptp(y), 1.0) / 2
    keep = np.ones(n, bool)
    for _ in range(iters):
        A = _terms((x[keep] - cx) / s, (y[keep] - cy) / s, order)
        cxi = np.linalg.lstsq(A, xi[keep], rcond=None)[0]
        ceta = np.linalg.lstsq(A, eta[keep], rcond=None)[0]
        Af = _terms((x - cx) / s, (y - cy) / s, order)
        rx, ry = xi - Af @ cxi, eta - Af @ ceta
        rr = np.hypot(rx, ry)
        sig = 1.4826 * np.median(np.abs(rr[keep])) if keep.sum() > 3 else rr[keep].std()
        new = rr < max(clip * sig, 0.05)
        if (new == keep).all() or new.sum() < need + 3:
            break
        keep = new
    dof = max(1, keep.sum() - need)
    rms_x = float(np.sqrt(np.sum(rx[keep] ** 2) / dof))
    rms_y = float(np.sqrt(np.sum(ry[keep] ** 2) / dof))
    return {'order': order, 'cx': cx, 'cy': cy, 's': s, 'cxi': cxi, 'ceta': ceta, 'n': int(keep.sum()), 'n_all': n,
            'rms_x': rms_x, 'rms_y': rms_y, 'used': keep, 'res_x': rx, 'res_y': ry}


def apply_plate(p, x, y):
    A = _terms((np.asarray(x, float) - p['cx']) / p['s'], (np.asarray(y, float) - p['cy']) / p['s'], p['order'])
    return A @ p['cxi'], A @ p['ceta']


def invert_plate(p, xi, eta, x0, y0, iters=8):
    """Pixel position that the plate model maps to (xi, eta), by Newton steps from a first guess."""
    x, y = float(x0), float(y0)
    for _ in range(iters):
        a, b = apply_plate(p, x, y)
        ax, bx = apply_plate(p, x + 0.5, y)
        ay, by = apply_plate(p, x, y + 0.5)
        J = np.array([[(ax - a)[0] / 0.5, (ay - a)[0] / 0.5], [(bx - b)[0] / 0.5, (by - b)[0] / 0.5]])
        dx, dy = np.linalg.solve(J, [xi - a[0], eta - b[0]])
        x, y = x + dx, y + dy
        if abs(dx) + abs(dy) < 1e-4:
            break
    return x, y


def plate_error_at(p, x, y):
    """1-sigma uncertainty (arcsec) of the plate model itself at pixel (x, y): leverage of that point in the fit."""
    A = _terms((np.asarray([x], float) - p['cx']) / p['s'], (np.asarray([y], float) - p['cy']) / p['s'], p['order'])
    used = p['used']
    xs = p.get('_x')
    if xs is None:
        return (p['rms_x'] + p['rms_y']) / 2 / math.sqrt(max(p['n'], 1))
    U = _terms((xs[used] - p['cx']) / p['s'], (p['_y'][used] - p['cy']) / p['s'], p['order'])
    try:
        lev = float((A @ np.linalg.inv(U.T @ U) @ A.T).item())
    except np.linalg.LinAlgError:
        lev = 1.0 / max(p['n'], 1)
    return math.sqrt(max(lev, 0)) * (p['rms_x'] + p['rms_y']) / 2


# ================================================================== finding stars and the object
def robust_sigma(a):
    a = np.asarray(a, float)
    a = a[np.isfinite(a)]
    return float(1.4826 * np.median(np.abs(a - np.median(a)))) if a.size else float('nan')


def find_peaks(img, nsig=5.0, fwhm_px=3.0, edge=6):
    """Local maxima of the smoothed image above nsig times its noise: (x, y, peak) brightest first."""
    from scipy import ndimage
    s = ndimage.gaussian_filter(np.nan_to_num(img, nan=float(np.nanmedian(img))), fwhm_px / 2.3548)
    sig = robust_sigma(s)
    m = (s == ndimage.maximum_filter(s, size=max(3, int(round(fwhm_px)) | 1))) & (s > np.median(s) + nsig * sig)
    m[:edge] = m[-edge:] = False
    m[:, :edge] = m[:, -edge:] = False
    ys, xs = np.nonzero(m)
    o = np.argsort(-s[ys, xs])
    return [(float(xs[i]), float(ys[i]), float(s[ys[i], xs[i]])) for i in o]


def measure_stars(img, fwhm0=3.0, nsig=6.0, max_stars=400):
    """Gaussian centroids of the clear point sources; drops saturated (bloated, flat-topped) and odd-shaped ones."""
    from spacerocksuite.imaging.cleanup import fit_gaussian
    out = []
    box = max(4, int(round(1.5 * fwhm0)))
    for x, y, _ in find_peaks(img, nsig, fwhm0)[:max_stars]:
        f = fit_gaussian(img, x, y, box=box, fwhm0=fwhm0)
        if not f or not np.isfinite(f['x']) or abs(f['x'] - x) > 2 or abs(f['y'] - y) > 2 or f['amp'] <= 0:
            continue
        out.append(f)
    if len(out) >= 5:
        fw = np.array([f['fwhm'] for f in out])
        amp = np.array([f['amp'] for f in out])
        med = float(np.median(fw))
        top = np.percentile(amp, 92)
        out = [f for f in out if 0.6 * med < f['fwhm'] < 1.6 * med and f['amp'] < top and f['x_err'] < 0.5]
    return out


def match(det_xy, cat_xy, radius_px=3.0, search_px=12.0):
    """Pairs (i_det, j_cat) after removing an overall shift between the archive solution and the catalogue."""
    det_xy, cat_xy = np.asarray(det_xy, float).reshape(-1, 2), np.asarray(cat_xy, float).reshape(-1, 2)
    if not len(det_xy) or not len(cat_xy):
        return [], (0.0, 0.0)
    d = det_xy[:, None, :] - cat_xy[None, :, :]
    r = np.hypot(d[..., 0], d[..., 1])
    j = np.argmin(r, 1)
    near = r[np.arange(len(det_xy)), j] < search_px
    if near.sum() < 3:
        return [], (0.0, 0.0)
    shift = np.median(d[np.arange(len(det_xy)), j][near], 0)
    r = np.hypot(d[..., 0] - shift[0], d[..., 1] - shift[1])
    j = np.argmin(r, 1)
    pairs, used = [], set()
    for i in np.argsort(r[np.arange(len(det_xy)), j]):
        if r[i, j[i]] < radius_px and j[i] not in used:
            pairs.append((int(i), int(j[i])))
            used.add(j[i])
    return pairs, (float(shift[0]), float(shift[1]))


def ellipse_distance(dx, dy, a, b, pa_deg):
    """How far (dx east, dy north, arcsec) lies from the centre in units of the ellipse (1 = on its edge).
    pa_deg: position angle of the major axis, measured from north through east."""
    t = math.radians(pa_deg)
    u = dx * math.sin(t) + dy * math.cos(t)          # along the major axis
    v = dx * math.cos(t) - dy * math.sin(t)
    return math.hypot(u / max(a, 1e-6), v / max(b, 1e-6))


def find_object(clean, pred_xy, search_px, fwhm_px, avoid_xy=(), avoid_px=2.5, nsig=4.0):
    """Brightest point source in the star-subtracted image within search_px of the prediction, away from where stars were
    removed (left-overs there are not trusted). Returns (x, y, peak_snr) or None."""
    from scipy import ndimage
    med = float(np.nanmedian(clean))
    noise = robust_sigma(ndimage.gaussian_filter(np.nan_to_num(clean, nan=med), fwhm_px / 2.3548)) or robust_sigma(clean)
    best = None
    for x, y, pk in find_peaks(clean, nsig, fwhm_px, edge=3):
        if math.hypot(x - pred_xy[0], y - pred_xy[1]) > search_px:
            continue
        if any(math.hypot(x - ax, y - ay) < avoid_px for ax, ay in avoid_xy):
            continue
        snr = (pk - med) / noise
        if best is None or snr > best[2]:
            best = (x, y, float(snr))
    return best


def forced_snr(img, x, y, r_px, seed=0):
    from spacerocksuite.imaging.cleanup import aperture_sum, empirical_noise
    flux, _ = aperture_sum(img, x, y, r_px)
    noise, _ = empirical_noise(img, x, y, r_px, n=200, seed=seed)
    return float(flux / noise) if noise and noise > 0 else float('nan')


def elongation(fit, motion_pa_px, trail_px):
    """Is the image stretched along the direction of motion? fit: elliptical Gaussian (theta in pixel axes, degrees).
    Returns a short verdict and the numbers. Only meaningful when the motion during the exposure is >= ~1 pixel."""
    if not fit:
        return {'verdict': 'not measured'}
    a, b = max(fit['sx'], fit['sy']), min(fit['sx'], fit['sy'])
    major = fit['theta'] if fit['sx'] >= fit['sy'] else (fit['theta'] + 90) % 180
    ratio = a / max(b, 1e-6)
    diff = abs((major - motion_pa_px + 90) % 180 - 90)
    out = {'ratio': round(ratio, 2), 'angle_off_deg': round(diff, 0), 'trail_px': round(trail_px, 1)}
    if trail_px < 1.0:
        out['verdict'] = 'too little motion during the exposure to check'
    elif ratio < 1.08:
        out['verdict'] = 'round (no clear stretch)'
    elif diff <= 30:
        out['verdict'] = 'stretched along the direction of motion'
    else:
        out['verdict'] = 'stretched, but not along the motion'
    return out


# ================================================================== timing: the plate's date, checked
def timing_consensus(trials):
    """trials: {asteroid: {offset_days: snr}}. Each asteroid votes for the offset where it is clearly seen
    (S/N >= 5 and at least twice every other trial). Returns (offset or None, votes, text)."""
    votes = {}
    for name, t in trials.items():
        ok = {d: s for d, s in t.items() if s is not None and np.isfinite(s)}
        if not ok:
            continue
        best = max(ok, key=ok.get)
        if ok[best] >= 5 and all(ok[best] >= 2 * max(s, 0.5) for d, s in ok.items() if d != best):
            votes.setdefault(best, []).append(name)
    if not votes:
        return None, {}, 'no known asteroid on this cutout was clearly seen, so the date was not checked this way'
    best = max(votes, key=lambda d: len(votes[d]))
    others = sum(len(v) for d, v in votes.items() if d != best)
    if others:
        return None, votes, f'known asteroids disagree about the date ({", ".join(f"{d:+g} d: {len(v)}" for d, v in votes.items())})'
    return best, votes, f'{len(votes[best])} known asteroid(s) appear where expected for the date {best:+g} d'


def parse_skybot(text):
    """SkyBoT text output -> list of {name, ra, dec, v, err}."""
    head, out = None, []
    for ln in (text or '').splitlines():
        if ln.startswith('#') and '|' in ln and 'Name' in ln:
            head = [h.strip().lower() for h in ln.lstrip('#').split('|')]
            continue
        if ln.startswith('#') or '|' not in ln or not head:
            continue
        v = dict(zip(head, [x.strip() for x in ln.split('|')]))

        def col(*names):
            for k in head:
                if any(k.startswith(n) for n in names):
                    return v.get(k)
        try:
            from astropy.coordinates import Angle
            ra = Angle(col('ra'), unit='hourangle').deg
            dec = Angle(col('de'), unit='deg').deg
        except Exception:
            continue
        out.append({'name': col('name') or col('num'), 'ra': float(ra), 'dec': float(dec), 'v': _f(col('mv')),
                    'err': _f(col('err'))})
    return out


def skybot(ra, dec, radius_deg, jd, stn, log):
    try:
        txt = net.fetch_text(SKYBOT, params={'-ep': f'{jd:.5f}', '-ra': f'{ra:.6f}', '-dec': f'{dec:.6f}', '-rd': f'{radius_deg:.4f}',
                                            '-mime': 'text', '-output': 'object', '-loc': stn, '-filter': '120', '-objFilter': '100',
                                            '-refsys': 'EQJ2000', '-from': 'SpaceLab'}, timeout=90, min_gap=1.5)
        return parse_skybot(txt)
    except Exception as e:
        log(f'    SkyBoT: {e}')
        return None


# ================================================================== what the MPC already has
def parse_obs80(lines):
    """Minimal 80-column parser: (jd, ra, dec, stn) per line."""
    from astropy.time import Time
    out = []
    for ln in lines:
        if len(ln) < 80 or ln[14] in 'sv':                    # satellite / roving second lines
            continue
        try:
            y, mo, d = int(ln[15:19]), int(ln[20:22]), float(ln[23:32])
            day = int(d)
            jd = Time(datetime(y, mo, day, tzinfo=timezone.utc)).jd + (d - day)
            h, m, s = float(ln[32:34]), float(ln[35:37]), float(ln[38:44])
            sg = -1 if ln[44] == '-' else 1
            dd, dm, ds = float(ln[45:47]), float(ln[48:50]), float(ln[51:56])
        except (ValueError, IndexError):
            continue
        out.append({'jd': jd, 'ra': (h + m / 60 + s / 3600) * 15, 'dec': sg * (dd + dm / 60 + ds / 3600), 'stn': ln[77:80],
                    'note2': ln[14]})
    return out


def mpc_observations(des, log):
    body = json.dumps({'desigs': [des], 'output_format': ['OBS80']}).encode()
    try:
        js = json.loads(_get_with_body(MPC_OBS, body))
    except Exception as e:
        log(f'  MPC observations for {des}: {e}')
        return None
    lines = []
    for item in js if isinstance(js, list) else [js]:
        v = item.get('OBS80') if isinstance(item, dict) else None
        lines += v.splitlines() if isinstance(v, str) else (v or [])
    return parse_obs80(lines)


def _get_with_body(url, body):
    import urllib.request
    req = urllib.request.Request(url, data=body, method='GET', headers={'Content-Type': 'application/json', 'User-Agent': net.UA})
    with urllib.request.urlopen(req, timeout=90, context=net.ssl_ctx()) as r:
        return r.read()


# the same telescope can be filed under more than one MPC code: measurements made on Digitized Sky Survey scans have their
# own codes (260 Siding Spring-DSS, 261 Palomar-DSS, 262 La Silla-DSS) next to the observatory's (413, 675 / 644, 809)
SAME_SITE = [{'675', '261', '644'}, {'260', '413'}, {'809', '262'}]


def same_site(a, b):
    return a == b or any(a in g and b in g for g in SAME_SITE)


def already_reported(mpc, stn, jd, tol_days=1.6):
    """MPC observations from the same telescope within tol_days of the plate (any measurer): this plate is likely done."""
    return [o for o in (mpc or []) if same_site(o['stn'], stn) and abs(o['jd'] - jd) <= tol_days]


# ================================================================== Gaia
def gaia_cone(ra, dec, radius_deg, gmax=20.5, log=print):
    q = ('SELECT ra, dec, pmra, pmdec, phot_g_mean_mag FROM gaiadr3.gaia_source WHERE '
         f"1=CONTAINS(POINT('ICRS',ra,dec),CIRCLE('ICRS',{ra:.6f},{dec:.6f},{radius_deg:.5f})) AND phot_g_mean_mag < {gmax}")
    try:
        txt = net.fetch_text(GAIA_TAP, params={'REQUEST': 'doQuery', 'LANG': 'ADQL', 'FORMAT': 'csv', 'QUERY': q}, timeout=180, min_gap=1.0)
        rows = [ln.split(',') for ln in txt.strip().splitlines()[1:]]
        cols = [np.array([_f(r[k]) if r[k] else np.nan for r in rows], float) for k in range(5)]
    except Exception as e:
        log(f'    Gaia (ESA) failed ({e}); trying VizieR')
        from .catalogs import _asu, _rows
        rr = _rows(_asu({'-source': 'I/355/gaiadr3', '-c': f'{ra} {dec:+}', '-c.rd': f'{radius_deg:.5f}',
                         '-out': 'RA_ICRS,DE_ICRS,pmRA,pmDE,Gmag', 'Gmag': f'<{gmax}', '-out.max': '20000'}, timeout=180))
        cols = [np.array([_f(r.get(k)) if r.get(k) else np.nan for r in rr], float) for k in ('RA_ICRS', 'DE_ICRS', 'pmRA', 'pmDE', 'Gmag')]
    ok = np.isfinite(cols[0]) & np.isfinite(cols[1])
    return {k: c[ok] for k, c in zip(('ra', 'dec', 'pmra', 'pmdec', 'g'), cols)}


# ================================================================== one plate, measured
def solve_plate(img, wcs, gaia, jd, ra0, dec0, fwhm0=3.0, log=print):
    """Fresh plate solution from Gaia DR3 at the plate epoch. Returns (plate model, stars, catalogue at epoch) or Nones."""
    stars = measure_stars(img, fwhm0)
    if len(stars) < 8:
        return None, stars, None
    ra_e, dec_e = propagate(gaia['ra'], gaia['dec'], gaia['pmra'], gaia['pmdec'], jd)
    old = abs(jd - J2016) / 365.25 > 25
    usable = (gaia['g'] > 11.5) & (gaia['g'] < 19.5) & (np.isfinite(gaia['pmra']) if old else True)
    cx, cy = wcs.all_world2pix(ra_e[usable], dec_e[usable], 0)
    pairs, shift = match([(s['x'], s['y']) for s in stars], np.column_stack([cx, cy]))
    if len(pairs) < 8:
        log(f'    only {len(pairs)} stars matched to Gaia (shift {shift[0]:.1f}, {shift[1]:.1f} px)')
        return None, stars, None
    idx = np.flatnonzero(usable)
    xs = np.array([stars[i]['x'] for i, _ in pairs])
    ys = np.array([stars[i]['y'] for i, _ in pairs])
    xi, eta = tangent(ra_e[idx[[j for _, j in pairs]]], dec_e[idx[[j for _, j in pairs]]], ra0, dec0)
    p = fit_plate(xs, ys, xi, eta)
    if p:
        p['_x'], p['_y'] = xs, ys
    return p, stars, {'ra': ra_e, 'dec': dec_e, 'g': gaia['g']}


def measure_plate(img, wcs, gaia, jd, pred, exp_s, rate, fwhm0=3.0, scale=0.67, avoid_mag=None, log=print):
    """Everything measured on one plate cutout. pred: {'ra','dec','a','b','pa'} (3-sigma ellipse in arcsec, PA from north
    through east); rate: (dRA*cos dec, dDec) arcsec/hour. Returns a dict (see keys) - never raises for 'not found'."""
    from spacerocksuite.imaging.cleanup import background, empirical_psf, matrix_deblend, fit_gaussian, gaussian_psf_fn
    out = {'found': False}
    img = background(np.nan_to_num(img, nan=float(np.nanmedian(img))), 41)
    p, stars, cat = solve_plate(img, wcs, gaia, jd, pred['ra'], pred['dec'], fwhm0, log)
    if p is None:
        out['why'] = 'not enough Gaia stars could be matched for a plate solution'
        return out
    fw = float(np.median([s['fwhm'] for s in stars])) if stars else fwhm0
    out['plate'] = {'order': p['order'], 'n': p['n'], 'n_all': p['n_all'], 'rms_ra': round(p['rms_x'], 3), 'rms_dec': round(p['rms_y'], 3),
                    'fwhm_arcsec': round(fw * scale, 2)}
    wx, wy = wcs.all_world2pix([pred['ra']], [pred['dec']], 0)
    px, py = invert_plate(p, 0.0, 0.0, float(wx[0]), float(wy[0]))
    out['pred_xy'] = (px, py)
    # stars near the prediction: modelled with the plate's own star shape and removed, so a blended object is left behind
    reach = max(pred['a'], 6.0) + 15.0
    h = int(min(max(reach / scale + 8, 25), min(img.shape) // 2 - 1))
    x0, y0 = int(round(px)) - h, int(round(py)) - h
    sub = img[max(0, y0):y0 + 2 * h + 1, max(0, x0):x0 + 2 * h + 1]
    ox, oy = max(0, x0), max(0, y0)
    edge = (max(9, int(4 * fw) | 1)) // 2 - 1     # stars just outside the window still spill light into it
    cxp, cyp = [], []
    xi_c, eta_c = tangent(cat['ra'], cat['dec'], pred['ra'], pred['dec'])
    near = np.hypot(xi_c, eta_c) <= (h + edge) * scale * 1.5
    for xi_s, eta_s, g in zip(xi_c[near], eta_c[near], cat['g'][near]):
        if avoid_mag is not None and g > avoid_mag:
            continue
        x, y = invert_plate(p, float(xi_s), float(eta_s), px + float(xi_s) / scale, py + float(eta_s) / scale)
        if ox - edge <= x < ox + sub.shape[1] + edge and oy - edge <= y < oy + sub.shape[0] + edge:
            cxp.append(x); cyp.append(y)
    local = [(x - ox, y - oy) for x, y in zip(cxp, cyp)]
    # the plate's own star shape, or a Gaussian of the measured width: whichever leaves less behind
    psfs = []
    try:
        psfs.append(empirical_psf(img, [(s['x'], s['y']) for s in stars[:60]], size=max(9, int(4 * fw) | 1)))
    except ValueError:
        pass
    psfs.append(gaussian_psf_fn(max(9, int(4 * fw) | 1), fw))
    clean, psf = sub, None
    if local:
        best = None
        for ps in psfs:
            try:
                r = matrix_deblend(sub, local, ps)
            except Exception as e:
                log(f'    star removal failed: {e}')
                continue
            if best is None or r['rms'] < best[0]['rms']:
                best = (r, ps)
        if best:
            clean, psf = best[0]['cleaned'], best[1]
    out['n_removed'] = len(local)
    out['stamp'] = sub.astype(np.float32)
    out['stamp_origin'] = (ox, oy)
    search = pred['a'] / scale + 2 * fw
    too_close = max(1.0, 0.75 * fw)           # closer than this to a removed star, a 'source' is usually what is left of the star
    hit = find_object(clean, (px - ox, py - oy), search, fw, avoid_xy=local, avoid_px=too_close)
    if not hit:
        out['clean'] = np.asarray(clean, np.float32)
        out['why'] = 'nothing clearly visible inside the 3-sigma area after removing the stars'
        out['snr_at_pred'] = round(forced_snr(clean, px - ox, py - oy, max(1.5, 0.75 * fw)), 1)
        return out
    # fit the object together with its neighbours, then measure it on the image with only the neighbours removed
    tx_, ty_ = hit[0], hit[1]
    fr = fe = None
    fit_snr = None
    for _ in range(3):
        try:
            jr = matrix_deblend(sub, local, psf if psf is not None else psfs[-1], target_xy=(tx_, ty_),
                                target_psf=psf if psf is not None else psfs[-1])
            clean, fit_snr = jr['cleaned'], jr.get('target_snr')
        except Exception:
            pass
        fr = fit_gaussian(clean, tx_, ty_, box=max(4, int(round(1.5 * fw))), fwhm0=fw)
        if not fr or not np.isfinite(fr['x']) or math.hypot(fr['x'] - hit[0], fr['y'] - hit[1]) > 2 * fw:
            fr = None
            break
        moved = math.hypot(fr['x'] - tx_, fr['y'] - ty_)
        tx_, ty_ = fr['x'], fr['y']
        if moved < 0.02:
            break
    out['clean'] = np.asarray(clean, np.float32)
    if fr:
        fe = fit_gaussian(clean, tx_, ty_, box=max(5, int(round(2 * fw))), fwhm0=fw, elliptical=True)
    if not fr:
        out['why'] = 'a source was there but its centre could not be measured'
        return out
    if any(math.hypot(fr['x'] - ax, fr['y'] - ay) < too_close for ax, ay in local):
        out['why'] = 'the only source there sits on top of a catalogued star (too close to tell apart)'
        return out
    tx, ty = fr['x'] + ox, fr['y'] + oy
    xi_t, eta_t = apply_plate(p, tx, ty)
    ra_t, dec_t = untangent(xi_t, eta_t, pred['ra'], pred['dec'])
    ap_snr = forced_snr(clean, fr['x'], fr['y'], max(1.5, 0.75 * fw))
    snr = fit_snr if fit_snr is not None and math.isfinite(fit_snr) else ap_snr     # from the joint fit's own uncertainty
    # uncertainty: centroid (scaled by the fit), plate model at this spot, and the survey-plate floor
    cen = math.hypot(fr['x_err'], fr['y_err']) / math.sqrt(2) * scale
    pl = plate_error_at(p, tx, ty)
    sig = math.sqrt(cen ** 2 + pl ** 2 + FLOOR ** 2)
    dx, dy = float(xi_t[0]), float(eta_t[0])
    # direction of motion in pixel axes: map one hour of motion through the plate model
    mx, my = invert_plate(p, rate[0], rate[1], px + rate[0] / scale, py + rate[1] / scale)
    motion_pa_px = math.degrees(math.atan2(my - py, mx - px)) % 180
    trail_px = math.hypot(*rate) * exp_s / 3600 / scale
    out.update({'found': True, 'x': tx, 'y': ty, 'ra': float(ra_t[0]), 'dec': float(dec_t[0]), 'snr': round(snr, 1), 'snr_aperture': round(ap_snr, 1),
                'peak_snr': round(hit[2], 1), 'sigma': round(sig, 2), 'sig_centroid': round(cen, 2), 'sig_plate': round(pl, 3),
                'off_ra': round(dx, 2), 'off_dec': round(dy, 2), 'off': round(math.hypot(dx, dy), 2),
                'in_ellipse': round(ellipse_distance(dx, dy, pred['a'], pred['b'], pred['pa']), 2),
                'shape': elongation(fe, motion_pa_px, trail_px), 'fwhm_obj_arcsec': round(fr['fwhm'] * scale, 2)})
    return out


def absent_elsewhere(img, wcs, ra, dec, fwhm_px, gaia=None, jd=None, log=print):
    """On a plate from another date: is there anything at the object's spot? (A real moving object should be gone.)
    Neighbouring stars are modelled and the flux is fitted at exactly that spot, so a bright star a few arcseconds away
    does not count as 'something there'. Returns None when this plate cannot tell (blank, edge, no solution)."""
    from spacerocksuite.imaging.cleanup import background, matrix_deblend, gaussian_psf_fn
    img = background(np.nan_to_num(img, nan=float(np.nanmedian(img))), 41)
    if not robust_sigma(img) > 0 or len(find_peaks(img, 6.0, fwhm_px)) < 5:
        return None                                   # blank or empty cutout: this plate does not cover the spot
    scale = pixel_scale(wcs)
    stars_xy = []
    x, y = (float(v[0]) for v in wcs.all_world2pix([ra], [dec], 0))
    if gaia is not None and jd is not None:
        p, stars, cat = solve_plate(img, wcs, gaia, jd, ra, dec, fwhm_px, log)
        if p is not None:
            x, y = invert_plate(p, 0.0, 0.0, x, y)
            xi_c, eta_c = tangent(cat['ra'], cat['dec'], ra, dec)
            for k in np.flatnonzero(np.hypot(xi_c, eta_c) < 40 * scale):
                if math.hypot(xi_c[k], eta_c[k]) > 0.8 * fwhm_px * scale:     # a catalogued star right on the spot stays
                    stars_xy.append(invert_plate(p, float(xi_c[k]), float(eta_c[k]), x + xi_c[k] / scale, y + eta_c[k] / scale))
    if not (12 <= x < img.shape[1] - 12 and 12 <= y < img.shape[0] - 12):
        return None
    h = int(min(30, x, y, img.shape[1] - x - 1, img.shape[0] - y - 1))
    xi0, yi0 = int(round(x)) - h, int(round(y)) - h
    sub = img[yi0:yi0 + 2 * h + 1, xi0:xi0 + 2 * h + 1]
    psf = gaussian_psf_fn(max(9, int(4 * fwhm_px) | 1), fwhm_px)
    e = max(9, int(4 * fwhm_px) | 1) // 2 - 1
    loc = [(sx - xi0, sy - yi0) for sx, sy in stars_xy if -e <= sx - xi0 < sub.shape[1] + e and -e <= sy - yi0 < sub.shape[0] + e]
    try:
        r = matrix_deblend(sub, loc, psf, target_xy=(x - xi0, y - yi0), target_psf=psf)
        snr = r.get('target_snr')
    except Exception:
        snr = None
    if snr is None or not math.isfinite(snr):
        snr = forced_snr(img, x, y, max(1.5, 0.75 * fwhm_px))
    if not math.isfinite(snr):
        return None
    return {'snr': round(float(snr), 1), 'absent': bool(snr < 3), 'xy': (x, y)}


def pixel_scale(wcs):
    from astropy.wcs.utils import proj_plane_pixel_scales
    return float(np.mean(np.asarray(proj_plane_pixel_scales(wcs.celestial), float)) * 3600)


def choose_date(trials):
    """trials: {offset_days: measurement}. Keep the corrected archive date (0) unless another whole-day offset shows the
    object clearly (S/N >= 5, inside the 3-sigma area) and at least twice as strongly as the corrected date does."""
    def strength(m):
        return m['snr'] if m.get('found') and m.get('in_ellipse', 9) <= 1 else 0.0
    base = trials.get(0.0) or {}
    best = max(trials, key=lambda d: strength(trials[d]))
    if best != 0.0 and strength(trials[best]) >= 5 and strength(trials[best]) >= 2 * max(strength(base), 0.5):
        return best
    return 0.0


def asteroid_timing(img, wcs, ra, dec, radius_deg, jd, site, limit, fwhm_px, log):
    """Known asteroids on this cutout, looked for at the plate date and one day either side: the date where they appear
    tells whether the archive date (after the usual +1 day) is right."""
    from spacerocksuite.imaging.cleanup import background
    bk = background(np.nan_to_num(img, nan=float(np.nanmedian(img))), 41)
    trials = {}
    for d in (-1.0, 0.0, 1.0):
        rows = skybot(ra, dec, radius_deg, jd + d, site, log)
        if rows is None:
            return {'text': 'SkyBoT (known-asteroid service) did not answer'}
        for r in rows:
            if r['v'] is None or r['v'] > limit - 1.0 or (r['err'] or 0) > 10:
                continue
            x, y = wcs.all_world2pix([r['ra']], [r['dec']], 0)
            x, y = float(x[0]), float(y[0])
            if not (10 <= x < bk.shape[1] - 10 and 10 <= y < bk.shape[0] - 10):
                continue
            pk = find_peaks(bk[int(y) - 6:int(y) + 7, int(x) - 6:int(x) + 7], 3.0, fwhm_px, edge=1)
            xx, yy = (int(x) - 6 + pk[0][0], int(y) - 6 + pk[0][1]) if pk else (x, y)
            trials.setdefault(r['name'], {})[d] = forced_snr(bk, xx, yy, max(1.5, 0.75 * fwhm_px))
    off, votes, text = timing_consensus(trials)
    return {'offset': off, 'n': sum(len(v) for v in votes.values()), 'text': text}


def verdict(m, cmp_=None):
    """Plain decision from the numbers. 'detected' still means: a person must look before anything is sent."""
    if not m.get('found'):
        return 'not found'
    if cmp_ is not None and not cmp_.get('absent', True):
        return 'static source (also there on another date)'
    if m['in_ellipse'] > 1.0:
        return 'outside the 3-sigma area'
    if m['snr'] >= 5:
        return 'detected'
    if m['snr'] >= 3:
        return 'marginal'
    return 'not found'


# ================================================================== the report
def ades_plate(des, meas, cfg, site, plate_label, date_note):
    """Draft ADES PSV for one plate position (one file per station). mode PHO: photographic plate."""
    observers, design, aperture = SITES.get(site, ('Plate survey', 'Schmidt', ''))
    perm = des if des.strip().isdigit() else ''
    prov = '' if perm else des
    name = cfg.get('observer_name') or 'YOUR NAME'
    pl = meas.get('plate') or {}
    head = ['# version=2017', '# observatory', f'! mpcCode {site}', '# submitter', f'! name {name}',
            '# observers', f'! name {observers}', '# measurers', f'! name {name}',
            '# telescope', f'! design {design}'] + ([f'! aperture {aperture}'] if aperture else []) + ['! detector PHO',
            '# comment', f'! line Measured on the SuperCOSMOS scan of {plate_label}. {date_note}',
            f'! line Plate solution: order-{pl.get("order")} polynomial fitted to {pl.get("n")} Gaia DR3 stars moved to the plate epoch with their proper motions; rms {pl.get("rms_ra")}" / {pl.get("rms_dec")}".',
            '! line Uncertainties: centroid, plate model and a 0.25" survey-plate floor, added in quadrature.']
    cols = ['permID', 'provID', 'mode', 'stn', 'obsTime', 'ra', 'dec', 'rmsRA', 'rmsDec', 'astCat']
    row = [perm, prov, 'PHO', site, meas['obsTime'], f"{meas['ra']:.7f}", f"{meas['dec']:+.7f}", f"{meas['sigma']:.3f}",
           f"{meas['sigma']:.3f}", 'Gaia3']
    return '\n'.join(head + [' |'.join(cols), ' |'.join(row)]) + '\n'


def review_png(path, title, m, cmp_img=None, cmp_label='', scale=0.67, pred=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.patches import Ellipse, Circle
    panels = [('plate (prediction at the cross)', m.get('stamp')), ('stars removed', m.get('clean'))]
    if cmp_img is not None:
        panels.append((cmp_label or 'another date', cmp_img))
    fig, ax = plt.subplots(1, len(panels), figsize=(4 * len(panels), 4.3))
    ax = np.atleast_1d(ax)
    ox, oy = m.get('stamp_origin', (0, 0))
    for a, (lab, im) in zip(ax, panels):
        if im is None:
            a.axis('off')
            continue
        v = np.nanpercentile(im, [5, 99.5])
        a.imshow(im, origin='lower', cmap='gray_r', vmin=v[0], vmax=v[1])
        a.set_title(lab, fontsize=9)
        a.set_xticks([]); a.set_yticks([])
        if lab != (cmp_label or 'another date') and m.get('pred_xy'):
            px, py = m['pred_xy'][0] - ox, m['pred_xy'][1] - oy
            a.plot(px, py, '+', color='#1f6feb', ms=14, mew=1.2)
            if pred:
                a.add_patch(Ellipse((px, py), 2 * pred['a'] / scale, 2 * pred['b'] / scale, angle=90 - pred['pa'],
                                    fill=False, color='#1f6feb', lw=0.8, ls='--'))
            if m.get('found'):
                a.add_patch(Circle((m['x'] - ox, m['y'] - oy), 6, fill=False, color='#e4572e', lw=1.4))
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=90)
    plt.close(fig)


def ai_look(llm, png, facts):
    try:
        r = llm.vision('You check precovery measurements on scanned photographic sky survey plates. Left: the plate cutout '
                       'with the predicted position (blue cross) and the measured object (red circle). Middle: the same with '
                       'catalogued stars removed. Right (if present): the same sky on another date, where a moving object '
                       'should be absent. Is the red-circled source a real point source that is absent on the other date, '
                       'or a plate flaw, a star residual or noise? Answer JSON: {"verdict": "real" | "artefact" | "unclear", '
                       '"confidence": 0.0-1.0, "reasons": ["short", "..."]}. At most 3 reasons. Numbers: ' + facts,
                       [str(png)], json_mode=True, max_tokens=250)
        if isinstance(r, dict) and r.get('verdict'):
            return {'verdict': str(r['verdict'])[:20], 'confidence': _f(r.get('confidence')),
                    'reasons': [str(x)[:120] for x in (r.get('reasons') or [])][:3]}
    except Exception:
        pass
    return None


# ================================================================== the job
def _fits(fits_bytes):
    from astropy.io import fits
    from spacerocksuite.imaging.stack import clean_wcs
    hd = fits.open(io.BytesIO(fits_bytes))
    h = next((x for x in hd if getattr(x, 'data', None) is not None and np.ndim(x.data) == 2), hd[0])
    return np.asarray(h.data, float), clean_wcs(h.header), h.header


def _served_plate(header, plates, survey_id):
    """The plate the cutout server actually used (it picks one per band), with its date from the SSA plate list."""
    got = str(header.get('PLATENUM', header.get('PLATEID', ''))).strip()
    if not got:
        return None
    for r in plates:
        if r[1].strip().lstrip('0') == got.lstrip('0') and (survey_id is None or r[0] == survey_id):
            return {'survey': r[0], 'plate': r[1], 'jd_ssa': r[6] + 2400000.5, 'exp_min': r[7]}
    return None


def withdraw(con, des, plate_id):
    """Someone else has reported this plate since it was found here: take back an unreviewed candidate (never one you judged)."""
    for c in db.rows(con, "SELECT id FROM pc_cands WHERE des=? AND archive=? AND status='new'", (des, f'SuperCOSMOS {plate_id}')):
        con.execute('DELETE FROM pc_cands WHERE id=?', (c['id'],))
        db.close_task(con, f'plate:{des}:{plate_id}')


def stale_objects(cands, mpc_for):
    """Objects with a waiting plate candidate that someone else has reported to the MPC in the meantime.
    cands: rows of pc_cands (des, obs as JSON text); mpc_for(des) -> the MPC's observations of that object."""
    from astropy.time import Time
    out, seen = set(), {}
    for c in cands:
        try:
            o = json.loads(c['obs'])[0]
            jd = Time(o['obsTime'].replace('Z', ''), format='isot', scale='utc').jd
        except (ValueError, KeyError, IndexError, TypeError):
            continue                                         # not a plate measurement this check understands
        if c['des'] not in seen:
            seen[c['des']] = mpc_for(c['des']) or []
        if already_reported(seen[c['des']], str(o.get('stn') or ''), jd):
            out.add(c['des'])
    return out


def search_object(ctx, llm, t, mpc, plates_cfg):
    """Plate search and measurements for one object. Returns a summary dict for the Meridian page."""
    os.environ.setdefault('SRS_HOME', str(config.CACHE / 'srs'))
    from spacerocksuite.precovery import get_track, search_plates, fetch_cutout
    from spacerocksuite.archives.supercosmos import load_plates, SURVEYS, cutout
    from .precovery import horizons_predict
    con, log = ctx.con, ctx.log
    des = t['des']
    summ = {'des': des, 'name': t.get('name') or des, 'why': t.get('why'), 't': time.time(), 'plates': [], 'note': ''}
    try:
        track = get_track(des, '1949-01-01', '2003-12-31', step_days=float(plates_cfg.get('step_days', 2)))
    except Exception as e:
        summ['note'] = f'no orbit/track: {e}'
        return summ
    hits = search_plates(track, float(plates_cfg.get('sigma', 3)), ['SuperCOSMOS'], log=lambda *a: log('   ', *a))
    hits = [h for h in hits if (h.depth_margin or -9) >= float(plates_cfg.get('depth_margin_min', 0.0))
            and h.sigma_a <= float(plates_cfg.get('max_ellipse_arcsec', 150))]
    log(f'  {des}: {len(hits)} plate(s) deep enough and with a small enough search area')
    P = load_plates()['rows']
    for h in hits[:int(plates_cfg.get('plates_per_object', 8))]:
        if ctx.time_left() < 180:
            summ['note'] = 'stopped for time; the rest next run'
            break
        sid, site = h.extra['survey_id'], h.extra['site']
        label = f"{h.plate_id} ({datetime.fromtimestamp((h.mid_jd - 2440587.5) * 86400, timezone.utc):%Y-%m-%d})"
        rec = {'plate': h.plate_id, 'site': site, 'jd': h.mid_jd, 'v': h.vmag_pred, 'limit': h.limit_mag, 'ellipse': round(h.sigma_a, 1)}
        summ['plates'].append(rec)
        done = already_reported(mpc, site, h.mid_jd)
        try:
            fb = fetch_cutout(h, size_arcmin=max(6.0, min(15.0, 2 * h.sigma_a / 60 + 5)))
            img, wcs, hdr = _fits(fb)
            served = _served_plate(hdr, P, sid)
            if served and served['plate'].strip().lstrip('0') != h.plate_id.split()[-1].lstrip('0'):
                rec['result'] = f"the cutout server gave plate {served['plate']} instead; skipped"
                continue
            scale = pixel_scale(wcs)
            half_diag = math.hypot(*img.shape) / 2 * scale / 3600
            preds = {d: horizons_predict(des, site, [h.mid_jd + d - 2400000.5])[0] for d in (0.0, -1.0, 1.0)}
            gaia = gaia_cone(preds[0.0]['ra'], preds[0.0]['dec'], min(0.3, half_diag), log=log)
            # the archive date, checked: the object's own signal at the corrected date and one day either side
            trials = {}
            for d, pr in preds.items():
                pred = {'ra': pr['ra'], 'dec': pr['dec'], 'a': max(pr['smaa'] or 1, 1.0), 'b': max(pr['smia'] or 1, 1.0), 'pa': pr['theta'] or 0.0}
                rate = (pr['ra_rate'] or 0.0, pr['dec_rate'] or 0.0)            # arcsec/hour (RA rate includes cos dec)
                m = measure_plate(img, wcs, gaia, h.mid_jd + d, pred, h.exptime_s, rate, scale=scale,
                                  avoid_mag=(h.vmag_pred or 20) + 3, log=lambda *a: log('   ', *a))
                m['date_offset'], m['pred'] = d, pred
                trials[d] = m
                if d == 0.0 and m.get('found') and m['snr'] >= 8 and m['in_ellipse'] <= 1:
                    break                                            # clear at the corrected date: no need to try others
            m = trials[choose_date(trials)]
            jd = h.mid_jd + m['date_offset']
            pred = m['pred']
            rec['date_probe'] = {f'{d:+g}': (t.get('snr') if t.get('found') else None) for d, t in trials.items()}
            rec['timing'] = asteroid_timing(img, wcs, preds[0.0]['ra'], preds[0.0]['dec'], half_diag * 0.7, h.mid_jd, site,
                                            h.limit_mag or 20, (m.get('plate') or {}).get('fwhm_arcsec', 2.0) / scale, log)
            m['obsTime'] = iso_z(jd)
            # another date, same sky: a real moving object must be gone
            cmp_, cmp_img, cmp_label = None, None, ''
            if m.get('found'):
                for osid, (oname, *_rest) in SURVEYS.items():
                    if osid == sid or SURVEYS[osid][3] == SURVEYS[sid][3]:
                        continue
                    try:
                        cb = cutout(m['ra'], m['dec'], osid, 4.0)
                        ci, cw, ch = _fits(cb)
                        sp = _served_plate(ch, P, None)
                        if sp and abs(sp['jd_ssa'] - h.mid_jd) < 0.5:
                            continue
                        cmp_ = absent_elsewhere(ci, cw, m['ra'], m['dec'], (m['plate']['fwhm_arcsec'] or 2) / pixel_scale(cw),
                                                gaia, (sp['jd_ssa'] + 1.0) if sp else h.mid_jd, log=lambda *a: None)
                        if cmp_ is not None:
                            x, y = cmp_.pop('xy')
                            hh = m['stamp'].shape[0] // 2
                            cmp_img = ci[max(0, int(y) - hh):int(y) + hh + 1, max(0, int(x) - hh):int(x) + hh + 1]
                            cmp_label = f"{oname} plate {sp['plate'] if sp else '?'}, another date"
                            cmp_['plate'] = cmp_label
                            break
                    except Exception as e:
                        log(f'    comparison plate ({oname}) skipped: {e}')
            rec['comparison'] = cmp_
            v = verdict(m, cmp_)
            rec.update({k: m.get(k) for k in ('snr', 'off', 'in_ellipse', 'sigma', 'shape', 'date_offset', 'n_removed', 'why', 'snr_at_pred', 'ra', 'dec', 'obsTime')})
            rec['solution'] = m.get('plate')
            rec['result'] = v
            png = config.PLOTS / 'plates' / f"{re.sub(r'[^A-Za-z0-9]+', '_', des)}_{re.sub(r'[^A-Za-z0-9-]+', '_', h.plate_id)}.png"
            review_png(png, f'{des} on {label}: {v}', m, cmp_img, cmp_label, scale=scale, pred=m.get('pred'))
            rec['png'] = str(png.relative_to(config.DATA))
            tm = rec['timing']
            date_note = ('SSA plate date +1 d (SuperCOSMOS dates are often one day early)' +
                         (f'; the object is seen at a further {m["date_offset"]:+g} d' if m['date_offset'] else '') +
                         (f'; date confirmed by {tm["n"]} known asteroid(s)' if tm.get('offset') == m['date_offset'] else '') + '.')
            if done and m.get('found'):
                # someone already reported this plate: a direct test of this measurement
                o = min(done, key=lambda o: abs(o['jd'] - jd))
                pr_o = track.at(o['jd'])
                pr_m = track.at(jd)
                ddx, ddy = tangent(o['ra'], o['dec'], m['ra'], m['dec'])
                mvx, mvy = tangent(float(pr_o[0]), float(pr_o[1]), float(pr_m[0]), float(pr_m[1]))
                rec['validation'] = {'mpc_dt_h': round((o['jd'] - jd) * 24, 2), 'diff_arcsec': round(float(math.hypot(ddx - mvx, ddy - mvy)), 2)}
                rec['result'] = 'already reported by others: used as an accuracy test'
                withdraw(con, des, h.plate_id)
                log(f"    {h.plate_id}: matches the MPC's position to {rec['validation']['diff_arcsec']}\" (times differ by {rec['validation']['mpc_dt_h']} h)")
                continue
            if done:
                rec['result'] = 'already reported by others (not found here)'
                withdraw(con, des, h.plate_id)
                continue
            log(f"    {h.plate_id}: {v}" + (f", S/N {m['snr']}, {m['off']}\" from the prediction" if m.get('found') else f" ({m.get('why')})"))
            if v in ('detected', 'marginal'):
                facts = (f"S/N {m['snr']}, {m['off']}\" from prediction (3-sigma area {pred['a']:.0f}\"), plate solution {m['plate']['n']} "
                         f"Gaia stars rms {m['plate']['rms_ra']}\", {m['n_removed']} stars removed nearby, shape: {m['shape'].get('verdict')}, "
                         f"other date: {'nothing there' if cmp_ and cmp_['absent'] else 'not checked' if not cmp_ else 'something there'}")
                ai = ai_look(llm, png, facts) if llm else None
                obs = [{'obsTime': m['obsTime'], 'ra': m['ra'], 'dec': m['dec'], 'filter': SURVEYS[sid][3], 'snr': m['snr'], 'timing': rec['timing'],
                        'date_probe': rec['date_probe'], 'objtype': t.get('objtype') or ('tno' if (t.get('q') or 30) >= 29 else 'unclassified'),
                        'mode': 'PHO', 'stn': site, 'sigma': m['sigma'], 'plate': h.plate_id, 'off': m['off'], 'in_ellipse': m['in_ellipse'],
                        'shape': m['shape'], 'comparison': cmp_, 'solution': m['plate'], 'date_offset': m['date_offset'], 'ai': ai,
                        'n_removed': m['n_removed']}]
                ades = ades_plate(des, m, ctx.cfg, site, label, date_note)
                exists = db.one(con, 'SELECT id FROM pc_cands WHERE des=? AND archive=?', (des, f'SuperCOSMOS {h.plate_id}'))
                if not exists:
                    con.execute('INSERT INTO pc_cands(des,night,archive,obs,offset,mdist,score,gif,png,ades,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                                (des, m['obsTime'][:10], f'SuperCOSMOS {h.plate_id}', json.dumps(obs), m['off'], m['in_ellipse'],
                                 m['snr'], None, rec['png'], ades, 'new', time.time()))
                    db.add_task(con, f"plate:{des}:{h.plate_id}", 'asteroids', 'review', f"Look at {des} on the {label} plate",
                                f"S/N {m['snr']}, {m['off']}\" from the prediction. Is it a real point source that is gone on the other date?",
                                {'tab': 'asteroids'}, 2)
        except Exception as e:
            rec['result'] = f'error: {type(e).__name__}: {str(e)[:140]}'
            log(f'    {h.plate_id}: {rec["result"]}')
    return summ


def run(ctx):
    from .llm import LLM
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    pc = cfg.get('plates') if isinstance(cfg.get('plates'), dict) else {}
    state = db.kv_get(con, 'plates') or {}
    objs = {o['des']: o for o in state.get('objects') or []}
    redo = float(pc.get('research_days', 120)) * 86400
    manual = [m for m in pc.get('manual') or [] if m]
    done = {d for d, o in objs.items() if time.time() - (o.get('t') or 0) < (20 * 3600 if d in manual else redo)}   # your list: daily
    try:
        rows = sbdb_targets(log, float(pc.get('h_max', 8.0)))
    except Exception as e:
        log(f'  target list unavailable: {e}')
        rows = []
    # a plate waiting for your look may have been reported by someone else since: search that object again now, so the
    # candidate is taken back and the plate becomes an accuracy test instead
    stale = stale_objects(db.rows(con, "SELECT des, obs FROM pc_cands WHERE status='new' AND archive LIKE 'SuperCOSMOS %'"),
                          lambda d: mpc_observations(d, log))
    if stale:
        log(f"  already at the MPC since they were found here: plates of {', '.join(sorted(stale))}; checking them again")
        done -= stale
        manual = list(dict.fromkeys(manual + sorted(stale)))
    todo = pick_targets(rows, done, datetime.now().year, float(pc.get('plate_limit', 21.0)), int(pc.get('first_after', 1995)),
                        int(pc.get('objects_per_run', 2)), manual)
    log(f"  objects this run: {', '.join(t['des'] for t in todo) or 'none'}")
    try:
        llm = LLM(cfg, log)
    except Exception:
        llm = None
    found = checked = tests = 0
    for t in todo:
        if ctx.time_left() < 300:
            break
        mpc = mpc_observations(t['des'], log)
        s = search_object(ctx, llm, t, mpc, pc)
        objs[t['des']] = s
        checked += len(s['plates'])
        found += sum(1 for p in s['plates'] if p.get('result') in ('detected', 'marginal'))
        tests += sum(1 for p in s['plates'] if p.get('validation'))
        ctx.progress(f"old plates: {checked} checked, {found} possible detection(s)")
    allv = [p['validation']['diff_arcsec'] for o in objs.values() for p in o.get('plates') or [] if p.get('validation')]
    out = {'t': time.time(), 'objects': sorted(objs.values(), key=lambda o: -(o.get('t') or 0))[:30],
           'accuracy': {'n': len(allv), 'median_arcsec': round(float(np.median(allv)), 2) if allv else None}}
    db.kv_set(con, 'plates', out)
    return (f'old plates: {len(todo)} object(s), {checked} plate(s) checked, {found} possible detection(s) for you to review'
            + (f', {tests} accuracy test(s) against positions already at the MPC' if tests else ''))
