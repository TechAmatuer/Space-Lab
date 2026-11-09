"""Beyond astronomy + sky watch (every 3 hours): free public data, no keys, nothing private.

* Space weather (NOAA SWPC): geomagnetic Kp, storm scales, solar flares, the Sun's radio flux, X-ray light curve,
  aurora chance around 40°N.
* Fireballs seen by US government sensors (NASA/JPL CNEOS), comets near the Sun (JPL Small-Body Database),
  gravitational-wave alerts (LIGO/Virgo/KAGRA GraceDB), new supernova candidates (ALeRCE broker, machine-classified).
* Engineering: upcoming and recent launches (The Space Devs Launch Library 2).
* Biology: potentially habitable worlds (NASA Exoplanet Archive).
* Earth from space: yesterday's whole planet and Earth at night (NASA GIBS), the sunlit disk from 1.5 million km (DSCOVR EPIC).
* The ISS: its path for the next 36 hours (from CelesTrak's orbit data and the sgp4 package), so each visitor's own
  browser can work out passes over them. No location is used here.
* Earth science: magnitude 4.5+ earthquakes this week (USGS).
* Stargazing helpers: upcoming events (meteor showers, eclipses, Moon phases, launches, comets), satellite counts
  (CelesTrak), a dark-sky map of the lower 48 from NASA's night-lights picture, and ZIP code centres (Census Bureau)
  so visitors can find dark skies near them. Their location never leaves their browser.
* This week's sky for everyone (no location): Moon phase, which planets are in the evening/morning sky, meteor showers.
* Community: reviews people post on GitHub (leaderboard and per-candidate consensus).
* Housekeeping: nightly database backup (7 kept), cache clean-up, disk use.
Each part fails on its own; the rest still updates.
"""
import json
import math
import re
import shutil
import sqlite3
import time
import urllib.parse
from datetime import datetime, timedelta, timezone

from . import config, db, net

SWPC = 'https://services.swpc.noaa.gov'
FIREBALL = 'https://ssd-api.jpl.nasa.gov/fireball.api'
SBDB_QUERY = 'https://ssd-api.jpl.nasa.gov/sbdb_query.api'
GRACEDB = 'https://gracedb.ligo.org/api/superevents/'
ALERCE = 'https://api.alerce.online/ztf/v1/objects/'
LL2 = 'https://ll.thespacedevs.com/2.2.0/launch/'
NEA_TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
GH_ISSUES = 'https://api.github.com/repos/{repo}/issues'
GIBS = 'https://gibs.earthdata.nasa.gov/wms/epsg4326/best/wms.cgi'
CELESTRAK = 'https://celestrak.org/NORAD/elements/gp.php'
USGS = 'https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson'
CENSUS_ZCTA = 'https://www2.census.gov/geo/docs/maps-data/data/gazetteer/{y}_Gazetteer/{y}_Gaz_zcta_national.zip'
CONUS = (24.0, -125.0, 50.0, -66.0)          # lat0, lon0, lat1, lon1 of the dark-sky map (the lower 48 states)

# Eclipses worth travelling for in the next few years (dates UTC). Source: NASA eclipse predictions.
ECLIPSES = [('2027-02-06', 'Annular solar eclipse ("ring of fire")', 'Chile, Argentina and western Africa; partial over most of South America'),
            ('2027-08-02', 'Total solar eclipse (over 6 minutes, the longest until 2114)', 'Spain, Morocco, Algeria, Tunisia, Libya, Egypt, Saudi Arabia, Yemen'),
            ('2028-01-26', 'Annular solar eclipse ("ring of fire")', 'Ecuador, Peru and Brazil, ending at sunset in Portugal and Spain'),
            ('2028-07-22', 'Total solar eclipse', 'Australia (including Sydney) and New Zealand'),
            ('2028-12-31', 'Total lunar eclipse (the Moon turns red)', 'Europe, Africa, Asia and Australia'),
            ('2044-08-23', 'Total solar eclipse in the USA', 'Montana and North Dakota (and Canada)'),
            ('2045-08-12', 'Total solar eclipse across the USA', 'coast to coast, from California to Florida')]
EPIC = 'https://epic.gsfc.nasa.gov'

# Major annual meteor showers: (name, peak month, peak day, typical peak rate per hour under dark skies, parent body)
SHOWERS = [('Quadrantids', 1, 3, 110, 'asteroid 2003 EH1'), ('Lyrids', 4, 22, 18, 'comet Thatcher'),
           ('Eta Aquariids', 5, 6, 50, "Halley's comet"), ('Southern Delta Aquariids', 7, 30, 25, 'comet 96P/Machholz (likely)'),
           ('Perseids', 8, 12, 100, 'comet Swift-Tuttle'), ('Draconids', 10, 8, 10, 'comet Giacobini-Zinner'),
           ('Orionids', 10, 21, 20, "Halley's comet"), ('Leonids', 11, 17, 15, 'comet Tempel-Tuttle'),
           ('Geminids', 12, 14, 150, 'asteroid 3200 Phaethon'), ('Ursids', 12, 22, 10, 'comet Tuttle')]


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _rows(js):
    """SWPC 'products' come as a header row + rows (older) or as a list of objects (newer). Return dicts."""
    if isinstance(js, list) and js and isinstance(js[0], list):
        head = js[0]
        return [dict(zip(head, r)) for r in js[1:]]
    return js if isinstance(js, list) else []


