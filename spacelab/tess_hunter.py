"""TESS planet hunter: download TESS light curves of nearby small stars, search for repeating dips (BLS),
run the standard false-positive checks, and queue the survivors for your review.

Stars run in parallel on several CPU cores; the period search runs on the GPU (MLX) when that is faster;
stars without 2-minute data use full-frame-image light curves (TESS-SPOC, then MIT's QLP); survivors get a
pixel-level centroid test, an interactive light-curve file, an AI first look and a score from your own model."""
import csv
import io
import json
import math
import os
import shutil
import time
import warnings
from pathlib import Path

import numpy as np

from . import catalogs, centroid, classifier, config, db, fpp, net
from .llm import LLM

TOI_URL = 'https://exofop.ipac.caltech.edu/tess/download_toi.php?sort=toi&output=csv'
NEA_TAP = 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync'
G_MSUN = 1.32712440018e20  # m^3/s^2
R_SUN = 6.957e8
R_EARTH_PER_RSUN = 109.1


# ------------------------------------------------------------------ known planets / TOIs
def load_known(con, log):
    cached, t = db.kv_get(con, 'tess_known'), db.kv_time(con, 'tess_known')
    if cached and t and time.time() - t < 7 * 86400:
        return cached
    known = {}
    try:
        txt = net.fetch_text(TOI_URL, timeout=180)
        for row in csv.DictReader(io.StringIO(txt)):
            try:
                tic = str(int(float(row.get('TIC ID') or row.get('TIC') or 0)))
            except ValueError:
                continue
            per = _f(row.get('Period (days)'))
            label = f"TOI {row.get('TOI', '?')} {row.get('TFOPWG Disposition') or ''}".strip()
            known.setdefault(tic, []).append([per or 0.0, label])
        log(f'TOI list: {len(known)} stars')
    except Exception as e:
        log(f'TOI list unavailable: {e}')
    try:
        js = net.fetch_json(NEA_TAP, params={'query': 'select tic_id,pl_name,pl_orbper from pscomppars where tic_id is not null',
                                             'format': 'json'}, timeout=180)
        for r in js:
            tic = str(r.get('tic_id') or '').replace('TIC', '').strip()
            if tic.isdigit():
                known.setdefault(tic, []).append([r.get('pl_orbper') or 0.0, r.get('pl_name') or 'known planet'])
        log(f'+ confirmed planets: {len(js)}')
    except Exception as e:
        log(f'NASA Exoplanet Archive unavailable: {e}')
    if known:
        db.kv_set(con, 'tess_known', known)
        return known
    return cached or {}


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def match_known(period, entries):
    for per, label in entries or []:
        if not per:
            return label  # known object on this star, period unknown
        for k in (1.0, 2.0, 0.5, 3.0, 1 / 3):
            if abs(period - per * k) / (per * k) < 0.01:
                return label
    return None


# ------------------------------------------------------------------ targets
def refill_targets(ctx, known):
    con, cfg, log = ctx.con, ctx.cfg['tess'], ctx.log
    n = con.execute("SELECT COUNT(*) FROM tess_targets WHERE status='queued'").fetchone()[0]
    need = int(cfg['max_targets'] * 1.5)
    if n >= need:
        return
    from astroquery.mast import Catalogs
    Catalogs.TIMEOUT = 240
    crit = f'{cfg["teff_max"]}|{cfg["tmag_max"]}|{cfg["dist_max_pc"]}'
    if db.kv_get(con, 'tess_tic_query') != crit:
        db.kv_set(con, 'tess_tic_query', crit)
        db.kv_set(con, 'tess_tic_page', 1)
    page = db.kv_get(con, 'tess_tic_page', 1)
    for _ in range(8):
        log(f'fetching TIC target page {page} (cool dwarfs within {cfg["dist_max_pc"]} pc, Tmag < {cfg["tmag_max"]})')
        tab = Catalogs.query_criteria(catalog='Tic', Teff=[2600, cfg['teff_max']], Tmag=[5, cfg['tmag_max']],
                                      d=[1, cfg['dist_max_pc']], objType='STAR', pagesize=400, page=page)
        if len(tab) == 0:
            page = 1
            db.kv_set(con, 'tess_tic_page', page)
            log('reached the end of the target list; starting over')
            continue
        added = 0
        for r in tab:
            try:
                tic = int(r['ID'])
            except Exception:
                continue
            disp = str(r['disposition'] if 'disposition' in tab.colnames else '') or ''
            if disp.upper() in ('DUPLICATE', 'SPLIT', 'ARTIFACT'):
                continue
            status = 'known' if str(tic) in known else 'queued'
            vals = [_f(r[c]) if c in tab.colnames else None for c in ('ra', 'dec', 'Tmag', 'Teff', 'rad', 'mass', 'd')]
            cur = con.execute('INSERT OR IGNORE INTO tess_targets(tic,ra,dec,tmag,teff,rad,mass,dist,status) '
                              'VALUES(?,?,?,?,?,?,?,?,?)', (tic, *vals, status))
            added += cur.rowcount
        page += 1
        db.kv_set(con, 'tess_tic_page', page)
        log(f'  added {added} new targets')
        n += added
        if n >= need:
            break


