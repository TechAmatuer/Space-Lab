"""Variable-star observatory, part 4: colour transformation coefficients, measured on standard fields (see VARIABLE_STARS.md).

Magnitudes through the observer's own filters differ slightly from the standard Johnson-Cousins system, and the difference
depends on the star's colour. The AAVSO asks for transformed magnitudes when possible (TRANS=YES). The coefficients come
from a standard field, a patch of sky whose stars have accurate B, V, R and I magnitudes:
1. Once a month (and on clear nights until there are measured coefficients) the planner adds a standard field to tonight's
   plan: M67 or NGC 7790, or one the observer lists in settings ("standard_fields").
2. The photometry job recognises those images (by OBJECT name or pointing), gets the field's standard stars from the AAVSO
   Variable Star Plotter (special=std_field) and measures every one of them.
3. Each night's coefficients are fitted the way the AAVSO's Transform Generator does it: straight lines, with outliers left
   out, of instrumental against standard colours (Tbv, Tvr, Tvi, Tri) and of standard minus instrumental magnitude against
   standard colour (Tv_bv, Tb_bv, Tr_vr, Ti_vi, ...).
4. Two nights that agree start the measured set; later nights that agree are averaged in (the last five). A night that
   disagrees (new filters, camera or optics?) is held for the observer to accept or reject on the local page.
Who decides: with no coefficients of their own in settings, the measured ones are used as soon as they exist. An observer
who already has coefficients ("transform") keeps them, unless "transform_auto" is true; "transform_auto": false switches the
measuring off altogether.
"""
import json
import math
import re
import time

import numpy as np

from . import db, net

STANDARD_FIELDS = [{'name': 'M67', 'ra': 132.846, 'dec': 11.817},        # open cluster in Cancer: winter and spring
                   {'name': 'NGC 7790', 'ra': 359.601, 'dec': 61.208}]   # open cluster in Cassiopeia: summer and autumn
PAIRS = {'B': [('B', 'V')], 'V': [('B', 'V'), ('V', 'R')], 'R': [('V', 'R'), ('R', 'I')], 'I': [('V', 'I'), ('R', 'I')]}
COLOUR_PAIRS = [('B', 'V'), ('V', 'R'), ('V', 'I'), ('R', 'I')]


def fields(s):
    """The built-in standard fields plus the observer's own ({"name", "ra", "dec"} in degrees)."""
    out = list(STANDARD_FIELDS)
    for f in s.get('standard_fields') or []:
        try:
            out.append({'name': str(f['name']).strip(), 'ra': float(f['ra']) % 360, 'dec': float(f['dec'])})
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _norm(x):
    return re.sub(r'[\s_\-]', '', str(x or '')).lower()


def which_field(hdr, s, ra=None, dec=None):
    """The standard field an image shows (by OBJECT name, else by pointing within 0.25 degrees), or None."""
    nm = _norm(hdr.get('OBJECT'))
    fs = fields(s)
    for f in fs:
        if nm and nm == _norm(f['name']):
            return f
    if ra is not None and dec is not None:
        for f in fs:
            if math.hypot(((f['ra'] - ra + 180) % 360 - 180) * math.cos(math.radians(dec)), f['dec'] - dec) < 0.25:
                return f
    return None


def std_chart(field, fov_arcmin, maglimit, fetch=None, max_age_days=90):
    """The field's standard stars from the AAVSO Variable Star Plotter (special=std_field); the ordinary comparison-star
    sequence of that position if the plotter has no standard stars there. Kept 90 days."""
    from . import vsphot
    vsphot.CHARTS.mkdir(parents=True, exist_ok=True)
    p = vsphot.CHARTS / f"std-{vsphot.slug(field['name'])}.json"
    if p.exists() and time.time() - p.stat().st_mtime < max_age_days * 86400:
        return json.loads(p.read_text())
    fov = next((f for f in vsphot.FOVS if f >= fov_arcmin), vsphot.FOVS[-1])
    get = fetch or (lambda q: net.fetch_json(vsphot.VSP_API, params=q, timeout=90))
    base = {'ra': round(field['ra'], 5), 'dec': round(field['dec'], 5), 'fov': fov, 'maglimit': round(float(maglimit), 1), 'format': 'json'}
    out = vsphot.parse_chart(get(dict(base, special='std_field')))
    out['source'] = 'AAVSO standard field'
    if len(out['comps']) < 10:
        seq = vsphot.parse_chart(get(base))
        if len(seq['comps']) > len(out['comps']):
            out = dict(seq, source='AAVSO comparison-star sequence (no standard stars listed here)')
    out['fov'] = fov
    if out['comps']:
        p.write_text(json.dumps(out))
    return out