# ------------------------------------------------------------------ space weather
def space_weather(log):
    out = {}
    try:
        kp = _rows(net.fetch_json(SWPC + '/products/noaa-planetary-k-index.json', timeout=40))
        out['kp'] = [{'t': r.get('time_tag'), 'kp': _f(r.get('Kp') if 'Kp' in r else r.get('kp_index'))} for r in kp[-24:]]
    except Exception as e:
        log(f'  Kp skipped: {e}')
    try:
        sc = net.fetch_json(SWPC + '/products/noaa-scales.json', timeout=40)
        now = sc.get('0') or {}
        out['scales'] = {k: {'scale': (now.get(k) or {}).get('Scale'), 'text': (now.get(k) or {}).get('Text')} for k in ('R', 'S', 'G')}
    except Exception as e:
        log(f'  storm scales skipped: {e}')
    try:
        fl = net.fetch_json(SWPC + '/json/goes/primary/xray-flares-7-day.json', timeout=40)
        fl = [x for x in fl if x.get('max_class')]
        out['flares'] = [{'peak': x.get('max_time'), 'cls': x.get('max_class')} for x in fl[-15:]][::-1]
    except Exception as e:
        log(f'  flares skipped: {e}')
    try:
        f107 = net.fetch_json(SWPC + '/json/f107_cm_flux.json', timeout=40)
        f107 = [x for x in f107 if _f(x.get('flux'))]
        out['radio'] = [{'t': x.get('time_tag'), 'flux': _f(x.get('flux'))} for x in f107[-30:]]
    except Exception as e:
        log(f'  radio flux skipped: {e}')
    try:
        xr = net.fetch_json(SWPC + '/json/goes/primary/xrays-1-day.json', timeout=60)
        xr = [x for x in xr if x.get('energy') == '0.1-0.8nm' and _f(x.get('flux'))]
        out['xray'] = [[x['time_tag'], _f(x['flux'])] for x in xr[::10]][-144:]
    except Exception as e:
        log(f'  X-ray light curve skipped: {e}')
    try:
        ov = net.fetch_json(SWPC + '/json/ovation_aurora_latest.json', timeout=90)
        best = 0
        for lon, lat, p in ov.get('coordinates') or []:
            lon = lon - 360 if lon > 180 else lon
            if 38 <= lat <= 42 and -125 <= lon <= -65:
                best = max(best, p)
        out['aurora_40n'] = best
        out['aurora_time'] = ov.get('Forecast Time') or ov.get('Observation Time')
    except Exception as e:
        log(f'  aurora skipped: {e}')
    return out


# ------------------------------------------------------------------ small bodies, transients, launches
def fireballs(log):
    try:
        js = net.fetch_json(FIREBALL, params={'limit': 15, 'req-loc': 'true'}, timeout=40)
        f = js.get('fields') or []
        out = []
        for r in js.get('data') or []:
            d = dict(zip(f, r))
            lat = _f(d.get('lat')); lon = _f(d.get('lon'))
            # rounded to 1 decimal: plenty for a map, and can never look like a private coordinate to the privacy scan
            out.append({'date': d.get('date'), 'energy_kt': _f(d.get('impact-e')), 'alt_km': _f(d.get('alt')), 'vel_kms': _f(d.get('vel')),
                        'lat': round(lat * (1 if d.get('lat-dir') == 'N' else -1), 1) if lat is not None else None,
                        'lon': round(lon * (1 if d.get('lon-dir') == 'E' else -1), 1) if lon is not None else None})
        return out
    except Exception as e:
        log(f'  fireballs skipped: {e}')
        return None


def comets(log, now_jd):
    try:
        cdata = json.dumps({'AND': ['q|LT|2.0', f'tp|RG|{now_jd - 150:.1f}|{now_jd + 150:.1f}']})
        js = net.fetch_json(SBDB_QUERY, params={'fields': 'full_name,q,tp,e,M1', 'sb-kind': 'c', 'sb-cdata': cdata}, timeout=60)
        f = js.get('fields') or []
        out = []
        for r in js.get('data') or []:
            d = dict(zip(f, r))
            tp = _f(d.get('tp'))
            out.append({'name': (d.get('full_name') or '').strip(), 'q_au': _f(d.get('q')), 'e': _f(d.get('e')),
                        'm1': _f(d.get('M1')), 'days_to_perihelion': round(tp - now_jd, 1) if tp else None})
        out.sort(key=lambda c: (c['m1'] if c['m1'] is not None else 99))
        return out[:15]
    except Exception as e:
        log(f'  comets skipped: {e}')
        return None