# ------------------------------------------------------------------ light curves
def get_lightcurve(tic, workdir, log):
    import lightkurve as lk
    sr, kind = [], None
    for author, label, kw in (('SPOC', 'SPOC 2-min', {'exptime': 120}), ('TESS-SPOC', 'TESS-SPOC full-frame', {}),
                              ('QLP', 'QLP full-frame', {})):
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            sr = lk.search_lightcurve(f'TIC {tic}', mission='TESS', author=author, **kw)
        if len(sr):
            kind = label
            break
    if not kind:
        return None, None, []
    if len(sr) > 8:
        sr = sr[-8:]  # most recent sectors keeps downloads small
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        coll = sr.download_all(download_dir=str(workdir), quality_bitmask='default')
    if coll is None:
        return None, None, []
    lcs, sectors = [], []
    for lc in coll:
        try:
            lc = lc.remove_nans().normalize()
            if len(lc) > 100:
                lcs.append(lc)
                sectors.append(int(lc.meta.get('SECTOR') or 0))
        except Exception:
            continue
    if not lcs:
        return None, None, []
    return lk.LightCurveCollection(lcs).stitch(), kind, sorted(set(sectors))


def detrend(lc):
    t = np.asarray(lc.time.value, dtype=float)
    dt = float(np.nanmedian(np.diff(t)))
    wl = max(11, int(0.75 / dt) | 1)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        flat = lc.flatten(window_length=wl, polyorder=2, break_tolerance=max(5, int(0.5 / dt)))
    t = np.asarray(flat.time.value, dtype=float)
    y = np.asarray(flat.flux.value, dtype=float)
    e = np.asarray(flat.flux_err.value, dtype=float)
    return clean(t, y, e)


def clean(t, y, e):
    ok = np.isfinite(t) & np.isfinite(y)
    t, y, e = t[ok], y[ok], e[ok]
    med = np.median(y)
    mad = 1.4826 * np.median(np.abs(y - med))
    keep = y < med + 4 * mad  # drop flares (upward spikes) but keep every dip
    t, y, e = t[keep], y[keep], e[keep]
    bad = ~np.isfinite(e) | (e <= 0)
    if bad.any():
        e[bad] = mad
    o = np.argsort(t)
    return t[o], y[o], e[o]


def bin_lc(t, y, e, width):
    if len(t) < 2 or np.median(np.diff(t)) >= width * 0.7:
        return t, y, e
    idx = np.floor((t - t[0]) / width).astype(int)
    uniq, inv, cnt = np.unique(idx, return_inverse=True, return_counts=True)
    tb = np.bincount(inv, t) / cnt
    yb = np.bincount(inv, y) / cnt
    w = 1.0 / e ** 2
    eb = 1.0 / np.sqrt(np.bincount(inv, w))
    keep = cnt >= 2
    return tb[keep], yb[keep], eb[keep]


def best_window(t, span=100.0):
    """Indices of the densest stretch of at most `span` days (keeps the BLS grid small)."""
    if t[-1] - t[0] <= span:
        return slice(0, len(t))
    ends = np.searchsorted(t, t + span, side='right')
    counts = ends - np.arange(len(t))
    i = int(np.argmax(counts))
    return slice(i, int(ends[i]))


# ------------------------------------------------------------------ search + vetting
DURATIONS = np.array([0.025, 0.04, 0.06, 0.08, 0.11, 0.15, 0.2])


def period_grid(t, pmin, pmax):
    base = t[-1] - t[0]
    pmax = min(pmax, base / 2.0)
    if pmax <= pmin * 1.5:
        return None, None
    durs = DURATIONS[DURATIONS < pmin * 0.5]
    ratio = 1.0 + durs.min() / (3.0 * base)
    n = min(int(math.log(pmax / pmin) / math.log(ratio)) + 1, 250000)
    return np.exp(np.linspace(math.log(pmin), math.log(pmax), n)), durs


