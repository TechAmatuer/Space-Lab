"""Variable-star observatory, part 1: what to observe tonight.

For an automated observatory that reports variable-star photometry to the AAVSO (see VARIABLE_STARS.md). Once a day:
1. Targets: the AAVSO Target Tool's lists (Alerts & Campaigns first, plus any observing sections chosen), read with the
   observer's own Target Tool API key or from a CSV exported from the Target Tool, plus the observer's own watch list
   (positions from AAVSO VSX).
2. Need: each target's requested cadence against the time since anyone last reported it to the AAVSO, and since this
   observatory last measured it. An alert or campaign star that is many cadences overdue rises to the top.
3. Tonight: when it is dark enough, how high each star gets above the observatory's horizon limit, the Moon, and whether
   the star's brightness suits the camera (not saturated, not too faint).
4. Exposures: per filter, scaled from the observer's exposure for a 12th-magnitude star, so each target gets the same
   signal whatever its brightness (the idea behind the N.I.N.A. ExoPlanet plugin's adaptive exposure).
5. A plan that fits the night (each target's sequence through every filter, slews included; time-series targets get a
   longer block), written as a N.I.N.A. Target Scheduler CSV. Its Type column carries the exposure class, so the observer
   gives each class an exposure-plan template once and imports with the Type filter.

The observatory's location and the API key stay in data/settings.json on this computer; nothing here is published.
"""
import base64
import csv
import io
import json
import math
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from . import config, db, net

TT_API = 'https://targettool.aavso.org/TargetTool/api/v1/targets'
VSX_API = 'https://www.aavso.org/vsx/index.php'
OUT = config.DATA / 'vstars'
SECTIONS = {'ac': 'Alerts / Campaigns', 'ep': 'Exoplanets', 'cv': 'Cataclysmic Variables', 'eb': 'Eclipsing Variables',
            'spp': 'Short Period Pulsators', 'lpv': 'Long Period Variables', 'yso': 'Young Stellar Objects',
            'het': 'High Energy Targets', 'misc': 'Miscellaneous'}
NICE = [1, 2, 3, 5, 8, 10, 15, 20, 30, 45, 60, 90, 120, 180, 240, 300, 450, 600]


def settings(cfg=None):
    """The observatory's settings (cfg['vstars']); the location falls back to Setup's telescope location."""
    cfg = cfg or config.load()
    s = dict(cfg.get('vstars') or {})
    site = dict(s.get('site') or {})
    if _num(site.get('lat')) is None or _num(site.get('lon')) is None:
        tel = cfg.get('telescope') or {}
        if _num(tel.get('lat')) is not None and _num(tel.get('lon')) is not None:
            site = {'lat': _num(tel['lat']), 'lon': _num(tel['lon']), 'elev_m': _num(tel.get('elev')) or 0}
    if _num(site.get('lat')) is not None and _num(site.get('lon')) is not None:
        site.update(lat=_num(site['lat']), lon=_num(site['lon']), elev_m=_num(site.get('elev_m')) or 0)
        s['site'] = site
    else:
        s['site'] = {}
    return s


# ------------------------------------------------------------------ targets
def _num(x):
    try:
        v = float(str(x).strip().rstrip(':'))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def angle(x, hours=False):
    """Degrees from decimal or sexagesimal text ('21 42 42.9', '21:42:42.9', '+43 35 09.9'). hours=True: RA given in hours."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    v = _num(s)
    if v is not None and not re.search(r'[\s:hdmº°\'"]', s):
        return v
    parts = [float(p) for p in re.findall(r'[\d.]+', s)][:3]
    if not parts:
        return None
    a = parts[0] + (parts[1] / 60 if len(parts) > 1 else 0) + (parts[2] / 3600 if len(parts) > 2 else 0)
    a = -a if s.lstrip().startswith('-') else a
    return a * 15 if hours else a


def _when(x):
    """Unix time from the Target Tool's last-observation text (ISO, 'YYYY-MM-DD hh:mm:ss' or a JD); None if unknown."""
    if x in (None, '', 'None'):
        return None
    v = _num(x)
    if v is not None:
        return (v - 2440587.5) * 86400 if v > 2400000 else None
    for fmt in ('%Y-%m-%dT%H:%M:%S', '%Y-%m-%d %H:%M:%S', '%Y-%m-%dT%H:%M:%SZ', '%Y-%m-%d %H:%M', '%Y-%m-%d'):
        try:
            return datetime.strptime(str(x).strip()[:19].rstrip('Z'), fmt).replace(tzinfo=timezone.utc).timestamp()
        except ValueError:
            continue
    return None


