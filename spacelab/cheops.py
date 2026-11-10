"""Answer-key test with ESA's CHEOPS telescope: can this lab's dip finder recover planets that are already known?

CHEOPS looks at one star at a time, mostly stars with well-measured transiting planets, and its light curves are public.
That makes them an answer key. For each chosen visit this job:
  1. predicts, from the published orbit (NASA Exoplanet Archive), when a transit falls inside the visit;
  2. downloads the public light curve (DACE, University of Geneva) and cleans it;
  3. searches it BLIND for the most significant box-shaped dip, without using the predicted time or depth;
  4. compares what it found with the published time and depth.
A dip finder that cannot recover these has no business being trusted on new candidates.

The search is the same statistic as the TESS single-dip search (mean depth in a sliding window over the scatter), with
CHEOPS-specific cleaning: its roll angle changes every 99-minute orbit, which puts a repeating wobble in the brightness.
"""
import json
import math
import time
import urllib.parse

import numpy as np

from . import config, db, net

API = 'https://cheops-webapp-pg.obsuksprd2.unige.ch/'
NEA_TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
JSON_H = {'Accept': 'application/json', 'Content-Type': 'application/json'}
DURATIONS = (0.03, 0.04, 0.055, 0.07, 0.09, 0.11, 0.135, 0.16, 0.19, 0.22, 0.26, 0.3)     # days


# ------------------------------------------------------------------ the search (pure functions, tested offline)
def design(t, roll):
    """Baseline model: a straight line in time plus the roll-angle wobble (first two harmonics)."""
    x = (t - t.mean()) / (np.ptp(t) or 1.0)
    r = np.radians(roll)
    return np.column_stack([np.ones_like(x), x, np.sin(r), np.cos(r), np.sin(2 * r), np.cos(2 * r)])


def scan(t, res, sig, durations=DURATIONS, min_in=8, min_side=8):
    """Most significant dip of any listed duration that has data on both sides. res is flux minus baseline."""
    best = None
    cs = np.concatenate([[0.0], np.cumsum(res)])
    cadence = float(np.median(np.diff(t)))
    for w in durations:
        if w > 0.6 * (t[-1] - t[0]):
            continue
        centers = np.arange(t[0] + w / 2, t[-1] - w / 2, w / 8)
        if not len(centers):
            continue
        lo, hi = np.searchsorted(t, centers - w / 2), np.searchsorted(t, centers + w / 2)
        n = hi - lo
        ok = (n >= max(min_in, 0.35 * w / cadence)) & (lo >= min_side) & (len(t) - hi >= min_side)
        if not ok.any():
            continue
        depth = np.where(ok, -(cs[hi] - cs[lo]) / np.maximum(n, 1), 0.0)
        snr = np.where(ok, depth / (sig / np.sqrt(np.maximum(n, 1))), 0.0)
        i = int(np.argmax(snr))
        if best is None or snr[i] > best['snr']:
            best = {'t0': float(centers[i]), 'duration': float(w), 'depth': float(depth[i]), 'snr': float(snr[i]), 'n_in': int(n[i])}
    return best


def blind_dip(t, y, roll):
    """Find the single most significant dip without being told where it is.
    The baseline is fitted, the best dip found, then the baseline is refitted with that dip left out (twice),
    so the dip does not get partly absorbed into the baseline. Returns (dip or None, residuals, scatter)."""
    X = design(t, roll)
    keep = np.ones(len(t), bool)
    best = res = None
    sig = 0.0
    for _ in range(3):
        if keep.sum() < X.shape[1] + 5:
            break
        coef = np.linalg.lstsq(X[keep], y[keep], rcond=None)[0]
        res = y - X @ coef
        r = res[keep]
        sig = float(1.4826 * np.median(np.abs(r - np.median(r))))
        if sig <= 0:
            break
        cand = scan(t, res, sig)
        if cand is None:
            break
        same = best is not None and abs(cand['t0'] - best['t0']) < 0.1 * cand['duration'] and cand['duration'] == best['duration']
        best = cand
        if same:
            break
        keep = np.abs(t - cand['t0']) > 0.6 * cand['duration']
    return best, res, sig