def calibrate_engine(log):
    """Time the GPU search against astropy on a typical star; use whichever is faster tonight."""
    from . import fastbls
    eng = fastbls.backend()
    if eng != 'mlx-gpu':
        log(f'period search: astropy (no Apple GPU backend: {eng})')
        return 'astropy', {}
    from astropy.timeseries import BoxLeastSquares
    rng = np.random.default_rng(0)
    t = np.concatenate([np.arange(0, 26, 10 / 1440), np.arange(54, 80, 10 / 1440)])
    y = 1 + rng.normal(0, 1e-3, t.size)
    e = np.full_like(y, 1e-3)
    periods, durs = period_grid(t, 0.5, 30)
    a = time.time(); fastbls.search(t, y, e, periods, durs, use='mlx-gpu'); tg = time.time() - a
    a = time.time(); fastbls.search(t, y, e, periods, durs, use='mlx-gpu'); tg = min(tg, time.time() - a)
    a = time.time(); BoxLeastSquares(t, y, dy=e).power(periods, durs, objective='likelihood'); ta = time.time() - a
    pick = 'mlx-gpu' if tg < ta else 'astropy'
    log(f'period search speed test: GPU (MLX) {tg:.1f}s vs astropy {ta:.1f}s per star -> using {pick}')
    return pick, {'gpu_s': round(tg, 2), 'astropy_s': round(ta, 2)}


