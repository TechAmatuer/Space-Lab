"""Transit timing targets (daily).

Every known transiting planet's predicted transit time drifts as small errors in its period add up. This job
computes, for each one, how uncertain tonight's predicted mid-transit time is ("ephemeris drift") and ranks:
  * Exoplanet Watch targets: planets MicroObservatory can measure whose timing has gone stale the most;
  * your telescope: transits in the next days you can actually see from home (dark sky, star high enough).
Your home location is used only here, on your Mac; it is never shared or published.
"""
import math
import time
import warnings

import numpy as np

from . import db, net

NEA_TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
SCOPES = {   # what a small smart telescope can realistically time (one-shot colour, green channel)
    'Seestar S50': {'vmax': 11.5, 'depth_ppt': 8.0},
    'Dwarf 3': {'vmax': 11.0, 'depth_ppt': 10.0},
    '': {'vmax': 11.5, 'depth_ppt': 8.0},
}


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def load_planets(con, log):
    cached, t = db.kv_get(con, 'ephem_nea'), db.kv_time(con, 'ephem_nea')
    if cached and t and time.time() - t < 3 * 86400:
        return cached
    q = ('select pl_name,hostname,ra,dec,sy_vmag,pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_tranmid,pl_tranmiderr1,'
         'pl_tranmiderr2,pl_trandep,pl_trandur,pl_ratror from pscomppars where tran_flag=1 and pl_tranmid is not null '
         'and pl_orbper is not null and sy_vmag < 13.5 and dec > -45')
    rows = net.fetch_json(NEA_TAP, params={'query': q, 'format': 'json'}, timeout=180)
    log(f'{len(rows)} transiting planets brighter than V=13.5 from the NASA Exoplanet Archive')
    db.kv_set(con, 'ephem_nea', rows)
    return rows


def drift(p, jd):
    """Return (next mid-transit JD, its 1-sigma uncertainty in minutes, epoch number)."""
    T0, P = _f(p.get('pl_tranmid')), _f(p.get('pl_orbper'))
    if not T0 or not P:
        return None
    warnings.simplefilter('ignore', RuntimeWarning)
    sT = np.nanmean([abs(_f(p.get('pl_tranmiderr1')) or np.nan), abs(_f(p.get('pl_tranmiderr2')) or np.nan)])
    sP = np.nanmean([abs(_f(p.get('pl_orbpererr1')) or np.nan), abs(_f(p.get('pl_orbpererr2')) or np.nan)])
    if not np.isfinite(sT) or not np.isfinite(sP):
        return None
    n = math.ceil((jd - T0) / P)
    return T0 + n * P, math.hypot(sT, n * sP) * 1440, n


def depth_ppt(p):
    d = _f(p.get('pl_trandep'))
    if d:
        return d * 10.0           # archive gives percent
    r = _f(p.get('pl_ratror'))
    return r * r * 1000 if r else None


def stale_targets(planets, jd, vmax, dmin):
    out = []
    for p in planets:
        v, dep, dec = _f(p.get('sy_vmag')), depth_ppt(p), _f(p.get('dec'))
        if v is None or dep is None or v > vmax or dep < dmin or (dec is not None and dec < -30):
            continue
        d = drift(p, jd)
        if not d:
            continue
        tnext, sig, n = d
        out.append({'name': p['pl_name'], 'host': p.get('hostname'), 'vmag': round(v, 1), 'depth_ppt': round(dep, 1),
                    'period': round(_f(p['pl_orbper']), 5), 'sigma_min': round(sig, 1), 'epochs_since': n,
                    'next_jd': round(tnext, 5), 'ra': _f(p.get('ra')), 'dec': dec, 'dur_h': _f(p.get('pl_trandur'))})
    # beyond ~6 hours the transit can't be caught in one night: those need a dedicated search, not a quick timing
    out = [r for r in out if r['sigma_min'] <= 360]
    out.sort(key=lambda r: -r['sigma_min'])
    return out