KEYS = {'star_name': 'name', 'starname': 'name', 'name': 'name', 'star': 'name', 'ra': 'ra', 'dec': 'dec', 'var_type': 'type', 'vartype': 'type',
        'variabilitytype': 'type', 'type': 'type', 'min_mag': 'min_mag', 'minmag': 'min_mag', 'max_mag': 'max_mag', 'maxmag': 'max_mag',
        'period': 'period', 'periodd': 'period', 'obs_cadence': 'cadence', 'obscadence': 'cadence', 'obscadenced': 'cadence', 'observingcadenced': 'cadence',
        'obs_section': 'sections', 'obssection': 'sections', 'observingsection': 'sections', 'filter': 'filters', 'filters': 'filters',
        'last_data_point': 'last', 'lastdatapoint': 'last', 'lastobservation': 'last', 'priority': 'priority', 'other_info': 'info',
        'otherinfo': 'info', 'obs_mode': 'mode', 'obsmode': 'mode', 'solar_conjunction': 'conj'}


def normalise(row, source):
    """One Target Tool record (API or exported CSV, whatever the column spelling) -> a target dict, or None."""
    r = {}
    for k, v in row.items():
        kk = re.sub(r'[^a-z_]', '', str(k).strip().lower().replace(' ', '_').replace('.', ''))
        key = KEYS.get(kk) or KEYS.get(kk.replace('_', ''))
        if key and key not in r:
            r[key] = v
    name = str(r.get('name') or '').strip()
    ra, dec = angle(r.get('ra'), hours=bool(re.search(r'[\s:h]', str(r.get('ra') or '')))), angle(r.get('dec'))
    if not name or ra is None or dec is None:
        return None
    secs = r.get('sections')
    secs = [s.strip() for s in (secs if isinstance(secs, list) else str(secs or '').split(',')) if str(s).strip()]
    filt = r.get('filters')
    filt = [s.strip() for s in (filt if isinstance(filt, list) else str(filt or '').split(',')) if str(s).strip()]
    pri = r.get('priority')
    mode = str(r.get('mode') or '')
    return {'name': name, 'ra': ra % 360, 'dec': dec, 'type': str(r.get('type') or ''), 'period': _num(r.get('period')),
            'min_mag': _num(r.get('min_mag')), 'max_mag': _num(r.get('max_mag')), 'cadence': _num(r.get('cadence')),
            'last': _when(r.get('last')), 'priority': pri is True or str(pri).lower() in ('true', 'yes', '1', 'high'),
            'sections': secs, 'filters': filt, 'info': str(r.get('info') or '')[:300], 'source': source,
            'photometry': not mode or mode.lower() in ('photometry', 'all'), 'conj': str(r.get('conj')).lower() == 'true',
            'series': bool(re.search(r'time[ -]?series', f"{r.get('info') or ''} {mode}", re.I))}