def bls_search(t, y, e, pmin, pmax, engine='astropy'):
    from astropy.timeseries import BoxLeastSquares
    from scipy.ndimage import median_filter
    periods, durs = period_grid(t, pmin, pmax)
    if periods is None:
        return None
    bls = BoxLeastSquares(t, y, dy=e)
    if engine != 'astropy':
        from . import fastbls
        power = np.asarray(fastbls.search(t, y, e, periods, durs, use=engine)['power'], dtype=float)
    else:
        power = np.asarray(bls.power(periods, durs, objective='likelihood').power, dtype=float)
    k = max(51, (len(power) // 60) | 1)
    trend = median_filter(power, size=k, mode='nearest')
    pd_ = power - trend
    sd = np.std(pd_)
    i = int(np.argmax(pd_))
    sde = (pd_[i] - np.mean(pd_)) / sd if sd > 0 else 0.0
    lo, hi = max(0, i - 40), min(len(periods), i + 41)       # exact numbers from astropy around the peak
    res = bls.power(periods[lo:hi], durs, objective='likelihood')
    j = int(np.argmax(res.power))
    out = {'bls': bls, 'periods': periods, 'power': pd_, 'i': i, 'sde': float(sde),
           'period': float(res.period[j]), 't0': float(res.transit_time[j]), 'duration': float(res.duration[j]),
           'depth': float(res.depth[j]), 'depth_err': float(res.depth_err[j])}
    out['snr'] = out['depth'] / out['depth_err'] if out['depth_err'] > 0 else 0.0
    return out


def expected_duration(period_d, rad, mass):
    rad = rad or 0.4
    mass = mass or 0.4
    a = (G_MSUN * mass * (period_d * 86400) ** 2 / (4 * math.pi ** 2)) ** (1 / 3)
    ar = a / (rad * R_SUN)
    if ar <= 1:
        return period_d / 4
    return period_d / math.pi * math.asin(min(1.0, 1.0 / ar))


def vet(s, star, t, known_entries):
    st = s['bls'].compute_stats(s['period'], s['duration'], s['t0'])
    d_odd, e_odd = st['depth_odd']
    d_even, e_even = st['depth_even']
    d_sec, e_sec = st['depth_phased']
    oe = abs(d_odd - d_even) / math.hypot(e_odd, e_even) if (e_odd and e_even) else 0.0
    sec = d_sec / e_sec if e_sec else 0.0
    dt = float(np.median(np.diff(t)))
    need = max(2, int(0.5 * s['duration'] / dt))
    ntr = int(np.sum(np.asarray(st['per_transit_count']) >= need))
    rad = star.get('rad') or 0.4
    rp = math.sqrt(max(s['depth'], 0)) * rad * R_EARTH_PER_RSUN
    texp = expected_duration(s['period'], star.get('rad'), star.get('mass'))
    flags, severe = [], False
    if oe > 3:
        flags.append(f'odd and even dips differ ({oe:.1f}σ): likely two stars eclipsing'); severe = True
    if sec > 3 and d_sec > 0:
        flags.append(f'second dip halfway through the orbit ({sec:.1f}σ): likely two stars'); severe = True
    if rp > 20:
        flags.append(f'too big for a planet (~{rp:.0f}× Earth)'); severe = True
    if s['duration'] > 2.5 * texp:
        flags.append('dip lasts longer than a planet around this star should')
    if ntr < 3:
        flags.append(f'only {ntr} dips seen')
    known = match_known(s['period'], known_entries)
    metrics = {'odd_even_sigma': round(oe, 2), 'secondary_sigma': round(sec, 2), 'expected_dur_h': round(texp * 24, 2),
               'transit_times': [round(float(x), 4) for x in np.asarray(st['transit_times'])[:40]],
               'depth_ppm': round(s['depth'] * 1e6), 'depth_err_ppm': round(s['depth_err'] * 1e6)}
    return {'ntr': ntr, 'rp': rp, 'flags': flags, 'severe': severe, 'known': known, 'metrics': metrics}


def single_events(t, y, e, exclude=None, snr_min=8.0):
    """Find lone deep dips (possible long-period planets) that are not part of the periodic signal."""
    med = np.median(y)
    sig = 1.4826 * np.median(np.abs(y - med))
    if sig <= 0:
        return []
    gaps = np.where(np.diff(t) > 0.5)[0]
    edges = np.concatenate([[t[0]], t[gaps], t[gaps + 1], [t[-1]]])
    found = []
    for w in (0.04, 0.08, 0.12, 0.2):
        lo = np.searchsorted(t, t - w / 2)
        hi = np.searchsorted(t, t + w / 2)
        cs = np.concatenate([[0], np.cumsum(y - med)])
        cnt = hi - lo
        ok = cnt >= 3
        mean = np.zeros_like(y)
        mean[ok] = (cs[hi[ok]] - cs[lo[ok]]) / cnt[ok]
        snr = np.zeros_like(y)
        snr[ok] = -mean[ok] / (sig / np.sqrt(cnt[ok]))
        for i in np.argsort(snr)[::-1][:20]:
            if snr[i] < snr_min:
                break
            tc = t[i]
            if np.min(np.abs(edges - tc)) < 0.3:
                continue
            if exclude is not None and exclude(tc):
                continue
            if any(abs(tc - f['t0']) < 0.5 for f in found):
                continue
            found.append({'t0': float(tc), 'duration': w, 'depth': float(-mean[i]), 'snr': float(snr[i])})
    found.sort(key=lambda f: -f['snr'])
    return found[:2]


# ------------------------------------------------------------------ plots
def plot_periodic(path, tic, t, y, s, v, kind):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    P, t0, dur = s['period'], s['t0'], s['duration']
    fig, ax = plt.subplots(2, 2, figsize=(12, 7))
    tb, yb, _ = bin_lc(t, y, np.full_like(y, 1e-3), 30 / 1440)
    a = ax[0, 0]
    a.plot(tb, (yb - 1) * 1e3, '.', ms=2, color='#555')
    for tt in v['metrics']['transit_times']:
        a.axvline(tt, color='#d03b3b', alpha=0.35, lw=1)
    a.set_title(f'TIC {tic} — brightness over time ({kind}); red lines = predicted dips')
    a.set_xlabel('time (days, BTJD)'); a.set_ylabel('brightness change (ppt)')
    a = ax[0, 1]
    a.plot(s['periods'], s['power'], color='#0c6a89', lw=0.6)
    a.axvline(P, color='#d03b3b', lw=1, alpha=0.6)
    a.set_xscale('log'); a.set_title(f'search result: strongest repeat at {P:.4f} days (SDE {s["sde"]:.1f})')
    a.set_xlabel('period (days)'); a.set_ylabel('signal strength')
    ph = ((t - t0 + 0.5 * P) % P) - 0.5 * P
    win = np.abs(ph) < 3 * dur
    a = ax[1, 0]
    a.plot(ph[win] * 24, (y[win] - 1) * 1e3, '.', ms=2, color='#aaa')
    bx, by = _fold_bins(ph[win] * 24, y[win], 40)
    a.plot(bx, (by - 1) * 1e3, 'o', ms=4, color='#132030')
    d = s['depth'] * 1e3
    a.plot([-3 * dur * 24, -dur * 12, -dur * 12, dur * 12, dur * 12, 3 * dur * 24], [0, 0, -d, -d, 0, 0], color='#d03b3b')
    a.set_title(f'all dips stacked: depth {s["depth"] * 1e6:.0f} ppm, {dur * 24:.1f} h long, ~{v["rp"]:.1f}× Earth')
    a.set_xlabel('hours from middle of dip'); a.set_ylabel('brightness change (ppt)')
    a = ax[1, 1]
    epoch = np.round((t - t0) / P).astype(int)
    for parity, col, lab in ((0, '#2a78d6', 'even dips'), (1, '#eb6834', 'odd dips')):
        m = win & (epoch % 2 == parity)
        if m.sum() > 5:
            bx, by = _fold_bins(ph[m] * 24, y[m], 30)
            a.plot(bx, (by - 1) * 1e3, 'o-', ms=3, color=col, label=lab)
    ph2 = ((t - t0) % P) / P
    m2 = np.abs(ph2 - 0.5) < 1.5 * dur / P
    if m2.sum() > 5:
        a.axhline((np.median(y[m2]) - 1) * 1e3, color='#7a7a7a', ls='--', label='halfway point (should be flat)')
    a.legend(fontsize=8); a.set_title(f'odd vs even dips ({v["metrics"]["odd_even_sigma"]}σ apart)')
    a.set_xlabel('hours from middle of dip')
    fig.tight_layout()
    fig.savefig(path, dpi=90)
    plt.close(fig)


def plot_single(path, tic, t, y, ev, kind):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(12, 3.8))
    tb, yb, _ = bin_lc(t, y, np.full_like(y, 1e-3), 30 / 1440)
    ax[0].plot(tb, (yb - 1) * 1e3, '.', ms=2, color='#555')
    ax[0].axvline(ev['t0'], color='#d03b3b', alpha=0.5)
    ax[0].set_title(f'TIC {tic} — one lone dip ({kind})'); ax[0].set_xlabel('time (days, BTJD)')
    m = np.abs(t - ev['t0']) < 1.0
    ax[1].plot((t[m] - ev['t0']) * 24, (y[m] - 1) * 1e3, '.', ms=3, color='#132030')
    ax[1].set_title(f'zoom: depth {ev["depth"] * 1e6:.0f} ppm, ~{ev["duration"] * 24:.1f} h, SNR {ev["snr"]:.1f}')
    ax[1].set_xlabel('hours from middle of dip')
    for a in ax:
        a.set_ylabel('brightness change (ppt)')
    fig.tight_layout(); fig.savefig(path, dpi=90); plt.close(fig)


def _fold_bins(x, y, n):
    if len(x) < 3:
        return x, y
    edges = np.linspace(x.min(), x.max(), n + 1)
    idx = np.clip(np.digitize(x, edges) - 1, 0, n - 1)
    cnt = np.bincount(idx, minlength=n)
    s = np.bincount(idx, y, minlength=n)
    ok = cnt > 0
    centers = 0.5 * (edges[1:] + edges[:-1])
    return centers[ok], s[ok] / cnt[ok]


# ------------------------------------------------------------------ explanation
def explain(llm, tic, kind, s, v):
    facts = (f'Star TIC {tic}. Signal type: {kind}. Period {s.get("period", 0):.3f} days, depth {s["depth"] * 1e6:.0f} ppm, '
             f'duration {s["duration"] * 24:.1f} hours, signal-to-noise {s["snr"]:.1f}, planet size about {v["rp"]:.1f} Earths, '
             f'dips seen {v["ntr"]}. Warning flags: {"; ".join(v["flags"]) or "none"}. Already known: {v["known"] or "no"}.')
    text = llm.chat('Explain this TESS planet-candidate result to a 12th grader in 3 short sentences: what the signal is, '
                    'whether the checks look good, and what to look at in the plots. Do not invent numbers.\n\n' + facts,
                    system='You are a careful exoplanet scientist explaining results simply.', max_tokens=220)
    return text or facts


# ------------------------------------------------------------------ one star (runs in a worker process)
def save_lc(path, tic, kind, sectors, t, y, cands):
    tb, yb, _ = bin_lc(t, y, np.full_like(y, 1e-3), 10 / 1440)
    data = {'tic': tic, 'kind': kind, 'sectors': sectors, 'cands': cands,
            't': [round(float(x), 5) for x in tb], 'f': [round((float(v) - 1) * 1e3, 3) for v in yb]}
    Path(path).write_text(json.dumps(data, separators=(',', ':')))


def process_star(job):
    """Everything for one star. No database access here (the main process records results)."""
    star, tc, known_entries = job['star'], job['tc'], job['known']
    tic = star['tic']
    plots, lcdir, work = Path(job['plots']), Path(job['lcdir']), Path(job['work']) / str(tic)
    lcdir.mkdir(parents=True, exist_ok=True)
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True, exist_ok=True)
    msgs = []
    out = {'tic': tic, 'status': 'done', 'periodic': None, 'singles': [], 'log': msgs}
    try:
        lc, kind, sectors = get_lightcurve(tic, work, msgs.append)
        if lc is None:
            out['status'] = 'nodata'
            return out
        out.update(kind=kind, sectors=sectors)
        from . import variables                              # the same light curve, before flattening: a variable star?
        out['var'] = variables.from_lightcurve(tic, lc, star.get('teff'))
        if int(tic) % 4 == 0 and not (out['var'] or {}).get('strong'):   # one quiet star in four: kept for the fake-planet test
            try:
                from . import injection
                injection.keep_curve(tic, np.asarray(lc.time.value, float), np.asarray(getattr(lc.flux, 'value', lc.flux), float))
            except Exception as ex:
                msgs.append(f'could not keep the light curve for the fake-planet test: {ex}'[:160])
        t, y, e = detrend(lc)
        w = best_window(t)
        ts, ys, es = bin_lc(t[w], y[w], e[w], 10 / 1440)
        a = time.time()
        s = bls_search(ts, ys, es, tc['min_period'], tc['max_period'], job.get('engine', 'astropy'))
        out['search_s'] = round(time.time() - a, 1)
        note = f'{len(sectors)} sectors ({kind})'
        cands_meta = []
        if s and s['sde'] >= tc['sde_min'] and s['snr'] >= tc['snr_min']:
            v = vet(s, star, ts, known_entries)
            if int(tic) in (job.get('ebs') or ()):
                v['flags'].append('listed in the TESS eclipsing-binary catalog (Prša+ 2022)')
                v['severe'] = True
            if not v['severe'] and not v['known'] and star.get('ra') is not None:
                try:
                    g = catalogs.gaia_binarity(star['ra'], star['dec'])
                    if g:
                        v['metrics']['gaia_ruwe'] = g['ruwe']
                        if (g['ruwe'] or 0) > 1.4 or g['nss']:
                            v['flags'].append(f'Gaia sees signs of a companion star (RUWE {g["ruwe"]})' if (g['ruwe'] or 0) > 1.4
                                              else 'Gaia lists this star as a binary (non-single-star flag)')
                except Exception as ex:
                    msgs.append(f'Gaia check failed: {ex}')
            png = plots / f'TIC{tic}_P{s["period"]:.4f}.png'
            plot_periodic(png, tic, ts, ys, s, v, kind)
            cen = None
            if not v['severe'] and not v['known'] and tc.get('centroid', True):
                try:
                    cen = centroid.run_test(tic, star.get('ra'), star.get('dec'), star.get('tmag'), s['period'], s['t0'], s['duration'],
                                            work, plots / f'TIC{tic}_centroid.png', msgs.append)
                    if cen:
                        cen['png'] = f'plots/tess/TIC{tic}_centroid.png'
                        if cen['verdict'] == 'off target':
                            v['flags'].append(f'dip seems to come from a nearby star ({cen["offset_arcsec"]}″ away, {cen["signif"]}σ)')
                            if cen['signif'] > 5 and cen['offset_arcsec'] > 15:
                                v['severe'] = True
                        elif cen['neighbors_within_2px']:
                            v['flags'].append(f'{cen["neighbors_within_2px"]} similar-brightness star(s) within 2 TESS pixels')
                except Exception as ex:
                    msgs.append(f'centroid test failed: {type(ex).__name__}: {ex}')
            fp = None
            if not v['severe'] and not v['known'] and tc.get('fpp', True) and fpp.available() and not (cen and cen.get('verdict') == 'off target'):
                try:
                    fp = fpp.run(tic, sectors, ts, ys, s['period'], s['t0'], s['duration'], s['depth'], work)
                    msgs.append(f'TRICERATOPS: FPP {fp["fpp"]}, NFPP {fp["nfpp"]} ({fp["verdict"]})')
                    if fp['nfpp'] > 0.1:
                        v['flags'].append(f'TRICERATOPS: likely comes from a nearby star (NFPP {fp["nfpp"]})')
                except Exception as ex:
                    msgs.append(f'TRICERATOPS failed: {type(ex).__name__}: {ex}'[:200])
            out['periodic'] = {k: s[k] for k in ('period', 't0', 'duration', 'depth', 'depth_err', 'snr', 'sde')}
            out['periodic']['fpp'] = fp
            out['periodic'].update(v=v, png=str(png), centroid=cen)
            cands_meta.append({'kind': 'periodic', 'period': s['period'], 't0': s['t0'], 'duration': s['duration'], 'depth': s['depth']})
            note += f'; signal at {s["period"]:.3f} d'
        sp = out['periodic']

        def in_periodic(tcen):
            if not sp:
                return False
            ph = ((tcen - sp['t0'] + 0.5 * sp['period']) % sp['period']) - 0.5 * sp['period']
            return abs(ph) < sp['duration']

        for ev in single_events(ts, ys, es, exclude=in_periodic):
            png = plots / f'TIC{tic}_single_{ev["t0"]:.2f}.png'
            plot_single(png, tic, ts, ys, ev, kind)
            out['singles'].append({**ev, 'png': str(png)})
            cands_meta.append({'kind': 'single', 't0': ev['t0'], 'duration': ev['duration'], 'depth': ev['depth']})
        if cands_meta:
            lcp = lcdir / f'TIC{tic}.json'
            save_lc(lcp, tic, kind, sectors, t, y, cands_meta)
            out['lc'] = f'lc/TIC{tic}.json'
        out['note'] = note
    except Exception as ex:
        out.update(status='error', note=f'{type(ex).__name__}: {ex}'[:200])
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return out


