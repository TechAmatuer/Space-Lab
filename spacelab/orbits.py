"""Orbital elements from JPL's Small-Body Database, cached for a day (for the 3D orbit views)."""
import time

from . import db, net

SBDB = 'https://ssd-api.jpl.nasa.gov/sbdb.api'


def elements(con, des, max_age=86400):
    key = f'orbit:{des}'
    t = db.kv_time(con, key)
    if t and time.time() - t < max_age:
        return db.kv_get(con, key)
    js = net.fetch_json(SBDB, params={'sstr': des}, timeout=60, min_gap=1.0)
    orb = js.get('orbit') or {}
    el = {e['name']: e.get('value') for e in orb.get('elements') or []}
    out = {'des': des, 'name': (js.get('object') or {}).get('fullname') or des, 'epoch': float(orb['epoch']) if orb.get('epoch') else None,
           **{k: float(el[k]) for k in ('e', 'a', 'i', 'om', 'w', 'ma', 'q', 'n') if el.get(k) not in (None, '')}}
    db.kv_set(con, key, out)
    return out