def target_tool(s, fetch=None, log=print):
    """Targets from the AAVSO Target Tool API (the observer's own key) or from an exported CSV."""
    key = (s.get('targettool_key') or '').strip()
    out = []
    if key:
        auth = base64.b64encode(f'{key}:api_token'.encode()).decode()
        params = [('obs_section', x) for x in (s.get('sections') or ['ac'])]
        try:
            js = (fetch or (lambda: net.fetch_json(TT_API + '?' + '&'.join(f'{k}={v}' for k, v in params), timeout=120,
                                                   headers={'Authorization': 'Basic ' + auth})))()
            rows = js.get('targets') if isinstance(js, dict) else js
            out = [t for t in (normalise(x, 'AAVSO Target Tool') for x in rows or []) if t]
            log(f'  AAVSO Target Tool: {len(out)} targets ({", ".join(SECTIONS.get(x, x) for x in (s.get("sections") or ["ac"]))})')
        except Exception as e:
            log(f'  AAVSO Target Tool did not answer ({type(e).__name__}); trying an exported CSV instead')
    if not out:
        p = Path(s.get('targettool_csv') or (config.INBOX / 'vstars' / 'targettool.csv'))
        if p.exists():
            with p.open(newline='', encoding='utf-8-sig') as fh:
                out = [t for t in (normalise(x, 'Target Tool export') for x in csv.DictReader(fh)) if t]
            log(f'  Target Tool export ({p.name}): {len(out)} targets')
    return out


def vsx_object(name, fetch=None):
    """Position, type, period and range of a variable star from AAVSO VSX, or None."""
    js = (fetch or (lambda: net.fetch_json(VSX_API, params={'view': 'api.object', 'ident': name, 'format': 'json'}, timeout=60)))()
    o = (js or {}).get('VSXObject') or {}
    ra, dec = _num(o.get('RA2000')), _num(o.get('Declination2000'))
    if ra is None or dec is None:
        return None
    rng = [_num(re.sub(r'[^\d.]', '', str(o.get(k) or ''))) for k in ('MaxMag', 'MinMag')]
    return {'name': o.get('Name') or name, 'ra': ra, 'dec': dec, 'type': o.get('VariabilityType') or '', 'period': _num(o.get('Period')),
            'max_mag': rng[0], 'min_mag': rng[1], 'auid': o.get('AUID') or ''}


def watchlist(s, con, fetch=None, log=print):
    """The observer's own stars, with positions from VSX (kept, so VSX is asked once per star)."""
    cache = db.kv_get(con, 'vs_vsx') or {}
    out = []
    for w in s.get('watchlist') or []:
        w = {'name': w} if isinstance(w, str) else dict(w)
        nm = str(w.get('name') or '').strip()
        if not nm:
            continue
        o = cache.get(nm)
        if not o:
            try:
                o = vsx_object(nm, (lambda n=nm: fetch(n)) if fetch else None)
            except Exception as e:
                log(f'  {nm}: VSX did not answer ({type(e).__name__})')
                o = None
            if o:
                cache[nm] = o
        if not o:
            log(f'  {nm}: not found in VSX; left out')
            continue
        out.append({**o, 'name': nm, 'cadence': _num(w.get('cadence_days')) or 1.0, 'last': None, 'priority': bool(w.get('priority')),
                    'sections': ['Watch list'], 'filters': [], 'info': str(w.get('note') or ''), 'source': 'watch list',
                    'photometry': True, 'series_min': _num(w.get('series_min')), 'conj': False})
    db.kv_set(con, 'vs_vsx', cache)
    return out


# ------------------------------------------------------------------ the night
def _sun_moon(times):
    from astropy.coordinates import get_body, get_sun
    from astropy.time import Time
    t = Time(times, format='unix')
    return get_sun(t), get_body('moon', t)


def night(site, day_unix, sun_alt=-12.0, step_min=10):
    """Dark window of the night that starts on the local evening of day_unix: (start, end, times, sun/moon helpers)."""
    from astropy.coordinates import AltAz, EarthLocation
    from astropy.time import Time
    from astropy.utils import iers
    import astropy.units as u
    iers.conf.iers_degraded_accuracy = 'warn'          # second-level Earth-rotation accuracy is irrelevant for planning
    loc = EarthLocation(lat=site['lat'] * u.deg, lon=site['lon'] * u.deg, height=(site.get('elev_m') or 0) * u.m)
    local_noon = day_unix - (day_unix + site['lon'] / 15 * 3600) % 86400 + 12 * 3600       # approximate solar noon
    times = local_noon + np.arange(0, 24 * 3600, step_min * 60)
    sun, moon = _sun_moon(times)
    fr = AltAz(obstime=Time(times, format='unix'), location=loc)
    salt = sun.transform_to(fr).alt.deg
    dark = salt < sun_alt
    if not dark.any():
        return None
    k = np.flatnonzero(dark)
    from astropy.coordinates import SkyCoord
    m = moon[dark]
    return {'start': float(times[k[0]]), 'end': float(times[k[-1]] + step_min * 60), 'times': times[dark], 'loc': loc,
            'moon': SkyCoord(m.ra, m.dec, frame='icrs'), 'sun': sun[dark], 'frame': AltAz(obstime=Time(times[dark], format='unix'), location=loc)}