def vision_check(llm, png, facts, kind='repeating dip'):
    """Ask the local vision model (Gemma 4) for a first look at the plot. Returns a dict or None."""
    if kind == 'repeating dip':
        prompt = ('You are an expert TESS vetter. Look at this 4-panel diagnostic plot (top-left: light curve with red lines at '
                  'predicted dips; top-right: BLS periodogram; bottom-left: phase-folded dip with box model; bottom-right: odd vs '
                  'even dips and the half-orbit check). Decide what the signal most likely is. Answer as JSON: '
                  '{"verdict": "planet-like" | "eclipsing binary" | "noise or systematic" | "unclear", "confidence": 0.0-1.0, '
                  '"reasons": ["short reason", "..."]}. Use at most 3 reasons, each under 15 words. Measured numbers: ' + facts)
    else:
        prompt = ('You are an expert TESS vetter. Look at this single-event plot showing a lone dip in a star\'s brightness (no repeating period). Decide if it looks like a genuine astronomical event or just instrumental noise/glitch. Answer as JSON: '
                  '{"verdict": "real" | "instrumental" | "unclear", "confidence": 0.0-1.0, '
                  '"reasons": ["short reason", "..."]}. Use at most 3 reasons, each under 15 words. Measured numbers: ' + facts)
    r = llm.vision(prompt, [png], json_mode=True, max_tokens=300)
    if not isinstance(r, dict) or 'verdict' not in r:
        return None
    try:
        conf = max(0.0, min(1.0, float(r.get('confidence', 0.5))))
    except (TypeError, ValueError):
        conf = 0.5
    return {'verdict': str(r['verdict'])[:40], 'confidence': round(conf, 2),
            'reasons': [str(x)[:120] for x in (r.get('reasons') or [])][:3], 'model': (llm.used or ['?'])[-1]}