def measure_field(sky, hdr, to_pix, ch, s, snr_min=30.0):
    """Instrumental magnitudes (per second) of the standard stars in one frame: [{'id', 'm', 'e'}]. Only stars inside the
    camera's linear range, with signal-to-noise of at least 30 and no other standard star within three apertures."""
    from . import vsphot
    comps = ch.get('comps') or []
    if not comps:
        return []
    fwhm, r = vsphot.aperture(sky, s)
    x, y = to_pix(np.array([c['ra'] for c in comps]), np.array([c['dec'] for c in comps]))
    got = vsphot.photometry(sky, hdr, s, np.asarray(x, float), np.asarray(y, float), fwhm, r)
    out = []
    pts = {i: (o['x'], o['y']) for i, o in got.items()}
    for i, o in got.items():
        if o['peak'] >= sky['lin'] or o['snr'] < snr_min:
            continue
        if any(j != i and math.hypot(o['x'] - p[0], o['y'] - p[1]) < 3 * r for j, p in pts.items()):
            continue
        out.append({'id': comps[i].get('auid') or comps[i].get('label'), 'm': round(o['m'], 5), 'e': round(o['e'], 5)})
    return out


# ------------------------------------------------------------------ fitting one night
def _line(x, y):
    """Robust straight line with the slope's uncertainty: (slope, error, stars used, colour range) or None."""
    from .vsqc import robust_line
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) < 8:
        return None
    a, b, sd, keep = robust_line(x, y, clip=2.5)
    xs = x[keep]
    if keep.sum() < 8 or np.ptp(xs) < 0.5:
        return None
    sxx = float(np.sum((xs - xs.mean()) ** 2))
    resid = y[keep] - (a + b * xs)
    s2 = float(np.sum(resid ** 2) / max(1, keep.sum() - 2))
    return b, math.sqrt(s2 / sxx) if sxx > 0 else 1.0, int(keep.sum()), float(np.ptp(xs))


def fit(inst, cat):
    """Coefficients from averaged instrumental magnitudes inst {filter: {id: m}} and standard magnitudes cat {filter: {id: M}}
    (filters as AAVSO codes B, V, R, I). Returns {name: [value, error, stars]}."""
    out = {}
    for a, b in COLOUR_PAIRS:
        if a not in inst or b not in inst or a not in cat or b not in cat:
            continue
        ids = [k for k in inst[a] if k in inst[b] and k in cat[a] and k in cat[b]]
        if len(ids) < 8:
            continue
        X = np.array([cat[a][k] - cat[b][k] for k in ids])
        lo, hi = a.lower(), b.lower()
        r = _line(X, [inst[a][k] - inst[b][k] for k in ids])
        if r and abs(r[0]) > 0.3:
            out[f'T{lo}{hi}'] = [round(1 / r[0], 4), round(r[1] / r[0] ** 2, 4), r[2]]        # Tbv = 1 / slope of (b-v) against (B-V)
        for f in (a, b):
            r = _line(X, [cat[f][k] - inst[f][k] for k in ids])
            if r:
                out[f'T{f.lower()}_{lo}{hi}'] = [round(r[0], 4), round(r[1], 4), r[2]]
    return out


def night_sets(con, night, s):
    """One coefficient set per standard field observed in this night: each frame is first put on its own zero point (so
    clouds or airmass between frames cannot bend the lines), then every star is averaged per filter."""
    from . import vsphot
    rows = db.rows(con, 'SELECT * FROM vs_std WHERE night=?', (night,))
    out = []
    for name in sorted({r['field'] for r in rows}):
        rs = [r for r in rows if r['field'] == name]
        p = vsphot.CHARTS / f'std-{vsphot.slug(name)}.json'
        if not p.exists():
            continue
        ch = json.loads(p.read_text())
        cat = {}
        for code in {r['filter'] for r in rs}:
            band = vsphot.chart_band(s, code)
            cat[code] = {(c.get('auid') or c.get('label')): c['mags'][band][0] for c in ch['comps'] if band in c['mags']}
        acc = {}
        for r in rs:
            stars = json.loads(r['stars'] or '[]')
            ref = cat.get(r['filter']) or {}
            offs = [ref[x['id']] - x['m'] for x in stars if x['id'] in ref]
            if len(offs) < 5:
                continue
            z = float(np.median(offs))
            for x in stars:
                acc.setdefault(r['filter'], {}).setdefault(x['id'], []).append(x['m'] + z)
        inst = {f: {k: float(np.median(v)) for k, v in d.items()} for f, d in acc.items()}
        coefs = fit(inst, cat)
        out.append({'night': night, 'field': name, 'coefs': coefs, 'frames': len(rs), 'filters': sorted(inst),
                    'stars': max((len(v) for v in inst.values()), default=0), 'source': ch.get('source'), 'at': time.time(),
                    'state': 'pending' if coefs else 'empty'})
    return out


# ------------------------------------------------------------------ which coefficients are in use
def _tol(name, s):
    colour = '_' not in name
    return float(s.get('transform_tol_colour' if colour else 'transform_tol', 0.03 if colour else 0.02))


def agree(a, b, s):
    """True when every coefficient two sets share agrees within the tolerance (or three times their combined error)."""
    common = set(a) & set(b)
    if not common:
        return False
    return all(abs(a[k][0] - b[k][0]) <= max(_tol(k, s), 3 * math.hypot(a[k][1], b[k][1])) for k in common)