def visibility(targets, nt, min_alt=30.0, moon_sep=15.0):
    """Adds to each target: hours above min_alt in the dark, its best time and highest altitude, and the Moon's distance."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    if not targets:
        return targets
    sc = SkyCoord([t['ra'] for t in targets] * u.deg, [t['dec'] for t in targets] * u.deg)
    step = (nt['times'][1] - nt['times'][0]) if len(nt['times']) > 1 else 600
    for t, c in zip(targets, sc):
        alt = c.transform_to(nt['frame']).alt.deg
        ok = alt >= min_alt
        msep = float(np.min(c.separation(nt['moon']).deg))
        t['hours_up'] = round(float(ok.sum() * step / 3600), 2)
        t['alt_max'] = round(float(alt.max()), 1)
        t['best'] = float(nt['times'][int(np.argmax(alt))])
        t['up'] = [float(nt['times'][i]) for i in np.flatnonzero(ok)] if ok.any() else []
        t['moon_sep'] = round(msep, 1)
        t['visible'] = bool(ok.any()) and msep >= moon_sep
        t['airmass_best'] = round(1 / math.sin(math.radians(max(t['alt_max'], 5))), 2)
    return targets


# ------------------------------------------------------------------ need, brightness, exposures
def expected_mag(t, own=None):
    """Magnitude to plan the exposure for: the last one measured here, else the middle of the catalogue range."""
    if own is not None:
        return own
    a, b = t.get('max_mag'), t.get('min_mag')
    if a is not None and b is not None:
        return (a + b) / 2
    return a if a is not None else b


def exposures(s, mag):
    """Seconds per filter for a star of this magnitude, rounded to familiar values and kept within the camera's limits."""
    out = []
    for f in s.get('filters') or []:
        raw = float(f.get('exp_at_mag12', 30)) * 10 ** (0.4 * ((mag if mag is not None else 12.0) - 12.0))
        raw = min(max(raw, float(s.get('exp_min', 3))), float(s.get('exp_max', 300)))
        nice = min(NICE, key=lambda x: abs(math.log(x / raw)))
        out.append({'filter': f.get('fits') or f.get('aavso'), 'aavso': f.get('aavso'), 'exp': nice, 'n': int(f.get('n', 1))})
    return out


def exposure_class(s, exps):
    """The Type written for N.I.N.A.: one exposure-plan template per class (set up once in Target Scheduler)."""
    v = max((e['exp'] for e in exps), default=0)
    for limit, name in s.get('classes') or [[15, 'VS short'], [60, 'VS medium'], [100000, 'VS long']]:
        if v <= limit:
            return name
    return 'VS long'


def sequence_s(s, exps, series_min=None):
    if series_min:
        return float(series_min) * 60
    t = sum((e['exp'] + float(s.get('readout_s', 5))) * e['n'] for e in exps)
    return t + len(exps) * float(s.get('filter_change_s', 10)) + float(s.get('overhead_s', 120))


