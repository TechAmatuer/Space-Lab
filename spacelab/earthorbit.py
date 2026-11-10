"""Around Earth: where the space stations, the big telescopes, the Earth watchers and the satellite swarms are (idea 29).

From CelesTrak's current orbit data (the same source as the ISS passes), every Sky watch run writes one file for the page:
- notable spacecraft: one full orbit from now (240 positions, km, in a frame fixed to the stars), its period and what it does;
- swarms (GPS, Galileo, weather and communication satellites on the geostationary ring, a sample of Starlink): one position
  now, the axis it circles around and its period, so the page can move them along.
Positions are good to a few km over a day, plenty for a picture. Nothing here depends on where the visitor is.
"""
import json
import math
import time
from datetime import datetime, timezone

import numpy as np

from . import config, net

CELESTRAK = 'https://celestrak.org/NORAD/elements/gp.php'
OUT = config.PLOTS / 'earth' / 'orbits.json'
N_ORBIT = 240
STARLINK_SAMPLE = 400
R_EARTH = 6371.0

# NORAD number, key, name, group, what it does, Wikipedia title, a word CelesTrak's name for it must contain
NOTABLE = [
    (25544, 'iss', 'International Space Station', 'stations', 'Home to people from many countries since 2000; a laboratory where astronauts test how bodies, plants and materials behave without weight.', 'International Space Station', 'ISS'),
    (48274, 'tiangong', 'Tiangong', 'stations', "China's space station, crewed since 2021.", 'Tiangong space station', 'CSS'),
    (20580, 'hubble', 'Hubble Space Telescope', 'telescopes', 'The visible-light telescope above the air since 1990; it still takes some of the sharpest pictures of the universe.', 'Hubble Space Telescope', 'HST'),
    (25867, 'chandra', 'Chandra X-ray Observatory', 'telescopes', 'Sees the X-rays of black holes, exploded stars and hot gas between galaxies. Its stretched orbit reaches a third of the way to the Moon.', 'Chandra X-ray Observatory', 'CXO'),
    (25989, 'xmm', 'XMM-Newton', 'telescopes', "ESA's X-ray telescope, on a long, stretched orbit since 1999.", 'XMM-Newton', 'XMM'),
    (33053, 'fermi', 'Fermi', 'telescopes', 'Watches the whole sky in gamma rays, the most energetic light there is.', 'Fermi Gamma-ray Space Telescope', 'GLAST'),
    (38358, 'nustar', 'NuSTAR', 'telescopes', 'Focuses high-energy X-rays, from black holes and the remains of exploded stars.', 'NuSTAR', 'NUSTAR'),
    (49954, 'ixpe', 'IXPE', 'telescopes', 'Measures how X-rays are polarised, which shows the shape of magnetic fields around black holes and neutron stars.', 'IXPE', 'IXPE'),
    (57800, 'xrism', 'XRISM', 'telescopes', 'Japanese-American X-ray telescope that measures the speed and make-up of hot gas.', 'X-Ray Imaging and Spectroscopy Mission', 'XRISM'),
    (63182, 'spherex', 'SPHEREx', 'telescopes', 'Maps the whole sky in 102 infrared colours, to learn about the first moment of the universe and the ices in star nurseries.', 'SPHEREx', 'SPHEREX'),
    (25994, 'terra', 'Terra', 'earth', 'One of the longest-working Earth watchers: forests, fires, clouds and ice since 1999.', 'Terra (satellite)', 'TERRA'),
    (27424, 'aqua', 'Aqua', 'earth', "Measures Earth's water: rain, sea ice, clouds and ocean temperature.", 'Aqua (satellite)', 'AQUA'),
    (49260, 'landsat9', 'Landsat 9', 'earth', 'Pictures of every piece of land every 16 days, part of a record going back to 1972.', 'Landsat 9', 'LANDSAT'),
    (40697, 'sentinel2a', 'Sentinel-2A', 'earth', "Europe's colour pictures of land and coasts, free for anyone.", 'Sentinel-2', 'SENTINEL'),
    (43013, 'noaa20', 'NOAA-20', 'weather', 'Passes over the poles and measures the air for weather forecasts.', 'NOAA-20', 'NOAA 20'),
    (60133, 'goes19', 'GOES-19', 'weather', "Hangs over the Americas on the geostationary ring and films the weather every few minutes (GOES-East).", 'GOES-19', 'GOES 19'),
    (51850, 'goes18', 'GOES-18', 'weather', 'The western partner of GOES-19, watching the Pacific (GOES-West).', 'GOES-18', 'GOES 18'),
]
GROUPS = [('stations', 'Space stations'), ('telescopes', 'Telescopes around Earth'), ('earth', 'Watching the Earth'),
          ('weather', 'Weather'), ('navigation', 'Navigation (GPS, Galileo)'), ('geo', 'Geostationary ring'), ('starlink', 'Starlink internet')]
