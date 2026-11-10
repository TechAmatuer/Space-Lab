"""Supernova alerts for nearby galaxies (idea 18 of the plan).

Every Sky watch run (every 3 hours) asks the ALeRCE broker for the newest supernova candidates in ZTF images: the ones its
picture classifier flags from the very first image, and the ones its light-curve classifier calls Type Ia, II or Ib/c.
Each is matched to the galaxies of the 3D map (2MASS Redshift Survey, distances as on the map). A candidate within about
30,000 light-years of a galaxy's centre (projected), or within a minute of arc for distant galaxies, belongs to it.
Candidates in galaxies closer than NEAR_MPC (about 130 million light-years) are sent as an alert and become a task in
Meridian: supernovae that close can get bright enough for amateur telescopes and are worth following up within hours.
These are machine-classified candidates, not confirmed supernovae: confirmation needs a spectrum.
"""
import math
import time

import numpy as np

from . import db, net

ALERCE = 'https://api.alerce.online/ztf/v1/objects/'
NEAR_MPC = 40.0          # alert (about 130 million light-years)
SHOW_MPC = 100.0         # listed on the page
KEEP_DAYS = 60
HOST_KPC = 30.0          # projected distance from the galaxy's centre that still counts as "in" it
MIN_SEP, MAX_SEP = 60.0, 600.0    # arcsec
QUERIES = [
    {'classifier': 'stamp_classifier', 'class_name': 'SN', 'probability': 0.5},     # from the first image: the earliest warning
    {'classifier': 'lc_classifier', 'class_name': 'SNIa', 'probability': 0.5},
    {'classifier': 'lc_classifier', 'class_name': 'SNII', 'probability': 0.5},
    {'classifier': 'lc_classifier', 'class_name': 'SNIbc', 'probability': 0.5},
]
KIND = {'SN': 'a supernova (from its first images)', 'SNIa': 'a Type Ia supernova', 'SNII': 'a Type II supernova', 'SNIbc': 'a Type Ib/c supernova'}


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def candidates(fetch=None, now_mjd=None, days=21):
    """The newest candidates from ALeRCE (first seen within `days`), one entry per object with its best classification."""
    fetch = fetch or (lambda p: net.fetch_json(ALERCE, params=p, timeout=60))
    now_mjd = now_mjd or time.time() / 86400 + 40587
    out = {}
    for q in QUERIES:
        try:
            js = fetch(dict(q, page_size=100, order_by='lastmjd', order_mode='DESC'))
        except Exception:
            continue
        for o in (js.get('items') if isinstance(js, dict) else js) or []:
            oid, ra, dec, first = o.get('oid'), _f(o.get('meanra')), _f(o.get('meandec')), _f(o.get('firstmjd'))
            if not oid or ra is None or dec is None or first is None or now_mjd - first > days:
                continue
            p = _f(o.get('probability')) or 0
            if oid not in out or p > out[oid]['prob']:
                out[oid] = {'id': oid, 'ra': ra, 'dec': dec, 'first_mjd': first, 'last_mjd': _f(o.get('lastmjd')), 'prob': round(p, 3),
                            'kind': q['class_name'], 'url': f'https://alerce.online/object/{oid}'}
    return list(out.values())


def load_galaxies():
    """The 3D map's galaxies: unit vectors, distances (Mpc) and names, from the files the universe job wrote."""
    from .universe import SITE_DIR, read_bin
    b, t = SITE_DIR / 'galaxies.bin', SITE_DIR / 'galaxies-names.txt'
    if not t.exists():
        t = SITE_DIR / 'galaxies.txt'
    if not b.exists() or not t.exists():
        return None
    g = read_bin(b, t)
    xyz = np.asarray(g['xyz'], float)
    d = np.linalg.norm(xyz, axis=1)
    return {'u': xyz / np.maximum(d, 1e-9)[:, None], 'd': d,
            'name': [nm or ('2MASX J' + i) for nm, i in zip(g['names'], g['ids'])]}