def gravitational_waves(log):
    try:
        js = net.fetch_json(GRACEDB, params={'query': 'far < 3.17e-8', 'count': 10, 'orderby': '-created', 'format': 'json'}, timeout=60)
        out = []
        for s in js.get('superevents') or []:
            labels = s.get('labels') or []
            far = _f(s.get('far'))
            out.append({'id': s.get('superevent_id'), 'created': s.get('created'), 'retracted': 'ADVNO' in labels,
                        'one_per_years': round(1 / (far * 3.156e7)) if far else None,
                        'url': f"https://gracedb.ligo.org/superevents/{s.get('superevent_id')}/view/"})
        return out
    except Exception as e:
        log(f'  gravitational waves skipped: {e}')
        return None


def supernovae(log):
    try:
        js = net.fetch_json(ALERCE, params={'classifier': 'lc_classifier', 'class_name': 'SNIa', 'probability': 0.6,
                                            'page_size': 10, 'order_by': 'lastmjd', 'order_mode': 'DESC'}, timeout=60)
        items = js.get('items') if isinstance(js, dict) else js
        out = []
        for o in items or []:
            out.append({'id': o.get('oid'), 'ra': _f(o.get('meanra')), 'dec': _f(o.get('meandec')),
                        'first_mjd': _f(o.get('firstmjd')), 'last_mjd': _f(o.get('lastmjd')), 'prob': _f(o.get('probability')),
                        'url': f"https://alerce.online/object/{o.get('oid')}"})
        return out
    except Exception as e:
        log(f'  supernova candidates skipped: {e}')
        return None


def launches(log):
    out = {}
    for kind in ('upcoming', 'previous'):
        try:
            js = net.fetch_json(LL2 + kind + '/', params={'limit': 8, 'mode': 'normal'}, timeout=60)
            rows = []
            for L in js.get('results') or []:
                m = L.get('mission') or {}
                rows.append({'name': L.get('name'), 'net': L.get('net'), 'status': (L.get('status') or {}).get('abbrev'),
                             'provider': (L.get('launch_service_provider') or {}).get('name'),
                             'rocket': ((L.get('rocket') or {}).get('configuration') or {}).get('full_name'),
                             'orbit': (m.get('orbit') or {}).get('name'), 'mission': (m.get('description') or '')[:300],
                             'pad': ((L.get('pad') or {}).get('location') or {}).get('name')})
            out[kind] = rows
        except Exception as e:
            log(f'  launches ({kind}) skipped: {e}')
    return out or None


def habitable_worlds(log):
    q = ('select pl_name,hostname,pl_rade,pl_bmasse,pl_eqt,pl_insol,sy_dist,st_teff,st_spectype,disc_year,disc_method '
         'from pscomppars where pl_rade < 2.5 and pl_insol between 0.25 and 1.8 order by sy_dist')
    try:
        txt = net.fetch_text(NEA_TAP, params={'query': q, 'format': 'json'}, timeout=120)
        rows = json.loads(txt)
        for r in rows:
            # rough Earth-similarity from size and sunlight only (1 = Earth); not the full Earth Similarity Index
            rr, s = _f(r.get('pl_rade')), _f(r.get('pl_insol'))
            r['similarity'] = round(math.sqrt((1 - abs((rr - 1) / (rr + 1))) * (1 - abs((s - 1) / (s + 1)))), 2) if rr and s else None
        return rows[:40]
    except Exception as e:
        log(f'  habitable worlds skipped: {e}')
        return None


# ------------------------------------------------------------------ Earth from space (images saved in data/plots/beyond)
def _jpeg(b):
    return bool(b) and len(b) > 20000 and b[:3] == b'\xff\xd8\xff'


EARTH_SETS = [
    ('day', "Yesterday's whole Earth, stitched together from one day of polar-orbiting satellite passes",
     [('VIIRS_SNPP_CorrectedReflectance_TrueColor', True, 'NASA GIBS · VIIRS on Suomi NPP'),
      ('MODIS_Terra_CorrectedReflectance_TrueColor', True, 'NASA GIBS · MODIS on Terra')]),
    ('aerosol', 'Smoke, dust and haze seen from space yesterday (aerosol optical depth: darker orange = hazier air)',
     [('MODIS_Combined_Value_Added_AOD', True, 'NASA GIBS · MODIS Terra + Aqua aerosols'),
      ('MODIS_Terra_Aerosol_Optical_Depth_3km', True, 'NASA GIBS · MODIS Terra aerosols')]),
    ('night', 'Earth at night: city lights seen from orbit',
     [('VIIRS_Black_Marble', False, 'NASA GIBS · Black Marble'),
      ('VIIRS_SNPP_DayNightBand_ENCC', True, 'NASA GIBS · VIIRS Day/Night Band')]),
]


