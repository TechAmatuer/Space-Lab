"""Variable-star observatory, part 3: the checks that keep the photometry honest (see VARIABLE_STARS.md).

- Frame screening. Before an image is measured it is compared with the other images of the same field: how many stars
  it shows, whether they are trailed or blurred, whether the sky is unusually bright. A clearly bad frame (cloud, a closed
  roof, a tracking failure) is set aside with the reason; a borderline one can be shown to the local vision model.
- Satellite and aircraft trails. A trail that crosses the variable flags that one measurement; a trail across a comparison
  star removes that star from the ensemble for that frame. The rest of the frame is still used.
- Comparison-star health. Each comparison star's agreement with the others is kept for every frame (after removing any
  colour trend, which is a colour term and not a bad star) and compared night after night. A star that varies, or sits
  well off its chart value, is flagged; catalogue look-ups (AAVSO VSX and the Gaia DR3 variability flag, both through
  VizieR) catch known variables. Variable stars leave the ensemble at once, the measurements that used them are measured
  again, and a report for the AAVSO's sequence team (CHET) is drafted for the observer to send.
- Zero points and extinction. Each night the zero points of all frames are fitted against airmass, filter by filter. A
  frame far below the fit lost light to cloud; the scatter says whether the night was photometric.
- Overheads. The gaps between exposures, from the images' own time stamps: downloads, filter changes, slews. The planner
  uses them so tonight's plan fits the night as it really runs.
- Before an AAVSO file is uploaded: a plain-English list of what to check, worded by the local AI when it is running
  (every number it writes must come from the facts it was given, or the plain template is used instead).
"""
import json
import math
import re
import time
from pathlib import Path

import numpy as np

from . import config, db

CHET_URL = 'https://www.aavso.org/chet-help'
CHET_DIR = config.DATA / 'vstars' / 'chet'
THUMBS = config.CACHE / 'vs_thumbs'
VIZIER_SERVERS = ('vizier.cds.unistra.fr', 'vizier.cfa.harvard.edu')


def _exptime(hdr):
    return float(hdr.get('EXPTIME') or hdr.get('EXPOSURE') or 0.0)


def saturation(hdr, s):
    """(saturated, linear limit) in ADU: 95% of the camera's saturation (FITS SATURATE, else the setting), and the top of the
    linear range (the setting linear_adu from the camera's linearity test, else 85% of saturation)."""
    level = float(hdr.get('SATURATE') or s.get('saturation_adu', 60000))
    lin = s.get('linear_adu')
    return level * 0.95, float(lin) if lin else level * 0.85


# ------------------------------------------------------------------ one source-extraction pass, shared by the check and the photometry
def extract(img, hdr, s):
    """Background, sky-subtracted image and the detected objects (the same detection the photometry used before, done once)."""
    import sep
    data = np.ascontiguousarray(img, dtype=np.float64)
    bad = ~np.isfinite(data)
    if bad.any():
        data[bad] = np.nanmedian(data)
    bkg = sep.Background(data)
    sub = data - bkg.back()
    try:
        objs = sep.extract(sub, 10, err=bkg.globalrms, minarea=6)
    except Exception:                                    # a crowded field can overflow sep's buffer
        sep.set_extract_pixstack(3_000_000)
        objs = sep.extract(sub, 10, err=bkg.globalrms, minarea=6)
    sat, lin = saturation(hdr, s)
    return {'data': data, 'bkg': bkg, 'sub': sub, 'objs': objs, 'sat': sat, 'lin': lin, '_rms': None}


def rms_map(sky):
    if sky.get('_rms') is None:
        sky['_rms'] = sky['bkg'].rms()
    return sky['_rms']


def fwhm_of(sky):
    """Typical star width (pixels) from the unsaturated, unflagged detections."""
    objs = sky['objs']
    good = objs[(objs['flag'] == 0) & (objs['peak'] < sky['sat'] * 0.8)] if len(objs) else objs
    fwhm = float(np.median(2.3548 * np.sqrt((good['a'] ** 2 + good['b'] ** 2) / 2))) if len(good) >= 3 else 4.0
    return min(max(fwhm, 1.5), 20.0), good


