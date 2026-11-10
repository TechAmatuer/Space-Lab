"""What can the planet search find? Fake planets hidden in real TESS light curves (idea 7 of the plan).

A fake transit (a dip of known depth, period and length) is put into a real light curve before anything is done to it.
Then the light curve goes through exactly what the planet hunter does (flattening, the same 100-day window, the same
period search, the same SDE and signal-to-noise thresholds) and the result is compared with what was hidden. Many such
trials give the share of planets of each size and period the search finds: its completeness. Without this, nobody can
say what the search misses, or how many planets a "nothing found" means were not there.

Honest limit: the light curves kept on this Mac are half-hour averages, while the search itself uses 2-minute and
10-minute data, so short dips (under about 1.5 hours) are found more often by the real search than here. These numbers
are a lower bound. Results build up over runs (monthly, plus whenever started by hand).
"""
import json
import math
import time
import warnings

import numpy as np

from . import config, db

INJ_DIR = config.DATA / 'lc' / 'inj'            # quiet stars' half-hour light curves, kept for this test
PLOT = config.PLOTS / 'injection' / 'completeness.png'
P_BINS = [0.5, 1, 2, 4, 8, 15]                  # days (the hunter searches 0.5-30 d, but needs 2+ dips in 100 days)
D_BINS = [200, 500, 1000, 2000, 5000, 20000]    # ppm (1000 ppm = 0.1%: an Earth-size planet around a 0.3 Sun-radius star)
MATCH = 0.01                                    # found if the period is within 1% (or half / double: counted apart)


def ensure(con):
    con.execute('CREATE TABLE IF NOT EXISTS injections (id INTEGER PRIMARY KEY, tic INTEGER, quiet INTEGER, period REAL, depth REAL, '
                'duration REAL, t0 REAL, found INTEGER, alias INTEGER, p_found REAL, sde REAL, snr REAL, at REAL)')


def keep_curve(tic, t, f):
    """Called by the planet hunter for one star in four that is not a variable star: keep its half-hour light curve."""
    from .variables import binned
    INJ_DIR.mkdir(parents=True, exist_ok=True)
    tb, fb = binned(t, f)
    if len(tb) < 200:
        return None
    p = INJ_DIR / f'TIC{tic}.npz'
    np.savez_compressed(p, t=tb.astype(np.float64), f=fb.astype(np.float32))
    return str(p.relative_to(config.DATA))


def curves(con):
    """Light curves to hide planets in: [(tic, path, quiet, star dict)]."""
    from .variables import VAR_DIR
    stars = {r['tic']: r for r in db.rows(con, 'SELECT tic, rad, mass, teff, tmag FROM tess_targets')}
    out = []
    for d, quiet in ((INJ_DIR, 1), (VAR_DIR, 0)):
        if d.exists():
            for p in sorted(d.glob('TIC*.npz')):
                try:
                    tic = int(p.stem[3:])
                except ValueError:
                    continue
                out.append((tic, p, quiet, stars.get(tic) or {}))
    return out


def transit_shape(t, period, t0, dur, depth):
    """1 - depth inside the dip, with a short ramp in and out (a tenth of the length each side)."""
    ph = np.abs(((t - t0 + 0.5 * period) % period) - 0.5 * period)
    half, ramp = dur / 2, max(dur * 0.1, 1e-4)
    m = np.clip((half - ph) / ramp, 0, 1)
    return 1 - depth * m