def earth_images(log, prev=None, max_age_h=12):
    """Download a few free NASA Earth images (no key). Refreshed at most every 12 hours."""
    if prev and time.time() - (prev[0].get('t') or 0) < max_age_h * 3600 and all((config.DATA / e['path']).is_file() for e in prev):
        return prev
    d = config.DATA / 'plots' / 'beyond'
    d.mkdir(parents=True, exist_ok=True)
    day = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
    out = []
    for key, caption, layers in EARTH_SETS:
        for layer, dated, src in layers:
            params = {'SERVICE': 'WMS', 'REQUEST': 'GetMap', 'VERSION': '1.3.0', 'LAYERS': layer, 'STYLES': '',
                      'FORMAT': 'image/jpeg', 'CRS': 'EPSG:4326', 'BBOX': '-90,-180,90,180', 'WIDTH': 1600, 'HEIGHT': 800}
            if dated:
                params['TIME'] = day
            try:
                b = net.fetch(GIBS, params=params, timeout=120, retries=2)
            except Exception as e:
                log(f'  Earth image {layer} skipped: {e}')
                continue
            if not _jpeg(b):
                log(f'  Earth image {layer}: not an image ({len(b or b"")} bytes)')
                continue
            rel = f'plots/beyond/earth-{key}.jpg'
            (config.DATA / rel).write_bytes(b)
            out.append({'key': key, 'caption': caption, 'source': src, 'date': day if dated else None, 'path': rel, 't': time.time()})
            break
    try:
        js = net.fetch_json(EPIC + '/api/natural', timeout=60)
        if js:
            im = js[-1]
            when = str(im.get('date') or '')
            b = net.fetch(f"{EPIC}/archive/natural/{when[:10].replace('-', '/')}/jpg/{im['image']}.jpg", timeout=120, retries=2)
            if _jpeg(b):
                rel = 'plots/beyond/earth-epic.jpg'
                (config.DATA / rel).write_bytes(b)
                out.append({'key': 'epic', 'caption': 'The sunlit side of Earth from 1.5 million km away, between Earth and the Sun',
                            'source': 'NASA EPIC camera on DSCOVR', 'date': when, 'path': rel, 't': time.time()})
    except Exception as e:
        log(f'  EPIC image skipped: {e}')
    return out or None


# ------------------------------------------------------------------ ISS path (Earth-fixed positions, no observer)
def _gmst(jd_ut1):
    t = (jd_ut1 - 2451545.0) / 36525
    return math.radians((280.46061837 + 360.98564736629 * (jd_ut1 - 2451545.0) + 0.000387933 * t * t) % 360)