def streaks(sky, fwhm, scale=4):
    """Satellite and aircraft trails: long, thin objects in a 4x4-binned copy of the frame (binning lifts faint trails and
    keeps this quick). Returns segments (x0, y0, x1, y1, width) in full-resolution pixels."""
    import sep
    sub = sky['sub']
    h, w = (sub.shape[0] // scale) * scale, (sub.shape[1] // scale) * scale
    if h < 8 * scale or w < 8 * scale:
        return []
    b = np.ascontiguousarray(sub[:h, :w].reshape(h // scale, scale, w // scale, scale).mean(axis=(1, 3)))
    rms = float(sky['bkg'].globalrms) / scale
    try:
        objs = sep.extract(b, 3.0, err=rms, minarea=12, deblend_cont=1.0)     # no deblending: a trail stays one object
    except Exception:
        return []
    out = []
    if not len(objs):
        return out
    el = objs['a'] / np.maximum(objs['b'], 1e-3)
    long_ = np.flatnonzero((el > 6) & (objs['a'] * scale > max(40.0, 6 * fwhm)))
    for i in long_[:40]:
        half = 2.0 * float(objs['a'][i]) * scale             # a line of length L has an rms length of L/sqrt(12): 2a covers it
        th = float(objs['theta'][i])
        dx, dy = half * math.cos(th), half * math.sin(th)
        x, y = (float(objs['x'][i]) + 0.5) * scale - 0.5, (float(objs['y'][i]) + 0.5) * scale - 0.5
        out.append((x - dx, y - dy, x + dx, y + dy, 3.0 * float(objs['b'][i]) * scale + fwhm))
    return out


def near_streak(x, y, r, segs):
    """True when an aperture of radius r at (x, y) touches one of the trails."""
    for x0, y0, x1, y1, wd in segs or []:
        vx, vy = x1 - x0, y1 - y0
        L2 = vx * vx + vy * vy
        t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((x - x0) * vx + (y - y0) * vy) / L2))
        if math.hypot(x - (x0 + t * vx), y - (y0 + t * vy)) < r + wd / 2:
            return True
    return False


def metrics(sky, hdr, s):
    """What the frame check looks at: stars found, their width and elongation, the sky level, trails."""
    fwhm, good = fwhm_of(sky)
    exp = _exptime(hdr) or 1.0
    m = {'n': int(len(sky['objs'])), 'fwhm': round(fwhm, 2), 'sky': round(float(sky['bkg'].globalback) / exp, 3),
         'rms': round(float(sky['bkg'].globalrms), 2), 'elong': None}
    if len(good) >= 3:
        m['elong'] = round(float(np.median(good['a'] / np.maximum(good['b'], 1e-3))), 3)
    m['streaks'] = [[round(float(v), 1) for v in sg] for sg in streaks(sky, fwhm)]
    return m


def reference(con, target, filt, days=30):
    """The field's usual frame (medians of earlier frames of this star and filter that passed the check), or None."""
    rows = db.rows(con, "SELECT qc FROM vs_frames WHERE target=? AND filter=? AND qc IS NOT NULL AND status IN ('ok','review') "
                        'AND at > ? ORDER BY at DESC LIMIT 60', (target, filt, time.time() - days * 86400))
    ms = []
    for r in rows:
        try:
            q = json.loads(r['qc'])
        except (TypeError, ValueError):
            continue
        if q.get('n'):
            ms.append(q)
    if len(ms) < 3:
        return None
    med = lambda k: float(np.median([q[k] for q in ms if q.get(k) is not None])) if any(q.get(k) is not None for q in ms) else None
    return {'n': med('n'), 'fwhm': med('fwhm'), 'sky': med('sky'), 'frames': len(ms)}


def screen(m, ref, s):
    """('ok' | 'borderline' | 'bad', reasons). Bad frames are not measured; borderline ones get a note (or the vision model)."""
    bad, border = [], []
    if ref and ref.get('n') and ref['n'] >= 10:
        frac = m['n'] / ref['n']
        if frac < float(s.get('qc_stars_bad', 0.15)):
            bad.append(f"only {m['n']} stars where this field usually shows about {ref['n']:.0f} (cloud, dew or a closed roof?)")
        elif frac < float(s.get('qc_stars_low', 0.45)):
            border.append(f"fewer stars than usual ({m['n']} of about {ref['n']:.0f}): thin cloud or haze?")
    elif m['n'] == 0:
        bad.append('no stars found (cloud, dew or a closed roof?)')
    elif m['n'] < 3:
        border.append(f"only {m['n']} star{'s' if m['n'] != 1 else ''} found")
    el = m.get('elong')
    if el is not None and el > float(s.get('qc_elong_bad', 2.2)):
        bad.append(f'stars trailed (elongation {el:.2f}): tracking, guiding or wind')
    elif el is not None and el > float(s.get('qc_elong_max', 1.6)):
        border.append(f'stars elongated ({el:.2f})')
    if ref and ref.get('fwhm') and m.get('fwhm') and m['fwhm'] > 2.5 * ref['fwhm']:
        border.append(f"stars {m['fwhm'] / ref['fwhm']:.1f}x wider than usual (focus or seeing)")
    if ref and ref.get('sky') and ref['sky'] > 0 and m.get('sky') is not None and m['sky'] > 4 * ref['sky'] and m['sky'] - ref['sky'] > 5:
        border.append(f"sky {m['sky'] / ref['sky']:.0f}x brighter than usual (twilight, Moon or lit cloud)")
    return ('bad' if bad else 'borderline' if border else 'ok'), bad + border


def thumbnail(img, path, size=640):
    """A stretched preview (asinh) of the frame, for the vision model."""
    from PIL import Image
    step = max(1, int(max(img.shape) / (size * 2)))
    d = np.asarray(img[::step, ::step], dtype=np.float64)
    d = np.where(np.isfinite(d), d, np.nanmedian(d))
    lo, hi = np.percentile(d, [5, 99.7])
    x = np.clip((d - lo) / max(hi - lo, 1e-9), 0, 1)
    x = np.arcsinh(10 * x) / np.arcsinh(10)
    im = Image.fromarray((x * 255).astype(np.uint8)[::-1])        # FITS rows run bottom to top
    im.thumbnail((size, size))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


VISION_PROMPT = ('This is a stretched astronomical CCD image of a star field taken for variable-star photometry. Decide whether it is '
                 'fit for photometry. Reply as JSON: {"verdict": one of "good", "trailed", "clouds", "satellite_trail", "out_of_focus", '
                 '"wrong_field", "other_problem", "confidence": a number from 0 to 1, "why": "one short sentence"}. '
                 'An automatic check said: ')
VISION_VERDICTS = {'good', 'trailed', 'clouds', 'satellite_trail', 'out_of_focus', 'wrong_field', 'other_problem'}


def ask_vision(llm, thumb_path, reasons):
    """The local vision model's opinion of a borderline frame: {'verdict', 'confidence', 'why'} or None."""
    if not llm or not thumb_path:
        return None
    try:
        r = llm.vision(VISION_PROMPT + '; '.join(reasons), [str(thumb_path)], json_mode=True, max_tokens=200)
    except Exception:
        return None
    if not isinstance(r, dict) or str(r.get('verdict')) not in VISION_VERDICTS:
        return None
    try:
        conf = max(0.0, min(1.0, float(r.get('confidence'))))
    except (TypeError, ValueError):
        conf = 0.5
    return {'verdict': str(r['verdict']), 'confidence': round(conf, 2), 'why': str(r.get('why') or '')[:200]}


# ------------------------------------------------------------------ comparison-star health
def comp_residuals(comps, used, zp0, wsum, wzsum, ident, colour):
    """Each comparison star's (measured - chart) magnitude against the others (leave-one-out for ensemble members), with any
    colour trend across the frame removed (that is a colour term, not a bad star). Returns ({id: [res, err, colour]}, slope)."""
    out, rows = {}, []
    for c in comps:
        z = c['cat'][0] - c['m']
        if any(c is u for u in used) and wsum - c['w'] > 0:
            zp = (wzsum - c['w'] * z) / (wsum - c['w'])          # the ensemble without this star
        else:
            zp = zp0
        res = (c['m'] + zp) - c['cat'][0]
        err = math.sqrt(c['e'] ** 2 + c['cat'][1] ** 2 + 0.003 ** 2)
        rows.append([ident(c), res, err, colour(c)])
    slope = None
    pts = [(r[3], r[1]) for r in rows if r[3] is not None]
    if len(pts) >= 5:
        ci, rs = np.array(pts).T
        keep = np.ones(len(ci), bool)
        for _ in range(3):
            if keep.sum() < 5 or np.ptp(ci[keep]) < 0.3:
                break
            b, a = np.polyfit(ci[keep], rs[keep], 1)
            d = rs - (a + b * ci)
            sd = 1.4826 * float(np.median(np.abs(d[keep]))) or 0.01
            new = np.abs(d) < max(3 * sd, 0.03)
            if new.sum() == keep.sum():
                slope = float(b)
                break
            keep = new
            slope = float(b)
        if slope is not None:
            c0 = float(np.mean(ci))
            for r in rows:
                if r[3] is not None:
                    r[1] -= slope * (r[3] - c0)
    for k, res, err, ci in rows:
        out[k] = [round(res, 4), round(err, 4), None if ci is None else round(ci, 3)]
    return out, slope


def comp_health(con, s, now=None, days=45):
    """Comparison stars that vary or sit off their chart value, from every frame of the last weeks. Returns new flags."""
    now = now or time.time()
    jd0 = now / 86400 + 2440587.5 - days
    try:
        rows = db.rows(con, "SELECT night, filter, chart, target, comp_res FROM vs_meas WHERE comp_res IS NOT NULL AND jd > ? "
                            "AND status != 'rejected'", (jd0,))
    except Exception:
        return []
    acc = {}
    for r in rows:
        try:
            cr = json.loads(r['comp_res'])
        except (TypeError, ValueError):
            continue
        for auid, v in cr.items():
            a = acc.setdefault((auid, r['filter']), {'nights': {}, 'errs': [], 'chart': r['chart'], 'target': r['target']})
            a['nights'].setdefault(r['night'], []).append(float(v[0]))
            a['errs'].append(float(v[1]))
    flags = db.kv_get(con, 'vs_comp_flags') or {}
    new = []
    var_lim, off_lim = float(s.get('comp_var_max', 0.04)), float(s.get('comp_offset_max', 0.08))
    for (auid, band), a in acc.items():
        meds = {n: float(np.median(v)) for n, v in a['nights'].items() if len(v) >= 2}
        allv = [x for v in a['nights'].values() for x in v]
        if len(meds) < 2 or len(allv) < 6:
            continue
        per_night = max(1, int(np.median([len(v) for v in a['nights'].values()])))
        e1 = float(np.median(a['errs']))
        enight = e1 / math.sqrt(per_night)
        vals = np.array(list(meds.values()))
        off = float(np.median(vals))
        spread = 1.4826 * float(np.median(np.abs(vals - off))) if len(vals) >= 3 else 0.0
        within = [1.4826 * float(np.median(np.abs(np.array(v) - np.median(v)))) for v in a['nights'].values() if len(v) >= 5]
        kind, why = None, ''
        if len(vals) >= 3 and spread > max(var_lim, 3 * enight):
            kind, why = 'variable', f'its {band} magnitude changes from night to night by {spread:.3f} mag (expected about {enight:.3f})'
        elif within and float(np.median(within)) > max(var_lim, 3 * e1):
            kind, why = 'variable', f'its {band} magnitude scatters by {float(np.median(within)):.3f} mag within a night (expected about {e1:.3f})'
        elif abs(off) > max(off_lim, 3 * enight):
            kind, why = 'offset', (f'it measures {abs(off):.3f} mag {"fainter" if off > 0 else "brighter"} in {band} than its chart value '
                                   f'on {len(meds)} nights')
        if not kind:
            continue
        key = f'{auid}|{band}'
        old = flags.get(key)
        if old and (old.get('cleared') or old.get('kind') == kind):
            continue
        flags[key] = {'auid': auid, 'band': band, 'kind': kind, 'why': why, 'chart': a['chart'], 'star': a['target'],
                      'nights': sorted(meds)[-10:], 'offset': round(off, 3), 'spread': round(spread, 3), 'n': len(allv),
                      'excluded': kind == 'variable' or abs(off) > 2 * off_lim, 'first': (old or {}).get('first') or now, 'at': now,
                      'by': 'measurements', 'cleared': False}
        new.append(flags[key])
    db.kv_set(con, 'vs_comp_flags', flags)
    return new


def _vizier(catalog, ra, dec, radius_deg, columns, row_limit=2000):
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.vizier import Vizier, conf
    for server in VIZIER_SERVERS:
        try:
            conf.server = server
            conf.timeout = 60
            res = Vizier(columns=columns, row_limit=row_limit).query_region(SkyCoord(ra * u.deg, dec * u.deg), radius=radius_deg * u.deg,
                                                                              catalog=catalog)
            return [dict(zip(r.colnames, [r[c] for c in r.colnames])) for r in res[0]] if res else []
        except Exception:
            continue
    raise RuntimeError('VizieR did not answer')


def catalog_check(con, ch, star_name, vizier=None, now=None, max_age_days=60):
    """Known variables among a chart's comparison stars: AAVSO VSX (VizieR B/vsx/vsx) and Gaia DR3's variability flag.
    A VSX variable is excluded at once; a Gaia-flagged star is only watched. Kept 60 days per chart. Returns new flags."""
    now = now or time.time()
    comps = ch.get('comps') or []
    if not comps or not ch.get('chartid'):
        return []
    cache = db.kv_get(con, 'vs_compcat') or {}
    hit = cache.get(ch['chartid'])
    if hit and now - hit.get('t', 0) < max_age_days * 86400:
        return []
    ras, decs = np.radians([c['ra'] for c in comps]), np.radians([c['dec'] for c in comps])
    ra0 = math.degrees(math.atan2(np.sin(ras).mean(), np.cos(ras).mean())) % 360
    dec0 = float(np.degrees(decs).mean())
    rad = max(math.hypot(((c['ra'] - ra0 + 180) % 360 - 180) * math.cos(math.radians(dec0)), c['dec'] - dec0) for c in comps) + 0.02
    q = vizier or _vizier
    try:
        vsx = q('B/vsx/vsx', ra0, dec0, rad, ['Name', 'Type', 'RAJ2000', 'DEJ2000'])
        gmax = max((min(v[0] for v in c['mags'].values()) for c in comps), default=15) + 1.5
        gaia = [g for g in q('I/355/gaiadr3', ra0, dec0, rad, ['RA_ICRS', 'DE_ICRS', 'Gmag', 'VarFlag'], 4000)
                if str(g.get('VarFlag') or '').strip().upper() == 'VARIABLE' and float(g.get('Gmag') or 99) < gmax]
    except Exception:
        return []                                         # try again next time
    dist = lambda c, ra, de: math.hypot(((float(ra) - c['ra'] + 180) % 360 - 180) * math.cos(math.radians(c['dec'])), float(de) - c['dec']) * 3600
    flags = db.kv_get(con, 'vs_comp_flags') or {}
    known, new = {}, []
    for c in comps:
        auid = c.get('auid') or c.get('label')
        v = next((x for x in vsx if x.get('RAJ2000') is not None and dist(c, x['RAJ2000'], x['DEJ2000']) < 3.0), None)
        g = next((x for x in gaia if dist(c, x['RA_ICRS'], x['DE_ICRS']) < 2.0), None)
        if not v and not g:
            continue
        what = f"AAVSO VSX lists it as the variable {str(v['Name']).strip()} ({str(v.get('Type') or '').strip() or 'type unknown'})" if v \
            else 'Gaia DR3 flags it as variable'
        known[auid] = what
        key = f'{auid}|*'
        if key in flags and (flags[key].get('cleared') or flags[key].get('kind') == 'catalog'):
            continue
        flags[key] = {'auid': auid, 'band': '*', 'kind': 'catalog', 'why': what, 'chart': ch['chartid'], 'star': star_name,
                      'label': c.get('label'), 'excluded': bool(v), 'first': now, 'at': now, 'by': 'catalogue', 'cleared': False,
                      'vsx': bool(v)}
        new.append(flags[key])
    cache[ch['chartid']] = {'t': now, 'known': known}
    db.kv_set(con, 'vs_compcat', cache)
    db.kv_set(con, 'vs_comp_flags', flags)
    return new


def excluded(con):
    """Comparison stars left out of every ensemble: {auid: reason}."""
    return {f['auid']: f['why'] for f in (db.kv_get(con, 'vs_comp_flags') or {}).values() if f.get('excluded') and not f.get('cleared')}


def requeue_for(con, auids):
    """Measurements not yet in a file that used one of these comparison stars are measured again (their frames are
    forgotten, so the next pass picks them up). Returns how many frames."""
    if not auids:
        return 0
    frames = set()
    for r in db.rows(con, "SELECT id, frame, comps, kname FROM vs_meas WHERE status IN ('ok','review') AND report IS NULL"):
        try:
            used = set(json.loads(r['comps'] or '[]'))
        except ValueError:
            used = set()
        if used & set(auids) or (r['kname'] in auids):
            frames.add(r['frame'])
    for f in frames:
        con.execute("DELETE FROM vs_meas WHERE frame=? AND status IN ('ok','review') AND report IS NULL", (f,))
        con.execute('DELETE FROM vs_frames WHERE path=?', (f,))
    return len(frames)


def chet_draft(flag, chart=None):
    """A report for the AAVSO sequence team, for the observer to read, adjust and send through CHET. Never sent from here."""
    comp = next((c for c in (chart or {}).get('comps') or [] if (c.get('auid') or c.get('label')) == flag['auid']), None)
    pos = ''
    if comp:
        from .vsobs import dms, hms
        mags = ', '.join(f'{b} = {v[0]:.3f}' for b, v in sorted(comp['mags'].items()))
        pos = f"Position (J2000): {hms(comp['ra'])}, {dms(comp['dec'])}. Chart values: {mags}.\n"
    label = f" (label {flag.get('label') or (comp or {}).get('label')})" if (flag.get('label') or comp) else ''
    if flag['kind'] == 'catalog':
        found = f"{flag['why']}, but it is a comparison star on this chart."
    else:
        nights = ', '.join(flag.get('nights') or [])
        found = (f"In CCD photometry on {len(flag.get('nights') or [])} nights ({nights}), {flag['why']}, measured against an ensemble "
                 f"of the chart's other comparison stars ({flag.get('n')} measurements).")
    caution = ('' if flag['kind'] == 'catalog' else
               'Before sending: check that the star is not near the image edge, a bad column or a brighter neighbour in your images.\n')
    return (f"Subject: Comparison star {flag['auid']}{label} on chart {flag.get('chart') or 'unknown'} for {flag.get('star') or 'unknown'}\n\n"
            f"Star: {flag['auid']}{label}. {pos}"
            f"What I found: {found}\n"
            f"Request: please check this sequence star.\n"
            f"{caution}"
            f"(Measured with ensemble aperture photometry in Space Lab. File this at {CHET_URL}.)\n")


def save_chet(con, flag, chart=None):
    """Write the draft to data/vstars/chet/ and list it for the local page."""
    CHET_DIR.mkdir(parents=True, exist_ok=True)
    name = re.sub(r'[^A-Za-z0-9_-]', '_', f"{flag['auid']}-{flag['band']}")[:60] + '.txt'
    (CHET_DIR / name).write_text(chet_draft(flag, chart))
    lst = [x for x in (db.kv_get(con, 'vs_chet') or []) if x.get('file') != name]
    lst.insert(0, {'file': name, 'path': f'vstars/chet/{name}', 'auid': flag['auid'], 'star': flag.get('star'), 'kind': flag['kind'],
                   'why': flag['why'], 'at': time.time(), 'sent': False})
    db.kv_set(con, 'vs_chet', lst[:100])
    return name


# ------------------------------------------------------------------ zero points, extinction, clouds
def robust_line(x, y, clip=3.0, iters=5):
    """Straight line through (x, y) with outliers left out: (intercept, slope, scatter, kept mask)."""
    x, y = np.asarray(x, float), np.asarray(y, float)
    keep = np.ones(len(x), bool)
    a = b = 0.0
    sd = 0.0
    for _ in range(iters):
        if keep.sum() < 3:
            break
        b, a = np.polyfit(x[keep], y[keep], 1) if np.ptp(x[keep]) > 0 else (0.0, float(np.median(y[keep])))
        d = y - (a + b * x)
        sd = 1.4826 * float(np.median(np.abs(d[keep]))) or 0.005
        new = np.abs(d) < clip * sd
        if new.sum() == keep.sum():
            break
        keep = new
    return float(a), float(b), float(sd), keep


def night_quality(con, night, s, apply=True):
    """Each filter's zero point against airmass for one night: the extinction, the scatter, and the frames that lost light to
    cloud (flagged when they lost more than half, noted when they lost a little). Returns the night's verdict."""
    rows = db.rows(con, 'SELECT id, filter, airmass, zp, status, flags, notes FROM vs_meas WHERE night=? AND zp IS NOT NULL AND airmass IS NOT NULL',
                   (night,))
    out = {'night': night, 'bands': {}, 'photometric': True, 'cloud': 0, 'thick': 0}
    thin, thick = float(s.get('qc_cloud_mag', 0.25)), float(s.get('qc_cloud_bad', 0.75))
    for band in sorted({r['filter'] for r in rows}):
        rs = [r for r in rows if r['filter'] == band]
        x = np.array([r['airmass'] for r in rs])
        y = np.array([r['zp'] for r in rs])
        if len(rs) >= 6 and np.ptp(x) >= 0.15:
            a, b, sd, keep = robust_line(x, y)
            k = -b
        else:
            med = float(np.median(y))
            a, b, k = med, 0.0, None
            sd = 1.4826 * float(np.median(np.abs(y - med))) if len(y) >= 3 else 0.0
        resid = y - (a + b * x)
        info = {'n': len(rs), 'k': None if k is None else round(k, 3), 'scatter': round(sd, 3), 'zp1': round(a + b, 3), 'cloud': 0}
        for r, d in zip(rs, resid):
            if d >= -thin:
                continue
            lost = 1 - 10 ** (0.4 * d)
            info['cloud'] += 1
            out['cloud'] += 1
            if not apply:
                continue
            flags, notes = json.loads(r['flags'] or '[]'), json.loads(r['notes'] or '[]')
            if d < -thick:
                out['thick'] += 1
                msg = f'thick cloud: this frame lost {lost:.0%} of its light'
                if msg not in flags:
                    flags.append(msg)
                st = 'review' if r['status'] == 'ok' else r['status']
            else:
                msg = f'thin cloud (lost {lost:.0%} of the light)'
                if msg not in notes:
                    notes.append(msg)
                st = r['status']
            con.execute('UPDATE vs_meas SET flags=?, notes=?, status=? WHERE id=?', (json.dumps(flags), json.dumps(notes), st, r['id']))
        if (k is not None and not (-0.05 < k < 1.2)) or sd > float(s.get('qc_photometric_scatter', 0.08)):
            out['photometric'] = False
        out['bands'][band] = info
    if rows and out['cloud'] > 0.2 * len(rows):
        out['photometric'] = False
    nq = db.kv_get(con, 'vs_nights') or {}
    nq[night] = dict(out, at=time.time())
    db.kv_set(con, 'vs_nights', dict(sorted(nq.items())[-60:]))
    return out


# ------------------------------------------------------------------ overheads, from the images' own time stamps
def overheads(con, night):
    """Gaps between exposures in one night: downloads (same star and filter), filter changes, slews (another star)."""
    rows = db.rows(con, "SELECT t0, exp, obj, filt FROM vs_frames WHERE night=? AND t0 IS NOT NULL AND exp IS NOT NULL ORDER BY t0", (night,))
    gaps = {'download': [], 'filter': [], 'slew': []}
    for a, b in zip(rows, rows[1:]):
        g = b['t0'] - (a['t0'] + a['exp'])
        if g < 0 or g > 1800:                              # overlapping clocks, or a pause (cloud, a meridian flip after a wait)
            continue
        kind = 'slew' if (a['obj'] or '') != (b['obj'] or '') else 'filter' if (a['filt'] or '') != (b['filt'] or '') else 'download'
        gaps[kind].append(g)
    if len(rows) < 2:
        return None
    span = rows[-1]['t0'] + rows[-1]['exp'] - rows[0]['t0']
    shutter = sum(r['exp'] for r in rows)
    med = lambda v: round(float(np.median(v)), 1) if v else None
    return {'night': night, 'frames': len(rows), 'hours': round(span / 3600, 2), 'open': round(shutter / span, 3) if span > 0 else None,
            'download_s': med(gaps['download']), 'filter_s': med(gaps['filter']), 'slew_s': med(gaps['slew']),
            'n': {k: len(v) for k, v in gaps.items()}, 'slews': len(gaps['slew']),
            'slew_total_min': round(sum(gaps['slew']) / 60, 1)}


def overhead_model(con, nights=10):
    """The planner's readout, filter-change and slew times from the last nights (enough gaps of each kind), or {}."""
    oh = (db.kv_get(con, 'vs_overhead') or {}).get('nights') or {}
    recent = [oh[k] for k in sorted(oh)[-nights:]]
    out = {}
    for key, setting, need in (('download_s', 'readout_s', 10), ('filter_s', 'filter_change_s', 5), ('slew_s', 'overhead_s', 5)):
        vals = [n[key] for n in recent if n.get(key) is not None and (n.get('n') or {}).get(key.split('_')[0], 0) >= 2]
        n_gaps = sum((n.get('n') or {}).get(key.split('_')[0], 0) for n in recent)
        if vals and n_gaps >= need:
            out[setting] = round(float(np.median(vals)), 1)
    if 'filter_change_s' in out and 'readout_s' in out:
        out['filter_change_s'] = max(0.0, round(out['filter_change_s'] - out['readout_s'], 1))   # a filter change also downloads
    return out


def save_overheads(con, night):
    o = overheads(con, night)
    if not o:
        return None
    k = db.kv_get(con, 'vs_overhead') or {}
    nights = k.get('nights') or {}
    nights[night] = o
    k['nights'] = dict(sorted(nights.items())[-30:])
    db.kv_set(con, 'vs_overhead', k)
    k['model'] = overhead_model(con)                     # from the store, which now holds this night
    db.kv_set(con, 'vs_overhead', k)
    return o


# ------------------------------------------------------------------ before the upload: what to check, in plain English
def _numbers(text):
    return re.findall(r'(?<![\w.])[-+]?\d+(?:\.\d+)?', text or '')


def polish(llm, facts, draft, instruction, max_words=110):
    """The local AI rewrites the draft in plain English; refused (draft kept) if it writes a number the facts do not contain,
    runs long, or is not running. Returns (text, 'local AI' | 'template')."""
    if not llm:
        return draft, 'template'
    prompt = (f'{instruction}\nUse only these facts; do not add numbers, names or advice that are not in them.\n\nFACTS:\n{facts}\n\n'
              f'DRAFT:\n{draft}\n\nReply with the rewritten text only.')
    try:
        out = llm.chat(prompt, kind='writer', max_tokens=300, temperature=0.2)
    except Exception:
        out = None
    if not out or not isinstance(out, str):
        return draft, 'template'
    out = out.strip().strip('"')
    allowed = set(_numbers(facts) + _numbers(draft))
    norm = lambda n: n.lstrip('+').rstrip('0').rstrip('.') if '.' in n else n.lstrip('+')
    allowed |= {norm(n) for n in allowed}
    if any(n not in allowed and norm(n) not in allowed for n in _numbers(out)) or len(out.split()) > max_words:
        return draft, 'template'
    return out, 'local AI'


def report_checks(con, s, info, rows, transform_info=None, events=None):
    """What to look at before uploading one night's AAVSO file: [(level, words)] (level 'ok', 'note' or 'check')."""
    checks = []
    stars = sorted({r['target'] for r in rows})
    filts = sorted({r['filter'] for r in rows})
    checks.append(('ok', f"{len(rows)} measurements of {len(stars)} star{'s' if len(stars) != 1 else ''} in {', '.join(filts)}"))
    if info.get('errors'):
        checks.append(('check', f"{info['errors']} format error{'s' if info['errors'] != 1 else ''}: WebObs would refuse the file"))
    elif info.get('warnings'):
        checks.append(('note', f"{info['warnings']} format warning{'s' if info['warnings'] != 1 else ''} (values that look unusual)"))
    else:
        checks.append(('ok', 'the format check passed'))
    nt = (db.kv_get(con, 'vs_nights') or {}).get(info['night'])
    if nt:
        ks = ', '.join(f"{b} {v['k']:.2f}" for b, v in nt['bands'].items() if v.get('k') is not None)
        if nt.get('photometric'):
            checks.append(('ok', 'the night looks photometric' + (f' (extinction {ks} mag per airmass)' if ks else '')))
        else:
            checks.append(('note', 'the night was not photometric (clouds or haze): differential magnitudes are still fine, '
                                   'but the check-star values deserve a look'))
        if nt.get('thick'):
            checks.append(('check', f"{nt['thick']} frame{'s' if nt['thick'] != 1 else ''} lost more than half their light to cloud"))
    ntr = sum(1 for r in rows if r.get('trans'))
    if ntr:
        src = (transform_info or {}).get('source')
        checks.append(('ok', f"{ntr} measurement{'s' if ntr != 1 else ''} colour-transformed"
                             + (f" ({'coefficients measured on standard fields' if src == 'auto' else 'your own coefficients'})" if src else '')))
    elif len(filts) > 1:
        checks.append(('note', 'no colour transformation applied (no coefficients yet)'))
    kd = [abs(r['kdev']) for r in rows if r.get('kdev') is not None]
    if kd:
        rms = math.sqrt(sum(x * x for x in kd) / len(kd))
        checks.append(('ok' if rms <= float(s.get('check_tol', 0.1)) / 2 else 'note', f'check stars agree with their chart values to {rms:.3f} mag (rms)'))
    hi = sum(1 for r in rows if (r.get('airmass') or 0) > 2.0)
    if hi:
        checks.append(('note', f"{hi} measurement{'s' if hi != 1 else ''} above airmass 2"))
    low = sum(1 for r in rows if (r.get('snr') or 1e9) < 50)
    if low:
        checks.append(('note', f"{low} measurement{'s' if low != 1 else ''} with signal-to-noise below 50"))
    for ev in events or []:
        if ev.get('night') == info['night']:
            checks.append(('check', f"{ev['star']}: {ev['words']}"))
    return checks


def summary_text(checks):
    order = {'check': 0, 'note': 1, 'ok': 2}
    lines = [w for lv, w in sorted(checks, key=lambda c: order[c[0]]) if lv != 'ok']
    head = 'Ready to upload.' if not lines else ('Look at this first: ' if any(c[0] == 'check' for c in checks) else 'Good to upload; note: ')
    body = '; '.join(lines)
    good = '; '.join(w for lv, w in checks if lv == 'ok')
    return (head + (body + '.' if body else '') + (f' Checked: {good}.' if good else '')).strip()