def flatten(t, f):
    """The same flattening the hunter uses (lightkurve, window of 0.75 days), then its outlier cleaning."""
    from .tess_hunter import clean
    import lightkurve as lk
    t, f = np.asarray(t, float), np.asarray(f, float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    f = f / np.nanmedian(f)
    e = np.full_like(f, 1.4826 * np.nanmedian(np.abs(np.diff(f))) / math.sqrt(2) or 1e-3)
    dt = float(np.nanmedian(np.diff(t)))
    wl = max(11, int(0.75 / dt) | 1)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        flat = lk.LightCurve(time=t, flux=f, flux_err=e).flatten(window_length=wl, polyorder=2, break_tolerance=max(5, int(0.5 / dt)))
    return clean(np.asarray(flat.time.value, float), np.asarray(flat.flux.value, float), np.asarray(flat.flux_err.value, float))


def trial(t, f, star, period, depth, t0, tc, engine='astropy', search=None):
    """Hide one planet, search as the hunter does, and say whether it was found. Returns a dict."""
    from .tess_hunter import best_window, bin_lc, bls_search, expected_duration
    dur = max(0.03, expected_duration(period, star.get('rad'), star.get('mass')) * 0.85)   # a typical, not a central, crossing
    fi = f * transit_shape(t, period, t0, dur, depth)
    ty, yy, ey = flatten(t, fi)
    w = best_window(ty)
    ts, ys, es = bin_lc(ty[w], yy[w], ey[w], 10 / 1440)
    s = (search or bls_search)(ts, ys, es, tc['min_period'], tc['max_period'], engine)
    res = {'period': period, 'depth': depth, 'duration': dur, 't0': t0, 'found': 0, 'alias': 0, 'p_found': None, 'sde': None, 'snr': None}
    if not s:
        return res
    res.update(p_found=s['period'], sde=round(s['sde'], 2), snr=round(s['snr'], 2))
    if s['sde'] >= tc['sde_min'] and s['snr'] >= tc['snr_min']:
        r = s['period'] / period
        if abs(r - 1) < MATCH:
            res['found'] = 1
        elif abs(r - 0.5) < MATCH / 2 or abs(r - 2) < MATCH * 2:
            res['alias'] = 1
    return res


def grid(rows, quiet=None):
    """Share found per period x depth cell: {'p': P_BINS, 'd': D_BINS, 'n': [[...]], 'found': [[...]]}."""
    n = np.zeros((len(P_BINS) - 1, len(D_BINS) - 1), int)
    k = np.zeros_like(n)
    for r in rows:
        if quiet is not None and r['quiet'] != quiet:
            continue
        i = np.searchsorted(P_BINS, r['period'], side='right') - 1
        j = np.searchsorted(D_BINS, r['depth'] * 1e6, side='right') - 1
        if 0 <= i < n.shape[0] and 0 <= j < n.shape[1]:
            n[i, j] += 1
            k[i, j] += r['found']
    return {'p': P_BINS, 'd': D_BINS, 'n': n.tolist(), 'found': k.tolist()}


def headline(g):
    """Plain sentences from a grid: how often small, medium and big dips are found at short periods."""
    n, k = np.array(g['n']), np.array(g['found'])
    out = []
    for lo, hi, what in ((0, 2, 'shallow dips (0.02-0.1%, about Earth-size around a small red dwarf)'),
                         (2, 3, 'dips of 0.1-0.2%'), (3, 5, 'deep dips (0.2-2%, Neptune- to Jupiter-size)')):
        nn, kk = n[:3, lo:hi].sum(), k[:3, lo:hi].sum()                 # periods 0.5-4 days
        if nn >= 10:
            out.append(f'{100 * kk / nn:.0f}% of {what} with periods of 0.5-4 days ({nn} trials)')
    return out


def plot(rows, path=PLOT):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, axes = plt.subplots(1, 2, figsize=(11, 4.4), dpi=100)
    for ax, q, title in ((axes[0], 1, 'quiet stars'), (axes[1], 0, 'variable (spotted, flaring) stars')):
        g = grid(rows, q)
        n, k = np.array(g['n'], float), np.array(g['found'], float)
        frac = np.where(n > 0, k / np.maximum(n, 1), np.nan)
        im = ax.imshow(frac.T, origin='lower', vmin=0, vmax=1, cmap='viridis', aspect='auto')
        for i in range(n.shape[0]):
            for j in range(n.shape[1]):
                if n[i, j]:
                    ax.text(i, j, f'{100 * frac[i, j]:.0f}%\n({int(n[i, j])})', ha='center', va='center', fontsize=7,
                            color='white' if frac[i, j] < 0.6 else 'black')
        ax.set_xticks(np.arange(len(P_BINS)) - 0.5, [str(x) for x in P_BINS])
        ax.set_yticks(np.arange(len(D_BINS)) - 0.5, [f'{x / 1e4:g}%' for x in D_BINS])
        ax.set_xlabel('period (days)')
        ax.set_ylabel('depth of the dip')
        ax.set_title(f'{title}: {int(k.sum())} of {int(n.sum())} fake planets found', fontsize=10)
    fig.colorbar(im, ax=axes, fraction=0.025, label='share found')
    fig.suptitle('What the planet search finds: fake planets hidden in real TESS light curves', fontsize=11)
    fig.savefig(path)
    plt.close(fig)
    return path


def summary(con):
    ensure(con)
    rows = db.rows(con, 'SELECT quiet, period, depth, found, alias FROM injections')
    if not rows:
        return {'n': 0}
    gq, gv = grid(rows, 1), grid(rows, 0)
    return {'n': len(rows), 'found': sum(r['found'] for r in rows), 'alias': sum(r['alias'] for r in rows),
            'n_quiet': sum(1 for r in rows if r['quiet']), 'quiet': gq, 'variable': gv,
            'headline_quiet': headline(gq), 'headline_variable': headline(gv),
            'plot': str(PLOT.relative_to(config.DATA)) if PLOT.exists() else None, 't': time.time(),
            'note': 'Measured on half-hour light curves; the real search uses 2- and 10-minute data, so short dips are found more often than shown.'}


def run(ctx, search=None, rng=None):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    ensure(con)
    tc = dict(config.DEFAULTS['tess'], **(cfg.get('tess') or {}))
    ic = cfg.get('injection') if isinstance(cfg.get('injection'), dict) else {}
    rng = rng or np.random.default_rng()
    pool = curves(con)
    if not pool:
        return 'no light curves kept yet to hide planets in (the planet hunter keeps one in four as it goes)'
    engine = 'astropy'
    if search is None:
        try:
            from .tess_hunter import calibrate_engine
            engine, _ = calibrate_engine(log)
        except Exception:
            engine = 'astropy'
    n_max = int(ic.get('trials_per_run', 400))
    done = 0
    log(f'  {len(pool)} light curves to hide fake planets in ({sum(1 for x in pool if x[2])} of quiet stars)')
    while done < n_max and ctx.time_left() > 90:
        tic, path, quiet, star = pool[int(rng.integers(len(pool)))]
        try:
            d = np.load(path)
            t, f = d['t'].astype(float), d['f'].astype(float)
        except Exception:
            continue
        period = float(np.exp(rng.uniform(math.log(P_BINS[0]), math.log(P_BINS[-1]))))
        depth = float(np.exp(rng.uniform(math.log(D_BINS[0]), math.log(D_BINS[-1])))) / 1e6
        t0 = float(t[0] + rng.uniform(0, period))
        try:
            r = trial(t, f, star, period, depth, t0, tc, engine, search)
        except Exception as e:
            log(f'  TIC {tic}: trial failed ({type(e).__name__}: {str(e)[:80]})')
            continue
        con.execute('INSERT INTO injections (tic, quiet, period, depth, duration, t0, found, alias, p_found, sde, snr, at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                    (tic, quiet, r['period'], r['depth'], r['duration'], r['t0'], r['found'], r['alias'], r['p_found'], r['sde'], r['snr'], time.time()))
        done += 1
        if done % 25 == 0:
            con.commit()
            log(f'  {done} trials so far')
    con.commit()
    rows = db.rows(con, 'SELECT quiet, period, depth, found, alias FROM injections')
    if rows:
        plot(rows)
    s = summary(con)
    db.kv_set(con, 'injection', s)
    lines = s.get('headline_quiet') or s.get('headline_variable') or []
    return (f'{done} fake planets hidden this run, {s.get("n", 0)} in all; {s.get("found", 0)} found at the right period'
            + (f'. Quiet stars: {lines[0]}' if s.get('headline_quiet') else f'. {lines[0]}' if lines else ''))