def clean(p):
    """Usable points of one visit: finite, not flagged by the CHEOPS pipeline, no bright-background or upward outliers."""
    t = np.asarray(p.get('obj_date_bjd_vect') or [], float) + 2400000.0
    y = np.asarray(p.get('photom_flux_vect') or [], float)
    n = min(len(t), len(y))

    def col(name, fill):
        v = np.asarray(p.get(name) or [], float)
        return v[:n] if len(v) >= n else np.full(n, fill)
    t, y = t[:n], y[:n]
    ev, roll, bg = col('photom_event_vect', 0.0), col('photom_roll_angle_vect', 0.0), col('photom_background_vect', 0.0)
    ok = np.isfinite(t) & np.isfinite(y) & np.isfinite(roll) & (ev == 0) & (y > 0)
    if ok.sum() > 20:
        b = bg[ok]
        mad = 1.4826 * np.median(np.abs(b - np.median(b)))
        if mad > 0:
            ok &= ~(bg > np.median(b) + 5 * mad)
        m = np.median(y[ok])
        s = 1.4826 * np.median(np.abs(y[ok] - m))
        ok &= y < m + 5 * s                      # drop upward spikes (cosmic rays), keep every dip
    o = np.argsort(t[ok])
    return t[ok][o], (y[ok] / np.median(y[ok]))[o] if ok.any() else y[ok], roll[ok][o]


def transit_in_visit(pl, start, end):
    """The predicted transit (mid-time, 1-sigma uncertainty in days) that lies fully inside [start, end] with
    out-of-transit data on both sides; None when there is none."""
    P, T0, dur = pl['period'], pl['t0'], pl['dur_h'] / 24.0
    n = round(((start + end) / 2 - T0) / P)
    for k in (n, n - 1, n + 1):
        tc = T0 + k * P
        margin = max(0.5 * dur, 1.5 / 24)
        if start + dur / 2 + margin <= tc <= end - dur / 2 - margin:
            return tc, math.hypot(pl.get('t0_err') or 0.0, k * (pl.get('period_err') or 0.0))
    return None


def compare(pl, tc, tc_sig, dip):
    """Did the blind search find the published transit? Time within a third of the transit length (or 3 sigma of the
    published prediction, or 20 minutes); depth within a factor of two-ish of the published depth."""
    dur = pl['dur_h'] / 24.0
    out = {'depth_lit_ppm': round(pl['depth'] * 1e6), 'dur_lit_h': round(pl['dur_h'], 2), 'eph_sigma_min': round(tc_sig * 1440, 1),
           'recovered': False, 'depth_ok': False}
    if not dip:
        return out
    off = dip['t0'] - tc
    ratio = dip['depth'] / pl['depth'] if pl['depth'] > 0 else None
    out.update({'depth_ppm': round(dip['depth'] * 1e6), 'dur_h': round(dip['duration'] * 24, 2), 'snr': round(dip['snr'], 1),
                'offset_min': round(off * 1440, 1), 'ratio': round(ratio, 2) if ratio is not None else None})
    out['recovered'] = bool(dip['snr'] >= 6 and abs(off) <= max(0.35 * dur, 3 * tc_sig, 20 / 1440))
    out['depth_ok'] = bool(out['recovered'] and ratio is not None and 0.5 <= ratio <= 1.5)
    return out


# ------------------------------------------------------------------ data access
def _params(js):
    out = {}
    for p in (js or {}).get('parameters') or []:
        for k in ('doubleValues', 'intValues', 'stringValues', 'booleanValues'):
            if k in p:
                out[p.get('variableName')] = p[k]
                break
    return out