def match(c, gal):
    """The galaxy a candidate is in, or None: {'host', 'dist', 'sep', 'kpc'}."""
    a, d = math.radians(c['ra']), math.radians(c['dec'])
    v = np.array([math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)])
    cos = gal['u'] @ v
    sep = np.degrees(np.arccos(np.clip(cos, -1, 1))) * 3600
    lim = np.clip(np.degrees(np.arctan(HOST_KPC / 1000 / np.maximum(gal['d'], 0.1))) * 3600, MIN_SEP, MAX_SEP)
    ok = np.flatnonzero(sep <= lim)
    if not len(ok):
        return None
    i = int(ok[np.argmin(sep[ok] / lim[ok])])
    return {'host': gal['name'][i], 'dist': round(float(gal['d'][i]), 1), 'sep': round(float(sep[i]), 1),
            'kpc': round(float(sep[i] / 3600 * math.pi / 180 * gal['d'][i] * 1000), 1)}


def brightness(oid, fetch=None):
    """Brightest and latest ZTF magnitude of a candidate (for 'can a small telescope see it?')."""
    fetch = fetch or (lambda u: net.fetch_json(u, timeout=60))
    try:
        js = fetch(f'{ALERCE}{oid}/lightcurve')
        det = [x for x in (js.get('detections') or []) if _f(x.get('magpsf')) is not None and _f(x.get('mjd')) is not None]
    except Exception:
        return {}
    if not det:
        return {}
    last = max(det, key=lambda x: x['mjd'])
    return {'mag_last': round(float(last['magpsf']), 2), 'mag_best': round(min(float(x['magpsf']) for x in det), 2),
            'band': {1: 'g', 2: 'r', 3: 'i'}.get(last.get('fid'), '')}


def check(con, cfg, log, prev=None, fetch=None, gal=None, now=None, sink=None, light=None):
    """Match the newest candidates to the map's galaxies; alert once per candidate in a nearby galaxy. Returns the list to show."""
    now = now or time.time()
    now_mjd = now / 86400 + 40587
    gal = gal if gal is not None else load_galaxies()
    if gal is None:
        log('  supernova watch: no galaxy map yet')
        return prev
    cands = candidates(fetch, now_mjd)
    seen = set(db.kv_get(con, 'sn_seen') or [])
    keep = {x['id']: x for x in (prev or []) if now_mjd - (x.get('first_mjd') or 0) <= KEEP_DAYS}
    new_near = 0
    for c in cands:
        m = match(c, gal)
        if not m or m['dist'] > SHOW_MPC:
            continue
        e = dict(keep.get(c['id'], {}), **c, **m)
        if m['dist'] <= NEAR_MPC:
            e.update(brightness(c['id'], light))
            if c['id'] not in seen:
                new_near += 1
                seen.add(c['id'])
                ly = m['dist'] * 3.2616
                mag = f", now magnitude {e['mag_last']} ({e.get('band', '')})" if e.get('mag_last') else ''
                text = (f"Possible supernova {c['id']} in {m['host']}, {m['dist']:.0f} Mpc ({ly:.0f} million light-years) away{mag}. "
                        f"ALeRCE: {KIND.get(c['kind'], c['kind'])}, {c['prob'] * 100:.0f}%. Not confirmed (needs a spectrum).")
                from . import health
                health.send(cfg, 'Space Lab: supernova in a nearby galaxy', text, sink)
                db.add_task(con, f"sn:{c['id']}", 'asteroids', 'review', f"Possible supernova in {m['host']} ({m['dist']:.0f} Mpc)",
                            text + ' Worth a look while it is bright: check TNS for a name and a spectrum.', {'url': c['url']}, 1)
                log(f'  ALERT {text}')
        keep[c['id']] = e
    db.kv_set(con, 'sn_seen', sorted(seen)[-2000:])
    out = sorted(keep.values(), key=lambda x: x['dist'])
    log(f'  supernova watch: {len(cands)} new candidates, {len(out)} in map galaxies within {SHOW_MPC:.0f} Mpc, {new_near} new within {NEAR_MPC:.0f} Mpc')
    return out