SWARMS = [('gps-ops', 'navigation', 'GPS'), ('galileo', 'navigation', 'Galileo'), ('geo', 'geo', None), ('starlink', 'starlink', None)]


def _sat(o):
    from sgp4.api import Satrec, WGS72
    from sgp4 import omm
    s = Satrec()
    omm.initialize(s, o)
    return s


def _pos(sat, t):
    from sgp4.api import jday
    dt = datetime.fromtimestamp(t, timezone.utc)
    jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second + dt.microsecond / 1e6)
    err, r, v = sat.sgp4(jd, fr)
    return (None, None) if err else (np.array(r), np.array(v))


def notable(o, t0):
    """One full orbit from t0 for a CelesTrak record: {'period_s', 'pts': [[x, y, z] km x N_ORBIT]}, or None."""
    mm = float(o['MEAN_MOTION'])                         # revolutions per day
    period = 86400.0 / mm
    sat = _sat(o)
    pts = []
    for k in range(N_ORBIT):
        r, _ = _pos(sat, t0 + period * k / N_ORBIT)
        if r is None:
            return None
        pts.append([round(float(x), 1) for x in r])
    return {'period_s': round(period, 1), 'pts': pts}


def swarm_point(o, t0):
    """[x, y, z, nx, ny, nz, period_min]: where it is now and the axis it turns around (for a near-circular orbit)."""
    sat = _sat(o)
    r, v = _pos(sat, t0)
    if r is None:
        return None
    n = np.cross(r, v)
    n /= np.linalg.norm(n)
    return [round(float(x), 0) for x in r] + [round(float(x), 4) for x in n] + [round(1440.0 / float(o['MEAN_MOTION']), 2)]


def build(log, fetch=None, now=None, rng=None):
    """Write the file for the page. Returns {'made', 'n', 'n_swarm', 'json'} or None when CelesTrak gave too little."""
    fetch = fetch or (lambda params: net.fetch_json(CELESTRAK, params=params, timeout=120))
    t0 = float(now or time.time())
    rng = rng or np.random.default_rng(int(t0 // 86400))
    objs, left = [], []
    for nid, key, name, group, what, wiki, expect in NOTABLE:
        try:
            js = fetch({'CATNR': nid, 'FORMAT': 'json'})
            o = js[0] if isinstance(js, list) and js else None
            if not o or expect.upper() not in str(o.get('OBJECT_NAME', '')).upper():
                left.append(name)
                continue
            orb = notable(o, t0)
            if not orb:
                left.append(name)
                continue
            r = np.linalg.norm(orb['pts'], axis=1)
            objs.append({'key': key, 'name': name, 'group': group, 'what': what, 'wiki': wiki, 'norad': nid,
                         'perigee_km': round(float(r.min()) - R_EARTH), 'apogee_km': round(float(r.max()) - R_EARTH), **orb})
        except Exception as e:
            left.append(name)
            log(f'  {name}: left out ({type(e).__name__}: {str(e)[:80]})')
    swarms = {}
    for grp, kind, label in SWARMS:
        try:
            js = fetch({'GROUP': grp, 'FORMAT': 'json'})
            js = [o for o in js if isinstance(o, dict) and o.get('MEAN_MOTION')]
            total = len(js)
            if grp == 'starlink' and len(js) > STARLINK_SAMPLE:
                js = [js[i] for i in sorted(rng.choice(len(js), STARLINK_SAMPLE, replace=False))]
            pts = [p for p in (swarm_point(o, t0) for o in js) if p]
            swarms.setdefault(kind, {'total': 0, 'pts': [], 'names': []})
            swarms[kind]['total'] += total
            swarms[kind]['pts'] += pts
            if label:
                swarms[kind]['names'].append(f'{label} ({total})')
        except Exception as e:
            log(f'  swarm {grp}: left out ({type(e).__name__}: {str(e)[:80]})')
    if len(objs) < 5:
        log(f'  around Earth: only {len(objs)} spacecraft placed; the file was left as it was')
        return None
    full = {'t0': t0, 'made': time.time(), 'objects': objs, 'swarms': swarms, 'groups': GROUPS, 'left_out': left,
            'source': 'Orbits: CelesTrak (US Space Force data), propagated with SGP4. Earth to scale; spacecraft are not.'}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix('.tmp')
    tmp.write_text(json.dumps(full, separators=(',', ':')))
    tmp.replace(OUT)
    n_sw = sum(len(s['pts']) for s in swarms.values())
    log(f'  around Earth: {len(objs)} spacecraft, {n_sw} swarm satellites' + (f'; left out: {", ".join(left)}' if left else ''))
    return {'made': full['made'], 'n': len(objs), 'n_swarm': n_sw, 'json': str(OUT.relative_to(config.DATA)), 'left_out': left}