def known_planets(log):
    """Transiting planets with a published time, period, length and depth good enough to predict a transit."""
    q = ('select pl_name,hostname,ra,dec,pl_orbper,pl_orbpererr1,pl_tranmid,pl_tranmiderr1,pl_trandur,pl_trandep,sy_vmag '
         'from pscomppars where tran_flag=1 and pl_trandep>0.03 and pl_trandur>0.5 and pl_tranmid>2400000 and pl_orbper>0')
    rows = json.loads(net.fetch_text(NEA_TAP, params={'query': q, 'format': 'json'}, timeout=180))
    out = []
    for r in rows:
        try:
            out.append({'name': r['pl_name'], 'host': r['hostname'], 'ra': float(r['ra']), 'dec': float(r['dec']),
                        'period': float(r['pl_orbper']), 'period_err': abs(float(r.get('pl_orbpererr1') or 0)),
                        't0': float(r['pl_tranmid']), 't0_err': abs(float(r.get('pl_tranmiderr1') or 0)),
                        'dur_h': float(r['pl_trandur']), 'depth': float(r['pl_trandep']) / 100.0, 'vmag': r.get('sy_vmag')})
        except (TypeError, ValueError, KeyError):
            continue
    log(f'  {len(out)} transiting planets with published times and depths')
    return out


def visits(log, limit=20000):
    js = json.loads(net.fetch(API + 'search', params={'limit': str(limit), 'filters': '{}', 'sort': '{}'}, headers=JSON_H, timeout=180))
    p = _params(js)
    n = len(p.get('file_key') or [])
    need = ('file_key', 'obj_id_catname', 'ra_deg', 'dec_deg', 'bjd_start', 'bjd_end')
    if not n or any(len(p.get(k) or []) != n for k in need):
        raise RuntimeError('the CHEOPS archive answered in an unexpected shape')
    log(f'  {n} public CHEOPS visits listed')
    return [{k: p[k][i] for k in need} for i in range(n)]


def light_curve(star, file_key):
    body = json.dumps({'aperture': 'default', 'filters': json.dumps({'file_key': {'equal': [file_key]}}), 'sort': '{}'}).encode()
    raw = net.fetch(API + 'photometry/' + urllib.parse.quote(str(star), safe=''), data=body, headers=JSON_H, timeout=240, min_gap=3.0)
    return _params(json.loads(raw))


def pick(planets, vis, done, want):
    """Visits that contain a full, well-predicted transit of a known planet; one planet at a time, least-tested first."""
    cos = lambda d: math.cos(math.radians(d))
    per_planet, by_dec = {}, {}
    for pl in planets:                                   # index by declination so each visit is compared with a few planets only
        by_dec.setdefault(round(pl['dec'] * 10), []).append(pl)
    for v in vis:
        try:
            ra, dec, a, b = float(v['ra_deg']), float(v['dec_deg']), float(v['bjd_start']), float(v['bjd_end'])
        except (TypeError, ValueError):
            continue
        if v['file_key'] in done or not (0.2 < b - a < 6):
            continue
        for pl in [x for k in (-1, 0, 1) for x in by_dec.get(round(dec * 10) + k, ())]:
            if abs(pl['dec'] - dec) > 0.01 or abs((pl['ra'] - ra) * cos(dec)) > 0.01:      # same star (within ~36 arcseconds)
                continue
            hit = transit_in_visit(pl, a, b)
            if hit and hit[1] * 1440 <= 30:
                per_planet.setdefault(pl['name'], []).append((b, v, pl, hit))
    tested = {}
    for d in done.values():
        if d.get('npts'):
            tested[d.get('planet')] = tested.get(d.get('planet'), 0) + 1
    order = sorted(per_planet, key=lambda n: (tested.get(n, 0), -per_planet[n][0][2]['depth']))
    out = []
    for name in order:
        if tested.get(name, 0) >= 2:
            continue
        _, v, pl, hit = sorted(per_planet[name], key=lambda x: -x[0])[0]         # the most recent visit
        out.append((v, pl, hit))
        if len(out) >= want:
            break
    return out