def plan(planets, jd, lat, lon, elev, model, days, log):
    """Transits fully observable from home in the next `days` (sun below -12°, star above 25°)."""
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord, get_sun
    from astropy.time import Time
    import astropy.units as u
    from astropy.utils import iers
    iers.conf.iers_degraded_accuracy = 'warn'   # second-level Earth-rotation accuracy is irrelevant for planning
    lim = SCOPES.get(model, SCOPES[''])
    loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=(elev or 0) * u.m)
    events = []
    for p in planets:
        v, dep, dec = _f(p.get('sy_vmag')), depth_ppt(p), _f(p.get('dec'))
        if v is None or dep is None or dec is None or v > lim['vmax'] or dep < lim['depth_ppt'] or dec < lat - 65:
            continue
        d = drift(p, jd)
        if not d:
            continue
        tn, sig, n = d
        P = _f(p['pl_orbper'])
        dur = (_f(p.get('pl_trandur')) or 2.5) / 24
        k = 0
        while tn + k * P < jd + days:
            tm = tn + k * P
            events.append((p, tm, dur, sig, dep, v))
            k += 1
    if not events:
        return []
    # sample each event window every 20 minutes (one hour of baseline before and after)
    samples, owner = [], []
    for i, (p, tm, dur, *_r) in enumerate(events):
        w0, w1 = tm - dur / 2 - 1 / 24, tm + dur / 2 + 1 / 24
        for t in np.arange(w0, w1 + 1e-9, 20 / 1440):
            samples.append(t)
            owner.append(i)
    owner = np.array(owner)
    times = Time(np.array(samples), format='jd')
    frame = AltAz(obstime=times, location=loc)
    sun_alt = get_sun(times).transform_to(frame).alt.deg
    ras = np.array([_f(events[i][0]['ra']) for i in owner])
    decs = np.array([_f(events[i][0]['dec']) for i in owner])
    star_alt = SkyCoord(ras * u.deg, decs * u.deg).transform_to(frame).alt.deg
    ok = []
    for i, (p, tm, dur, sig, dep, v) in enumerate(events):
        m = owner == i
        if np.all(sun_alt[m] < -12) and np.all(star_alt[m] > 25):
            ok.append({'name': p['pl_name'], 'mid_utc': Time(tm, format='jd').isot[:16] + 'Z', 'mid_jd': round(tm, 5),
                       'start_utc': Time(tm - dur / 2 - 1 / 24, format='jd').isot[:16] + 'Z',
                       'end_utc': Time(tm + dur / 2 + 1 / 24, format='jd').isot[:16] + 'Z',
                       'dur_h': round(dur * 24, 1), 'depth_ppt': round(dep, 1), 'vmag': round(v, 1), 'sigma_min': round(sig, 1),
                       'min_alt': round(float(star_alt[m].min())), 'value': round(sig * math.sqrt(dep), 1)})
    ok.sort(key=lambda e: e['mid_jd'])
    log(f'{len(ok)} transits observable from home in the next {days} days (of {len(events)} checked)')
    return ok


def run(ctx):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    tg = cfg['targets']
    planets = load_planets(con, log)
    jd = time.time() / 86400 + 2440587.5
    stale = stale_targets(planets, jd, tg['mobs_vmax'], tg['mobs_depth_ppt'])
    db.kv_set(con, 'ephem', {'t': time.time(), 'stale': stale[:60], 'n': len(stale)})
    msg = f'{len(stale)} planets MicroObservatory can time; most stale: ' + \
          ', '.join(f'{s["name"]} (±{s["sigma_min"]:.0f} min)' for s in stale[:3])
    sc = cfg.get('telescope') or {}
    lat, lon = _f(sc.get('lat')), _f(sc.get('lon'))
    if lat is not None and lon is not None:
        ev = plan(planets, jd, lat, lon, _f(sc.get('elev')) or 0, sc.get('model') or '', int(tg['days_ahead']), log)
        db.kv_set(con, 'ephem_plan', {'t': time.time(), 'events': ev[:80], 'model': sc.get('model') or ''})
        msg += f'; {len(ev)} transits you can observe from home this week'
    else:
        db.kv_set(con, 'ephem_plan', {'t': time.time(), 'events': [], 'missing_location': True})
    return msg