def score(t, now, own_last=None):
    """How much this star needs data tonight, and why, in words."""
    why, sc = [], 0.0
    cad = t.get('cadence') or None
    if cad:
        if t.get('last'):
            days = max(0.0, (now - t['last']) / 86400)
            over = days / cad
            sc += min(over, 6.0)
            why.append(f'last AAVSO data {days:.1f} d ago, wanted every {cad:g} d' + (f' ({over:.0f}x overdue)' if over >= 2 else ''))
        else:
            sc += 2.0
            why.append(f'no recent AAVSO data; wanted every {cad:g} d')
    if any('Alert' in x or 'Campaign' in x for x in t.get('sections') or []):
        sc += 2.0
        why.insert(0, 'AAVSO alert or campaign')
    if t.get('priority'):
        sc += 1.5
        why.append('marked high priority')
    if t.get('source') == 'watch list':
        sc += 1.0
    if own_last and cad:
        since = (now - own_last) / 86400
        if since < cad * 0.8:
            sc -= 3.0
            why.append(f'measured here {since:.1f} d ago (cadence {cad:g} d)')
    return round(sc, 2), why


def plan(s, targets, nt, now, own=None):
    """Choose and order tonight's targets: the most needed first, each placed where it is up and the night is free."""
    own = own or {}
    lo, hi = float(s.get('bright_limit', 8.0)), float(s.get('faint_limit', 16.0))
    slots = []
    chosen, left = [], []
    for t in targets:
        mag = expected_mag(t, (own.get(t['name']) or {}).get('mag'))
        t['mag_plan'] = round(mag, 2) if mag is not None else None
        t['score'], t['why'] = score(t, now, (own.get(t['name']) or {}).get('t'))
        if not t.get('photometry', True):
            left.append((t, 'visual or spectroscopy only'))
        elif t.get('conj'):
            left.append((t, 'too close to the Sun'))
        elif not t.get('visible'):
            left.append((t, 'not high enough while it is dark' if not t.get('up') else f"the Moon is {t.get('moon_sep')}° away"))
        elif mag is not None and t.get('max_mag') is not None and t['max_mag'] < lo - 0.5 and (t.get('min_mag') or t['max_mag']) < lo:
            left.append((t, f'too bright for the camera (brighter than {lo:g})'))
        elif mag is not None and mag > hi:
            left.append((t, f'too faint (about {mag:.1f}, limit {hi:g})'))
        else:
            chosen.append(t)
    chosen.sort(key=lambda t: (-t['score'], -(t.get('hours_up') or 0)))
    out = []
    for t in chosen:
        if len(out) >= int(s.get('max_targets', 60)):
            left.append((t, 'the night is full'))
            continue
        exps = exposures(s, t.get('mag_plan'))
        series = t.get('series_min') or (float(s.get('series_min') or 0) if t.get('series') else None) or None
        need = sequence_s(s, exps, series)
        start = None
        for u in sorted(t['up'], key=lambda x: abs(x - t['best'])):                 # as close to its highest point as possible
            if all(u + need <= a or u >= b for a, b in slots) and u + need <= nt['end']:
                start = u
                break
        if start is None:
            left.append((t, 'no free time while it is up'))
            continue
        slots.append((start, start + need))
        out.append({**{k: t.get(k) for k in ('name', 'ra', 'dec', 'type', 'period', 'min_mag', 'max_mag', 'cadence', 'sections', 'source',
                                            'alt_max', 'moon_sep', 'hours_up', 'score', 'why', 'mag_plan', 'info')},
                    'start': start, 'minutes': round(need / 60, 1), 'exposures': exps, 'class': exposure_class(s, exps),
                    'series': bool(series)})
    out.sort(key=lambda x: x['start'])
    used = sum(b - a for a, b in slots)
    return out, [{'name': t['name'], 'why': w, 'score': t.get('score')} for t, w in left], used


# ------------------------------------------------------------------ N.I.N.A. Target Scheduler CSV
def hms(ra):
    h = (ra % 360) / 15
    hh, mm = int(h), int((h - int(h)) * 60)
    ss = round(((h - hh) * 60 - mm) * 60)
    if ss == 60:
        ss, mm = 0, mm + 1
    if mm == 60:
        mm, hh = 0, (hh + 1) % 24
    return f'{hh:02d}h {mm:02d}m {ss:02d}s'