def iss_track(log, prev=None, hours=36, step_s=60, max_age_h=8):
    """Earth-fixed (ECEF, km) positions of the ISS every minute for the next 36 hours. Visitors' browsers turn these
    into pass times for their own location, so no location ever reaches this Mac or GitHub."""
    if prev and time.time() - (prev.get('made') or 0) < max_age_h * 3600 and prev.get('t0', 0) + hours * 3600 - time.time() > 24 * 3600:
        return prev
    try:
        from sgp4.api import Satrec, jday
    except ImportError:
        log('  ISS path skipped: the sgp4 package is not installed yet (it is installed with the next update restart)')
        return None
    try:
        lines = [x.strip() for x in net.fetch_text(CELESTRAK, params={'CATNR': 25544, 'FORMAT': 'TLE'}, timeout=40).splitlines() if x.strip()]
        l1, l2 = [x for x in lines if x.startswith('1 ')][0], [x for x in lines if x.startswith('2 ')][0]
        sat = Satrec.twoline2rv(l1, l2)
        t0 = int(time.time() // 60 * 60)
        pts = []
        for i in range(int(hours * 3600 / step_s)):
            t = t0 + i * step_s
            dt = datetime.fromtimestamp(t, timezone.utc)
            jd, fr = jday(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
            err, r, _ = sat.sgp4(jd, fr)
            if err:
                break
            g = _gmst(jd + fr)
            x = r[0] * math.cos(g) + r[1] * math.sin(g)          # TEME -> Earth-fixed (polar motion ignored: < 20 m)
            y = -r[0] * math.sin(g) + r[1] * math.cos(g)
            pts.append([round(x), round(y), round(r[2])])
        if len(pts) < 60:
            raise RuntimeError('orbit propagation failed')
        epoch = datetime(2000 + int(l1[18:20]), 1, 1, tzinfo=timezone.utc) + timedelta(days=float(l1[20:32]) - 1)
        return {'t0': t0, 'step': step_s, 'xyz': pts, 'epoch': epoch.isoformat()[:16], 'made': time.time()}
    except Exception as e:
        log(f'  ISS path skipped: {e}')
        return None


def earthquakes(log):
    try:
        js = net.fetch_json(USGS, timeout=60)
        out = []
        for ft in js.get('features') or []:
            pr, co = ft.get('properties') or {}, (ft.get('geometry') or {}).get('coordinates') or [None, None, None]
            out.append({'mag': _f(pr.get('mag')), 'place': pr.get('place'), 'time': pr.get('time'), 'url': pr.get('url'),
                        'tsunami': bool(pr.get('tsunami')),
                        'lon': round(co[0], 1) if co[0] is not None else None, 'lat': round(co[1], 1) if co[1] is not None else None,
                        'depth_km': round(co[2]) if co[2] is not None else None})
        out.sort(key=lambda q: -(q['mag'] or 0))
        return out[:60]
    except Exception as e:
        log(f'  earthquakes skipped: {e}')
        return None


# ------------------------------------------------------------------ this week's sky (location-free)
def sky_week(log):
    try:
        from astropy.coordinates import get_body, get_sun, GeocentricTrueEcliptic
        from astropy.time import Time
        t = Time.now()
        sun = get_sun(t)
        moon = get_body('moon', t)
        elong = sun.separation(moon).deg
        illum = (1 - math.cos(math.radians(elong))) / 2
        lam = lambda c: c.transform_to(GeocentricTrueEcliptic(equinox=t)).lon.deg
        waxing = ((lam(moon) - lam(sun)) % 360) < 180
        phase = ('New Moon' if illum < 0.03 else 'Full Moon' if illum > 0.97 else
                 ('Waxing ' if waxing else 'Waning ') + ('crescent' if illum < 0.45 else 'gibbous' if illum > 0.55 else 'half (quarter)'))
        planets = []
        TYPICAL_MAG = {'mercury': 0.0, 'venus': -4.2, 'mars': 0.5, 'jupiter': -2.4, 'saturn': 0.7}
        for name in ('mercury', 'venus', 'mars', 'jupiter', 'saturn'):
            p = get_body(name, t)
            e = sun.separation(p).deg
            east = ((lam(p) - lam(sun)) % 360) < 180
            need = 18 if name == 'mercury' else 15
            planets.append({'name': name.title(), 'elongation': round(e, 1), 'ra': round(float(p.ra.deg), 2),   # geocentric
                            'dec': round(float(p.dec.deg), 2), 'mag': TYPICAL_MAG[name],
                            'where': ('evening sky (after sunset, west)' if east else 'morning sky (before sunrise, east)') if e >= need else 'too close to the Sun'})
        today = datetime.now(timezone.utc).date()
        showers = []
        for name, m, d, zhr, parent in SHOWERS:
            for y in (today.year - 1, today.year, today.year + 1):
                peak = datetime(y, m, d, tzinfo=timezone.utc).date()
                dd = (peak - today).days
                if -3 <= dd <= 30:
                    showers.append({'name': name, 'peak': peak.isoformat(), 'days': dd, 'zhr': zhr, 'parent': parent})
        return {'moon': {'illumination': round(illum, 2), 'phase': phase}, 'planets': planets,
                'showers': sorted(showers, key=lambda s: s['days']), 'moon_phases': moon_phases(t)}
    except Exception as e:
        log(f'  sky this week skipped: {e}')
        return None


def moon_phases(t0, days=60):
    """New, first-quarter, full and last-quarter Moon times for the next two months (UTC, to a few minutes)."""
    import numpy as np
    from astropy.coordinates import get_body, get_sun, GeocentricTrueEcliptic
    from astropy.time import Time
    import astropy.units as u
    ts = t0 + np.arange(0, days * 24 + 1, 2) * u.hour
    frame = GeocentricTrueEcliptic(equinox=ts)
    d = (get_body('moon', ts).transform_to(frame).lon.deg - get_sun(ts).transform_to(frame).lon.deg) % 360
    names = {0: 'New Moon', 90: 'First quarter Moon', 180: 'Full Moon', 270: 'Last quarter Moon'}
    out = []
    for i in range(len(d) - 1):
        a, b = d[i], d[i + 1]
        for target, name in names.items():
            # unwrap so the step a->b crosses `target` without the 360->0 jump
            aa, bb = (a - target + 180) % 360 - 180, (b - target + 180) % 360 - 180
            if aa < 0 <= bb and bb - aa < 90:
                f = -aa / (bb - aa)
                when = Time(ts[i].jd + f * (ts[i + 1].jd - ts[i].jd), format='jd')
                out.append({'t': when.isot[:16] + 'Z', 'name': name})
    return out


# ------------------------------------------------------------------ satellites (how crowded orbit is; Starlink trains)
def satellites(log, prev=None, max_age_h=24):
    if prev and time.time() - (prev.get('t') or 0) < max_age_h * 3600:
        return prev
    try:
        js = net.fetch_json(CELESTRAK, params={'GROUP': 'active', 'FORMAT': 'json'}, timeout=120)
        names = [str(o.get('OBJECT_NAME') or '') for o in js]
        return {'active': len(names), 'starlink': sum(n.startswith('STARLINK') for n in names),
                'oneweb': sum(n.startswith('ONEWEB') for n in names), 't': time.time()}
    except Exception as e:
        log(f'  satellite count skipped: {e}')
        return None


# ------------------------------------------------------------------ dark-sky map (lower 48) and ZIP code centres
def _gibs(layer, bbox, w, h, fmt='image/jpeg', dated=False):
    params = {'SERVICE': 'WMS', 'REQUEST': 'GetMap', 'VERSION': '1.3.0', 'LAYERS': layer, 'STYLES': '', 'FORMAT': fmt,
              'CRS': 'EPSG:4326', 'BBOX': ','.join(str(x) for x in bbox), 'WIDTH': w, 'HEIGHT': h}
    b = net.fetch(GIBS, params=params, timeout=180, retries=2)
    if not (b[:3] == b'\xff\xd8\xff' or b[:4] == b'\x89PNG') or len(b) < 20000:
        raise RuntimeError(f'{layer}: not an image')
    return b


def sky_glow(lights, km_x, km_y, reach_km=200):
    """Rough sky brightness: every lit pixel brightens the sky around it, fading with distance (about d^-2.5).
    lights: 2-D array of night-light brightness (0-1). Returns an array of the same shape."""
    import numpy as np
    rx, ry = int(reach_km / km_x), int(reach_km / km_y)
    yy, xx = np.mgrid[-ry:ry + 1, -rx:rx + 1]
    d = np.hypot(xx * km_x, yy * km_y)
    k = np.where(d <= reach_km, 1.0 / (d + 3.0) ** 2.5, 0.0)
    H, W = lights.shape
    fh, fw = H + 2 * ry + 1, W + 2 * rx + 1
    F = np.fft.rfft2(lights, (fh, fw)) * np.fft.rfft2(k, (fh, fw))
    return np.fft.irfft2(F, (fh, fw))[ry:ry + H, rx:rx + W]


def dark_sky_map(log, prev=None, max_age_days=30):
    """Estimated night-sky brightness for the lower 48 from NASA's Black Marble night-lights picture (refreshed monthly).
    Saved as a small greyscale PNG (0 = darkest, 254 = brightest, 255 = water) for the Stargazing tab's dark-sky finder."""
    import io
    import numpy as np
    from PIL import Image
    rel = 'plots/beyond/darksky.png'
    if prev and time.time() - (prev.get('made') or 0) < max_age_days * 86400 and (config.DATA / rel).is_file():
        return prev
    try:
        lat0, lon0, lat1, lon1 = CONUS
        W, H = 2360, 1040                                    # 0.025 degrees per pixel
        night = np.asarray(Image.open(io.BytesIO(_gibs('VIIRS_Black_Marble', CONUS, W, H))).convert('L'), dtype=float) / 255
        lights = np.clip(night - 0.12, 0, None) ** 2.2       # ignore the faint land texture, stretch bright cities
        km_y = 0.025 * 111.32
        km_x = km_y * np.cos(np.radians((lat0 + lat1) / 2))
        glow = sky_glow(lights, km_x, km_y)
        idx = np.log10(glow + 1e-6)
        water = None
        for layer in ('BlueMarble_NextGeneration', 'BlueMarble_ShadedRelief_Bathymetry'):
            try:
                day = np.asarray(Image.open(io.BytesIO(_gibs(layer, CONUS, W, H))).convert('RGB'), dtype=int)
                r, g, b = day[..., 0], day[..., 1], day[..., 2]
                water = (b > r + 12) & (b >= g) & (r + g + b < 360)
                break
            except Exception as e:
                log(f'  land/water layer {layer} skipped: {e}')
        lo, hi = np.percentile(idx, 1), np.percentile(idx, 99.9)
        v = np.clip((idx - lo) / (hi - lo) * 254, 0, 254)
        if water is not None:
            v[water] = 255
        img = Image.fromarray(v.astype(np.uint8)).resize((W // 2, H // 2), Image.NEAREST)   # 0.05 degrees per pixel
        arr = np.asarray(img)
        land = arr[arr < 255]
        hist = np.bincount(land.ravel(), minlength=255)[:255]
        cdf = (np.cumsum(hist) / max(1, land.size)).round(4).tolist()
        (config.DATA / 'plots' / 'beyond').mkdir(parents=True, exist_ok=True)
        img.save(config.DATA / rel, optimize=True)
        log(f'  dark-sky map made ({img.size[0]}x{img.size[1]}, land/water mask: {"yes" if water is not None else "no"})')
        return {'path': rel, 'bbox': [lat0, lon0, lat1, lon1], 'w': img.size[0], 'h': img.size[1], 'cdf': cdf,
                'lo': round(float(lo), 3), 'hi': round(float(hi), 3), 'water': water is not None, 'made': time.time()}
    except Exception as e:
        log(f'  dark-sky map skipped: {e}')
        return None


def zip_index(log, prev=None, max_age_days=180):
    """Centre point of every US ZIP code (Census Bureau ZCTA gazetteer), as 'ZIP lat*100 lon*100' lines.
    The Stargazing tab loads it only when a visitor types a ZIP code, and looks it up inside their browser."""
    import io
    import zipfile
    rel = 'plots/beyond/zip.txt'
    if prev and time.time() - (prev.get('made') or 0) < max_age_days * 86400 and (config.DATA / rel).is_file():
        return prev
    year = datetime.now().year
    for y in range(year, year - 5, -1):
        try:
            raw = net.fetch(CENSUS_ZCTA.format(y=y), timeout=180, retries=1)
            z = zipfile.ZipFile(io.BytesIO(raw))
            text = z.read(z.namelist()[0]).decode('utf-8', 'replace').splitlines()
            head = [h.strip().upper() for h in text[0].split('\t')]
            gi, la, lo = head.index('GEOID'), head.index('INTPTLAT'), head.index('INTPTLONG')
            lines = []
            for row in text[1:]:
                c = row.split('\t')
                if len(c) > max(gi, la, lo):
                    lines.append(f'{c[gi].strip()} {round(float(c[la]) * 100)} {round(float(c[lo]) * 100)}')
            (config.DATA / 'plots' / 'beyond').mkdir(parents=True, exist_ok=True)
            (config.DATA / rel).write_text('\n'.join(lines) + '\n')
            log(f'  ZIP code centres saved ({len(lines)} ZIP codes, Census {y})')
            return {'path': rel, 'year': y, 'n': len(lines), 'made': time.time()}
        except Exception as e:
            log(f'  Census ZIP file {y} skipped: {e}')
    return None


# ------------------------------------------------------------------ upcoming events (also published as a calendar)
def build_events(out, now=None):
    now = now or datetime.now(timezone.utc)
    ev = []
    for name, m, d, zhr, parent in SHOWERS:
        for y in (now.year, now.year + 1):
            peak = datetime(y, m, d, tzinfo=timezone.utc)
            if now - timedelta(days=1) <= peak <= now + timedelta(days=365):
                ev.append({'date': peak.date().isoformat(), 'kind': 'meteor', 'title': f'{name} meteor shower peak',
                           'detail': f'Up to about {zhr} meteors an hour under dark skies, best after midnight. Debris from {parent}. '
                                     'Watch the nights around the peak too.'})
    for p in ((out.get('sky') or {}).get('moon_phases') or []):
        if p['name'] in ('New Moon', 'Full Moon'):
            ev.append({'t': p['t'], 'kind': 'moon', 'title': p['name'],
                       'detail': 'Darkest skies of the month: best for faint stars, galaxies and meteors.' if p['name'] == 'New Moon'
                       else 'The Moon is up all night: great for looking at the Moon, poor for faint objects.'})
    for date, title, where in ECLIPSES:
        if date >= now.date().isoformat():
            ev.append({'date': date, 'kind': 'eclipse', 'title': title, 'detail': f'Visible from: {where}.'})
    for L in ((out.get('launches') or {}).get('upcoming') or [])[:8]:
        if L.get('net'):
            ev.append({'t': L['net'], 'kind': 'launch', 'title': f"Launch: {L['name']}",
                       'detail': f"{L.get('provider') or ''}{', to ' + L['orbit'] if L.get('orbit') else ''}. Launch times often slip."})
    for c in (out.get('comets') or []):
        dtp = c.get('days_to_perihelion')
        if dtp is not None and 0 <= dtp <= 120 and (c.get('m1') or 99) < 11:
            ev.append({'date': (now + timedelta(days=dtp)).date().isoformat(), 'kind': 'comet', 'title': f"Comet {c['name']} closest to the Sun",
                       'detail': f"Comets are often brightest around this time. It passes {c.get('q_au')} AU from the Sun."})
    key = lambda e: e.get('t') or e.get('date')
    return sorted(ev, key=key)[:80]


# ------------------------------------------------------------------ community reviews (GitHub issues)
SECTION = re.compile(r'###\s*(.+?)\s*\n+(.*?)(?=\n###|\Z)', re.S)


def parse_issue(body):
    out = {}
    for k, v in SECTION.findall(body or ''):
        out[k.strip().lower()] = v.strip()
    return out


def community(log, repo):
    if not repo or '/' not in repo:
        return None
    people, tess, ast = {}, {}, {}
    try:
        for label, bucket in (('candidate-review', tess), ('asteroid-review', ast)):
            js = net.fetch_json(GH_ISSUES.format(repo=repo), params={'labels': label, 'state': 'all', 'per_page': 100},
                                headers={'Accept': 'application/vnd.github+json'}, timeout=40)
            for it in js if isinstance(js, list) else []:
                user = (it.get('user') or {}).get('login') or '?'
                f = parse_issue(it.get('body'))
                verdict = f.get('what do you think it is?') or f.get('what do you see?') or 'not given'
                key = f.get('tic number') or f.get('sighting') or ''
                key = re.sub(r'\D', '', key) if label == 'candidate-review' else key[:80]
                if not key or verdict.startswith('_No response'):
                    continue
                people[user] = people.get(user, 0) + 1
                bucket.setdefault(key, {}).setdefault(verdict, 0)
                bucket[key][verdict] += 1
        board = sorted(({'user': u, 'reviews': n} for u, n in people.items()), key=lambda x: -x['reviews'])[:20]
        return {'leaderboard': board, 'tess': tess, 'asteroids': ast, 't': time.time()}
    except Exception as e:
        log(f'  community reviews skipped: {e}')
        return None


# ------------------------------------------------------------------ housekeeping
def backup_db(con, log, keep=7):
    d = config.DATA / 'backups'
    d.mkdir(exist_ok=True)
    name = d / f"spacelab-{datetime.now().strftime('%Y%m%d')}.db"
    if name.exists():
        return None
    dst = sqlite3.connect(name)
    try:
        con.backup(dst)
    finally:
        dst.close()
    old = sorted(d.glob('spacelab-*.db'))[:-keep]
    for p in old:
        p.unlink()
    log(f'  database backed up ({name.name}; {len(old)} old copies removed)')
    return name.name


def clean_and_measure(log, max_age_days=60):
    """Remove Space Lab's own temporary files older than max_age_days, then report disk use."""
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for sub in ('cache/fpp', 'cache/cutouts', 'cache/tesscut', 'cache/lk'):
        base = config.DATA / sub
        if not base.is_dir():
            continue
        for p in base.rglob('*'):
            try:
                if p.is_file() and p.stat().st_mtime < cutoff:
                    p.unlink()
                    removed += 1
            except OSError:
                pass
    sizes = {}
    for p in config.DATA.iterdir():
        try:
            sizes[p.name] = p.stat().st_size if p.is_file() else sum(f.stat().st_size for f in p.rglob('*') if f.is_file())
        except OSError:
            pass
    total = sum(sizes.values())
    free = shutil.disk_usage(config.DATA).free
    top = sorted(sizes.items(), key=lambda kv: -kv[1])[:6]
    if removed:
        log(f'  removed {removed} old temporary files')
    return {'total_gb': round(total / 1e9, 2), 'free_gb': round(free / 1e9, 1),
            'largest': [[k, round(v / 1e9, 2)] for k, v in top], 'removed': removed}


def run(ctx):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    now_jd = time.time() / 86400 + 2440587.5
    prev = db.kv_get(con, 'beyond') or {}
    parts = {'space_weather': lambda: space_weather(log), 'fireballs': lambda: fireballs(log),
             'comets': lambda: comets(log, now_jd), 'gw': lambda: gravitational_waves(log),
             'supernovae': lambda: supernovae(log), 'launches': lambda: launches(log),
             'habitable': lambda: habitable_worlds(log), 'sky': lambda: sky_week(log),
             'earth': lambda: earth_images(log, prev.get('earth')), 'iss': lambda: iss_track(log, prev.get('iss')),
             'quakes': lambda: earthquakes(log), 'satellites': lambda: satellites(log, prev.get('satellites')),
             'darksky': lambda: dark_sky_map(log, prev.get('darksky')), 'zip': lambda: zip_index(log, prev.get('zip'))}
    out, ok = {}, []
    for k, fn in parts.items():
        v = fn()
        if v:
            out[k] = v
            ok.append(k)
        elif prev.get(k):
            out[k] = prev[k]          # keep the last good copy when a source is down
    out['events'] = build_events(out)
    out['t'] = time.time()
    db.kv_set(con, 'beyond', out)
    comm = community(log, (cfg.get('share') or {}).get('github_repo'))
    if comm is not None:
        db.kv_set(con, 'community', comm)
    try:
        backup_db(con, log)
    except Exception as e:
        log(f'  backup skipped: {e}')
    try:
        db.kv_set(con, 'disk', clean_and_measure(log))
    except Exception as e:
        log(f'  disk check skipped: {e}')
    return f"updated {len(ok)} of {len(parts)} sources ({', '.join(ok)})" + (f"; {sum(c['reviews'] for c in comm['leaderboard'])} community reviews" if comm else '')