def plot(path, title, t, y, res, dip, tc, verdict):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    h = (t - t[0]) * 24
    fig, ax = plt.subplots(2, 1, figsize=(9, 5.6), sharex=True)
    ax[0].plot(h, (y - 1) * 1e6, '.', ms=2, color='#777')
    ax[0].set_ylabel('as measured (ppm)')
    ax[0].set_title(title, fontsize=10)
    ax[1].plot(h, res * 1e6, '.', ms=2, color='#999')
    nb = max(1, len(t) // 60)
    if len(t) >= 2 * nb:
        k = len(t) // nb * nb
        ax[1].plot(h[:k].reshape(-1, nb).mean(1), res[:k].reshape(-1, nb).mean(1) * 1e6, 'o', ms=3.5, color='#1f6feb')
    if dip:
        a, b = (dip['t0'] - dip['duration'] / 2 - t[0]) * 24, (dip['t0'] + dip['duration'] / 2 - t[0]) * 24
        ax[1].axvspan(a, b, color='#f2994a', alpha=0.25, label='dip found by the blind search')
        ax[1].hlines(-dip['depth'] * 1e6, a, b, color='#f2994a', lw=2)
    ax[1].axvline((tc - t[0]) * 24, color='#2fbf71', ls='--', lw=1.5, label='published transit time')
    ax[1].set_ylabel('wobble removed (ppm)')
    ax[1].set_xlabel(f'hours since the visit began · {verdict}')
    ax[1].legend(loc='lower right', fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=80)
    plt.close(fig)


def summary(items):
    done = [i for i in items if i.get('npts')]
    rec = [i for i in done if i.get('recovered')]
    ratios = sorted(i['ratio'] for i in rec if i.get('ratio') is not None)
    return {'tested': len(done), 'recovered': len(rec), 'depth_ok': sum(1 for i in rec if i.get('depth_ok')),
            'median_ratio': round(ratios[len(ratios) // 2], 2) if ratios else None}


def run(ctx):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    c = cfg.get('cheops') if isinstance(cfg.get('cheops'), dict) else {}
    want = int(c.get('visits_per_run', 6))
    state = db.kv_get(con, 'cheops') or {}
    done = {i['file_key']: i for i in state.get('items') or []}
    planets = known_planets(log)
    todo = pick(planets, visits(log), done, want)
    log(f'  {len(todo)} visit(s) with a full, well-predicted transit to test now')
    for v, pl, (tc, tc_sig) in todo:
        if ctx.time_left() < 120:
            break
        key = v['file_key']
        item = {'file_key': key, 'planet': pl['name'], 'star': v['obj_id_catname'], 'vmag': pl.get('vmag'), 't': time.time(),
                'date': time.strftime('%Y-%m-%d', time.gmtime((float(v['bjd_start']) - 2440587.5) * 86400))}
        try:
            t, y, roll = clean(light_curve(v['obj_id_catname'], key))
            if len(t) < 80:
                raise ValueError(f'only {len(t)} usable points')
            dip, res, sig = blind_dip(t, y, roll)
            item.update(compare(pl, tc, tc_sig, dip))
            item.update({'npts': int(len(t)), 'scatter_ppm': round(sig * 1e6)})
            verdict = ('recovered' if item['recovered'] else 'NOT recovered') + (f": {item.get('depth_ppm')} ppm found, {item['depth_lit_ppm']} ppm published" if dip else '')
            png = config.PLOTS / 'cheops' / f'{key}.png'
            plot(png, f"{pl['name']} seen by CHEOPS on {item['date']} ({key})", t, y, res, dip, tc, verdict)
            item['plot'] = str(png.relative_to(config.DATA))
            log(f"  {pl['name']} {item['date']}: {verdict}; time off by {item.get('offset_min')} min, SNR {item.get('snr')}")
        except Exception as e:
            item['error'] = f'{type(e).__name__}: {str(e)[:120]}'
            log(f"  {pl['name']} ({key}) skipped: {item['error']}")
        done[key] = item
        ctx.progress(f"CHEOPS answer-key test: {len(done)} visits so far")
    items = sorted(done.values(), key=lambda i: -(i.get('t') or 0))[:60]
    out = {'t': time.time(), 'items': items, **summary(items)}
    db.kv_set(con, 'cheops', out)
    return (f"answer-key test: the blind search recovered {out['recovered']} of {out['tested']} known transits "
            f"({out['depth_ok']} with the right depth)") if out['tested'] else 'no CHEOPS visit could be tested this time'