def dms(dec):
    sgn = '-' if dec < 0 else ''
    a = abs(dec)
    dd, mm = int(a), int((a - int(a)) * 60)
    ss = round(((a - dd) * 60 - mm) * 60)
    if ss == 60:
        ss, mm = 0, mm + 1
    if mm == 60:
        mm, dd = 0, dd + 1
    return f'{sgn}{dd:02d}º {mm:02d}\' {ss:02d}"'


def nina_csv(rows):
    """Target Scheduler bulk-import CSV: Type, Name, Ra, Dec, Rotation, ROI (see VARIABLE_STARS.md for the templates)."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator='\r\n')
    w.writerow(['Type', 'Name', 'Ra', 'Dec', 'Rotation', 'ROI'])
    for r in rows:
        w.writerow([('VS series' if r.get('series') else r['class']), r['name'], hms(r['ra']), dms(r['dec']), 0, 100])
    return buf.getvalue()


# ------------------------------------------------------------------ the job
def own_last(con):
    """When this observatory last measured each star, and its last magnitude (from part 2, spacelab/vsphot.py)."""
    try:
        rows = db.rows(con, "SELECT target, MAX(jd) AS jd, mag FROM vs_meas WHERE status != 'rejected' GROUP BY target")
    except Exception:
        return {}
    return {r['target']: {'t': (r['jd'] - 2440587.5) * 86400, 'mag': r['mag']} for r in rows if r['jd']}


def run(ctx, fetch_tt=None, fetch_vsx=None, now=None):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    s = settings(cfg)
    site = s.get('site') or {}
    if not s.get('enabled'):
        return 'variable-star observatory not set up (see VARIABLE_STARS.md)'
    if site.get('lat') is None or site.get('lon') is None:
        return 'variable-star observatory: the observatory location is missing in settings (vstars.site)'
    now = now or time.time()
    targets = target_tool(s, fetch_tt, log)
    seen = {t['name'].upper() for t in targets}
    targets += [t for t in watchlist(s, con, fetch_vsx, log) if t['name'].upper() not in seen]
    if not targets:
        return 'no targets: add a Target Tool API key, an exported Target Tool CSV, or a watch list (see VARIABLE_STARS.md)'
    nt = night(site, now, float(s.get('sun_alt', -12)))
    if not nt:
        return 'no dark time tonight at this latitude'
    visibility(targets, nt, float(s.get('min_alt', 30)), float(s.get('moon_sep_min', 15)))
    rows, left, used = plan(s, targets, nt, now, own_last(con))
    day = datetime.fromtimestamp(nt['start'] + site['lon'] / 15 * 3600, timezone.utc).strftime('%Y-%m-%d')   # the local evening's date
    OUT.mkdir(parents=True, exist_ok=True)
    csv_path = OUT / f'tonight-{day}.csv'
    csv_path.write_text(nina_csv(rows), encoding='utf-8')
    if s.get('nina_import_dir'):
        try:
            d = Path(s['nina_import_dir']).expanduser()
            d.mkdir(parents=True, exist_ok=True)
            (d / 'spacelab-tonight.csv').write_text(nina_csv(rows), encoding='utf-8')
        except Exception as e:
            log(f'  could not copy the plan to {s["nina_import_dir"]}: {e}')
    meta = {'day': day, 'made': time.time(), 'dark': [nt['start'], nt['end']], 'used_s': used, 'targets': rows, 'left': left[:200],
            'csv': str(csv_path.relative_to(config.DATA)), 'n_candidates': len(targets)}
    (OUT / f'tonight-{day}.json').write_text(json.dumps(meta))
    db.kv_set(con, 'vs_plan', meta)
    dark_h = (nt['end'] - nt['start']) / 3600
    log(f'  tonight ({day}): {len(rows)} targets, {used / 3600:.1f} of {dark_h:.1f} dark hours planned; {len(left)} left out')
    return f'{len(rows)} variable stars planned for tonight ({used / 3600:.1f} of {dark_h:.1f} dark hours); {len(left)} left out'