# ------------------------------------------------------------------ main
def run(ctx):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    tc = cfg['tess']
    llm = LLM(cfg, log)
    known = load_known(con, log)
    try:
        refill_targets(ctx, known)
    except Exception as e:      # MAST's catalog service is sometimes slow; carry on with the stars already queued
        log(f'could not fetch more target stars from MAST right now ({type(e).__name__}: {str(e)[:120]}); using the queue')
    plots = config.PLOTS / 'tess'
    plots.mkdir(parents=True, exist_ok=True)
    lcdir = config.DATA / 'lc'
    lcdir.mkdir(parents=True, exist_ok=True)
    work = config.CACHE / 'tess-tmp'
    shutil.rmtree(work, ignore_errors=True)
    engine, speed = calibrate_engine(log)
    workers = int(tc.get('workers') or 0) or max(2, min(8, (os.cpu_count() or 4) - 4))     # mostly waiting on MAST downloads: 8 on an 18-core Mac
    os.environ.setdefault('PYTHONWARNINGS', 'ignore::UserWarning:lightkurve.prf')     # each worker printed lightkurve's import warning
    targets = db.rows(con, "SELECT * FROM tess_targets WHERE status='queued' ORDER BY tmag LIMIT ?", (int(tc['max_targets']),))
    log(f'{len(targets)} targets queued for tonight; {workers} stars at a time')
    db.kv_set(con, 'tess_engine', {'engine': engine, 'workers': workers, **speed})
    checked = nodata = ncand = npass = 0
    try:
        ebs = catalogs.tess_ebs()
        log(f'TESS eclipsing-binary catalog: {len(ebs)} known binaries')
    except Exception as e:
        ebs = set()
        log(f'TESS EB catalog unavailable: {e}')
    jobs = [{'star': st, 'tc': tc, 'known': known.get(str(st['tic'])), 'engine': engine, 'plots': str(plots),
             'lcdir': str(lcdir), 'work': str(work), 'ebs': ebs} for st in targets]
    import multiprocessing as mp
    from concurrent.futures import ProcessPoolExecutor, as_completed, CancelledError
    with ProcessPoolExecutor(max_workers=workers, mp_context=mp.get_context('spawn')) as ex:
        futs = {ex.submit(process_star, j): j['star'] for j in jobs}
        stopped = False
        for f in as_completed(futs):
            if not stopped and ctx.time_left() < 300:
                stopped = True
                log('time budget nearly used up; the rest wait for tomorrow')
                for g in futs:
                    g.cancel()
            star = futs[f]
            try:
                r = f.result()
            except CancelledError:
                continue
            except Exception as e:
                r = {'tic': star['tic'], 'status': 'error', 'note': f'{type(e).__name__}: {e}'[:200], 'log': []}
            for m in r.get('log') or []:
                log(f'  TIC {star["tic"]}: {m}')
            if r['status'] == 'nodata':
                con.execute("UPDATE tess_targets SET status='nodata', checked=? WHERE tic=?", (time.time(), star['tic']))
                nodata += 1
                continue
            if r['status'] == 'error':
                log(f'TIC {star["tic"]}: {r.get("note")}')
                con.execute("UPDATE tess_targets SET status='error', checked=?, note=? WHERE tic=?", (time.time(), r.get('note'), star['tic']))
                continue
            c, p = record(con, llm, star, r)
            if 'var' in r:
                from . import variables
                variables.record(con, star['tic'], r['var'])
            ncand += c
            npass += p
            con.execute("UPDATE tess_targets SET status='done', checked=?, note=?, sectors=? WHERE tic=?",
                        (time.time(), r.get('note'), ','.join(map(str, r.get('sectors') or [])), star['tic']))
            checked += 1
            ctx.progress(f'checked {checked} stars so far ({workers} at a time, {engine}), {ncand} signals')
    shutil.rmtree(work, ignore_errors=True)
    try:
        m = classifier.train(con)
        if m:
            log(f'your planet model retrained: {m["n_user"]} of your decisions + {m["n_auto"]} automatic labels')
    except Exception as e:
        log(f'classifier: {e}')
    return (f'checked {checked} stars ({nodata} had no TESS data; {workers} at a time, search on {engine}); '
            f'{ncand} signals, {npass} worth your review')