def combine(sets):
    """Weighted mean of each coefficient over these sets: {name: [value, error, sets]}."""
    acc = {}
    for st in sets:
        for k, (v, e, _n) in st['coefs'].items():
            acc.setdefault(k, []).append((v, max(e, 0.002)))
    out = {}
    for k, ve in acc.items():
        w = np.array([1 / e ** 2 for _, e in ve])
        out[k] = [round(float(np.sum(w * [v for v, _ in ve]) / w.sum()), 4), round(float(1 / math.sqrt(w.sum())), 4), len(ve)]
    return out


def record(con, s, new_sets):
    """Add a night's sets to the history and decide: used (agrees), held (disagrees with the sets in use), pending (waiting
    for a second night). Returns the sets just held."""
    hist = db.kv_get(con, 'vs_tsets') or []
    held = []
    for st in new_sets:
        old = next((h for h in hist if h['night'] == st['night'] and h['field'] == st['field']), None)
        if old:                                           # the night was fitted again (more frames came in): decide afresh
            if old.get('state') in ('rejected', 'superseded'):
                continue
            hist.remove(old)
            for h in hist:
                if h.get('state') == 'used' and old.get('state') == 'used' and not [x for x in hist if x.get('state') == 'used' and x is not h]:
                    h['state'] = 'pending'               # its partner goes back to waiting for a second night
        if st['state'] == 'empty':
            hist.append(st)
            continue
        used = [h for h in hist if h.get('state') == 'used']
        if used:
            st['state'] = 'used' if agree(st['coefs'], combine(used[-5:]), s) else 'held'
            if st['state'] == 'held':
                held.append(st)
        else:
            pend = [h for h in hist if h.get('state') == 'pending']
            if pend and agree(st['coefs'], pend[-1]['coefs'], s):
                pend[-1]['state'] = st['state'] = 'used'
        hist.append(st)
    db.kv_set(con, 'vs_tsets', hist[-60:])
    return held


def decide(con, night, field, accept):
    """The observer accepts (a new start: earlier sets are set aside) or rejects a held set."""
    hist = db.kv_get(con, 'vs_tsets') or []
    st = next((h for h in hist if h['night'] == night and h['field'] == field and h.get('state') == 'held'), None)
    if not st:
        return False
    if accept:
        for h in hist:
            if h.get('state') in ('used', 'pending'):
                h['state'] = 'superseded'
        st['state'] = 'used'
    else:
        st['state'] = 'rejected'
    db.kv_set(con, 'vs_tsets', hist)
    return True


def spec_from(coefs, codes):
    """Settings-style transformation for each filter: {'V': ['B', 'V', Tv_bv, Tbv], ...} (filter, colour pair,
    transformation coefficient, colour coefficient), from the first colour pair with both coefficients."""
    out = {}
    for f in codes:
        for a, b in PAIRS.get(f, []):
            tf, tc = f'T{f.lower()}_{a.lower()}{b.lower()}', f'T{a.lower()}{b.lower()}'
            if tf in coefs and tc in coefs:
                out[f] = [a, b, coefs[tf][0], coefs[tc][0]]
                break
    return out


def auto_on(s):
    """Measure and use coefficients from standard fields? Yes, unless the observer has their own (then only with transform_auto
    true), and never with transform_auto false."""
    a = s.get('transform_auto')
    return (not s.get('transform')) if a is None else bool(a)


def active(con, s):
    """The transformation in use: {'source': 'auto' | 'manual' | None, 'spec': {...}, 'coefs', 'sets', 'since'}."""
    manual = s.get('transform') or {}
    if auto_on(s):
        used = [h for h in (db.kv_get(con, 'vs_tsets') or []) if h.get('state') == 'used']
        if used:
            coefs = combine(used[-5:])
            spec = spec_from(coefs, [f.get('aavso') for f in s.get('filters') or []])
            if spec:
                return {'source': 'auto', 'spec': spec, 'coefs': coefs, 'sets': len(used[-5:]), 'since': used[-5:][0]['night'],
                        'latest': used[-1]['night']}
    return {'source': 'manual' if manual else None, 'spec': manual, 'coefs': None, 'sets': 0}


def due(con, s, now=None):
    """Why a standard field should be observed tonight, or None: no measured set yet, or the last one is older than
    transform_every_days (30)."""
    if not auto_on(s):
        return None
    now = now or time.time()
    try:
        last = con.execute('SELECT MAX(jd) FROM vs_std').fetchone()[0]
    except Exception:
        last = None
    age = (now / 86400 + 2440587.5 - last) if last else None
    if active(con, s)['source'] != 'auto':
        if age is not None and age < 0.75:
            return None
        return 'no measured transformation coefficients yet: a standard field on two clear nights gives the first set'
    every = float(s.get('transform_every_days', 30))
    if age is None or age > every:
        return f'the last standard field was {age or 0:.0f} days ago (every {every:g} days keeps the coefficients current)'
    return None