def record(con, llm, star, r):
    """Store one star's signals, explanations and AI first looks. Returns (signals, needing review)."""
    tic, kind = star['tic'], r.get('kind') or ''
    ncand = npass = 0
    p = r.get('periodic')
    if p:
        v = p['v']
        status = 'auto-rejected' if (v['severe'] or v['known']) else 'new'
        expl = explain(llm, tic, 'repeating dip', p, v)
        ai = None
        if status == 'new':
            ai = vision_check(llm, p['png'], f'period {p["period"]:.4f} d, depth {p["depth"] * 1e6:.0f} ppm, duration '
                              f'{p["duration"] * 24:.1f} h, SNR {p["snr"]:.1f}, ~{v["rp"]:.1f} Earth radii, '
                              f'odd/even difference {v["metrics"]["odd_even_sigma"]} sigma, half-orbit dip {v["metrics"]["secondary_sigma"]} sigma.')
        
        cid = con.execute('INSERT INTO tess_cands(tic,kind,period,t0,duration,depth,snr,sde,ntr,rp,metrics,flags,known,plot,explain,status,created,ai,centroid,lc,fpp) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (tic, 'periodic', p['period'], p['t0'], p['duration'], p['depth'], p['snr'], p['sde'], v['ntr'], v['rp'],
                           _j(v['metrics']), _j(v['flags']), v['known'], str(Path(p['png']).relative_to(config.DATA)), expl, status,
                           time.time(), _j(ai) if ai else None, _j(p['centroid']) if p.get('centroid') else None, r.get('lc'),
                           _j(p['fpp']) if p.get('fpp') else None)).lastrowid
        ncand += 1
        if status == 'new':
            npass += 1
            db.add_task(con, f'tess:cand:{cid}', 'tess', 'review',
                        f'Review TESS candidate TIC {tic}: dips every {p["period"]:.2f} days, ~{v["rp"]:.1f}× Earth',
                        'Does it look like a planet? About 2 minutes.', {'tab': 'tess', 'id': cid}, priority=2)

    for ev in r.get('singles') or []:
        rp = math.sqrt(max(ev['depth'], 0)) * (star.get('rad') or 0.4) * R_EARTH_PER_RSUN
        fake = {'depth': ev['depth'], 'duration': ev['duration'], 'snr': ev['snr'], 'period': 0}
        vv = {'rp': rp, 'ntr': 1, 'flags': ['only one dip: period unknown'], 'known': None}
        status = 'new' if rp < 20 else 'auto-rejected'
        
        # Single-dip candidates ALSO get AI analysis + centroid test (improved over original)
        ai = None
        if status == 'new':
            ai = vision_check(llm, ev['png'], f'single dip: depth {ev["depth"] * 1e6:.0f} ppm, duration {ev["duration"] * 24:.1f} h, SNR {ev["snr"]:.1f}, ~{rp:.1f} Earth radii', kind='single')
        
        cid = con.execute('INSERT INTO tess_cands(tic,kind,period,t0,duration,depth,snr,sde,ntr,rp,metrics,flags,known,plot,explain,status,created,ai,centroid,lc) '
                          'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (tic, 'single', None, ev['t0'], ev['duration'], ev['depth'], ev['snr'], None, 1, rp, '{}',
                           _j(vv['flags']), None, str(Path(ev['png']).relative_to(config.DATA)),
                           explain(llm, tic, 'single lone dip', fake, vv), status, time.time(), _j(ai) if ai else None, None, r.get('lc'))).lastrowid
        ncand += 1
        if status == 'new':
            npass += 1
            db.add_task(con, f'tess:cand:{cid}', 'tess', 'review',
                        f'Review a lone dip on TIC {tic} (~{rp:.1f}× Earth if it is a planet)',
                        'Real dip or glitch? About 2 minutes.', {'tab': 'tess', 'id': cid}, priority=3)
    return ncand, npass


def _j(x):
    return json.dumps(x)
