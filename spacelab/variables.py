"""Variable stars in the TESS light curves this lab already downloads: find them, check whether anyone has catalogued them,
and prepare a submission to the AAVSO's Variable Star Index (VSX) for the ones nobody has. Nothing is ever submitted from
here: VSX needs your own login, and a person must look first.

How it works:
  1. Every light curve the TESS planet hunter downloads is also searched for repeating brightness changes (Lomb-Scargle
     periodogram on the light curve before it is flattened for the planet search). Stars checked before this existed are
     downloaded again, a few at a time (job "variables").
  2. A clear, repeating signal (false-alarm chance below 1e-10, amplitude well above the noise, at least three cycles) is
     checked against three catalogues of known variable stars: VSX itself, Gaia DR3's variability classification, and
     ASAS-SN's variable stars. If a catalogue cannot be reached the star waits; "could not check" never counts as "new".
  3. A TESS pixel is 21 arcseconds wide, so the light of nearby stars mixes in. Every Gaia star within two pixels is
     weighed: if a neighbour is bright enough to produce the signal by itself, the card says so.
  4. The rest become candidates: a picture (periodogram, light curve, folded light curve), a type from the light-curve
     shape (spotted rotating star, eclipsing binary, flares), the local AI's first look, and the fields VSX asks for.
"""
import json
import math
import time

import numpy as np

from . import config, db

VAR_DIR = config.DATA / 'lc' / 'var'
PLOTS = config.PLOTS / 'variables'
FAP_MAX = 1e-10            # false-alarm probability of the periodogram peak
SNR_MIN = 10.0             # amplitude / its uncertainty
AMP_MIN = 0.5              # ppt: smaller signals are left to professional surveys (systematics are this large)
P_MIN, P_MAX = 0.04, 9.0   # days. Slower changes are too easily confused with spacecraft trends (a sector lasts 27 days)
EDGE = 0.85                # a strongest period this close to the longest searched is a trend, not a repeat
CYCLES_MIN = 3.0
MATCH = 30.0               # arcsec: a catalogued variable this close is taken to be this star (TESS pixels are 21")
BLEND_R = 42.0             # arcsec: two TESS pixels
SERVICES = ('VSX', 'Gaia DR3 variability', 'ASAS-SN variables')
VSX_API = 'https://www.aavso.org/vsx/index.php'
CAT_V = 2                  # catalogue check version: 2 = a mirror's error page no longer counts as an answer


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ the light curve
def binned(t, f, width=30 / 1440):
    """Half-hour averages (medians), so every sector weighs the same whatever its cadence."""
    t, f = np.asarray(t, float), np.asarray(f, float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    if len(t) < 20:
        return t, f
    o = np.argsort(t)
    t, f = t[o], f[o]
    if np.median(np.diff(t)) >= 0.7 * width:
        return t, f
    idx = np.floor((t - t[0]) / width).astype(int)
    _, start, cnt = np.unique(idx, return_index=True, return_counts=True)
    tb = np.array([t[s:s + c].mean() for s, c in zip(start, cnt)])
    fb = np.array([np.median(f[s:s + c]) for s, c in zip(start, cnt)])
    keep = cnt >= 2
    return tb[keep], fb[keep]


def phase_curve(t, f, period, nb=50, t0=None):
    """Median brightness in nb phase bins (nan where empty) and the phase of each point."""
    t0 = t[0] if t0 is None else t0
    ph = ((t - t0) / period) % 1.0
    k = np.minimum((ph * nb).astype(int), nb - 1)
    pc = np.full(nb, np.nan)
    for j in range(nb):
        s = f[k == j]
        if len(s) >= 3:
            pc[j] = np.median(s)
    return pc, ph


def count_flares(t, f, noise):
    """Brief brightenings: at least two points in a row more than 5 times the noise above the star's normal light."""
    if len(f) < 50 or not noise:
        return 0
    med = np.median(f)
    hi = f > med + 5 * noise
    n, run = 0, 0
    for h in hi:
        run = run + 1 if h else 0
        if run == 2:
            n += 1
    return n


def analyze(t, f, teff=None):
    """Search one light curve (time in days, flux normalised to about 1) for a repeating signal. Returns a dict with the
    period (days), amplitude and noise (parts per thousand), the false-alarm probability, a type guess and whether it is
    strong enough to check, or None when there is too little data."""
    from astropy.timeseries import LombScargle
    t, f = np.asarray(t, float), np.asarray(f, float)
    ok = np.isfinite(t) & np.isfinite(f)
    t, f = t[ok], f[ok]
    if len(t) < 200:
        return None
    f = f / np.median(f)
    raw_noise = 1.4826 * np.median(np.abs(np.diff(f))) / math.sqrt(2)
    flares = count_flares(t, f, raw_noise)
    clip = f < np.median(f) + 5 * 1.4826 * np.median(np.abs(f - np.median(f)))     # flares out, dips kept
    tb, fb = binned(t[clip], f[clip])
    if len(tb) < 100:
        return None
    base = float(tb[-1] - tb[0])
    pmax = min(P_MAX, base / CYCLES_MIN)
    if pmax <= P_MIN * 2:
        return None
    fmin, fmax = 1.0 / pmax, 1.0 / P_MIN
    n = int(min(200000, max(5000, 5 * base * fmax)))
    freq = np.linspace(fmin, fmax, n)
    ls = LombScargle(tb, fb)
    pw = ls.power(freq)
    k = int(np.argmax(pw))
    period = 1.0 / freq[k]
    fap = float(ls.false_alarm_probability(pw[k], minimum_frequency=fmin, maximum_frequency=fmax, method='baluev'))
    noise = 1.4826 * np.median(np.abs(np.diff(fb))) / math.sqrt(2)               # per half-hour point
    pc, _ = phase_curve(tb, fb, period)
    amp = float(np.nanmax(pc) - np.nanmin(pc)) if np.isfinite(pc).sum() > 25 else 0.0
    per_bin = noise / math.sqrt(max(1.0, len(tb) / 50))
    snr = amp / (per_bin * math.sqrt(2)) if per_bin > 0 else 0.0
    # the shape decides the type. Eclipsing binaries: the periodogram usually finds half the orbit; folded on twice that,
    # the light curve is flat most of the time with two narrow dips.
    pc2, _ = phase_curve(tb, fb, 2 * period, nb=100)
    good = np.isfinite(pc2)
    duty, unequal, kind, true_p = 1.0, 0.0, 'rotation', period
    if good.sum() > 60:
        top, bot = np.nanpercentile(pc2, 90), np.nanmin(pc2)
        depth = top - bot
        if depth > 0:
            duty = float(np.mean(pc2[good] < top - 0.5 * depth))
            a = np.nanmin(pc2[:50]) if np.isfinite(pc2[:50]).any() else bot
            b = np.nanmin(pc2[50:]) if np.isfinite(pc2[50:]).any() else bot
            unequal = abs(a - b) / depth
    skew = float(((fb - fb.mean()) ** 3).mean() / (fb.std() ** 3 + 1e-30))
    if duty < 0.22 and skew < -0.3:
        kind, true_p = 'eclipsing (detached)', 2 * period
    elif period < 0.5 and (unequal > 0.08 or skew < -0.3) and duty > 0.3:
        kind, true_p = 'eclipsing (contact)', 2 * period
    if true_p != period:                              # an eclipsing binary's depth, measured on its own orbit
        pct, _ = phase_curve(tb, fb, true_p, nb=200)
        if np.isfinite(pct).sum() > 100:
            amp = max(amp, float(np.nanmax(pct) - np.nanmin(pct)))
    # the same signal in each stretch of data (TESS sectors), not one sector's glitch
    gaps = np.flatnonzero(np.diff(tb) > 1.5)
    segs = np.split(np.arange(len(tb)), gaps + 1)
    w = 2 * np.pi / period

    def sin_amp(ix):
        X = np.column_stack([np.sin(w * tb[ix]), np.cos(w * tb[ix]), np.ones(len(ix))])
        c = np.linalg.lstsq(X, fb[ix], rcond=None)[0]
        return 2 * math.hypot(c[0], c[1])
    whole = sin_amp(np.arange(len(tb)))
    elig = [s for s in segs if len(s) > 20 and tb[s[-1]] - tb[s[0]] >= 2 * period]
    seg_ok = sum(1 for s in elig if sin_amp(s) >= 0.4 * whole)
    why = []
    if fap >= FAP_MAX:
        why.append('the periodogram peak could be chance')
    if snr < SNR_MIN or amp * 1e3 < AMP_MIN:
        why.append('too weak')
    if base / true_p < CYCLES_MIN:
        why.append('too few cycles')
    if period > EDGE * pmax:
        why.append('a slow trend, not a repeat')
    if len(elig) >= 2 and seg_ok < 2:
        why.append('seen in only one stretch of data')
    strong = not why
    return {'period': round(true_p, 6), 'ls_period': round(period, 6), 'fap': fap, 'power': round(float(pw[k]), 4),
            'amp': round(amp * 1e3, 3), 'noise': round(noise * 1e3, 3), 'snr': round(snr, 1), 'skew': round(skew, 3),
            'duty': round(duty, 3), 'unequal': round(unequal, 3), 'kind': kind, 'flares': int(flares),
            'cycles': round(base / true_p, 1), 'days': round(base, 1), 'n': int(len(tb)), 'strong': bool(strong),
            'segments': [len(elig), int(seg_ok)], 'why_not': why,
            'vsx_type': vsx_type(kind, flares, teff)}


def vsx_type(kind, flares, teff=None):
    """The VSX type code for this shape: BY (spotted cool dwarf) or ROT, EA (detached) or EW (contact), +UV with flares."""
    if kind.startswith('eclipsing'):
        base = 'EA' if 'detached' in kind else 'EW'
    else:
        base = 'BY' if (teff or 3500) < 5200 else 'ROT'
    return base + ('+UV' if flares >= 3 and base in ('BY', 'ROT') else '')


def save_curve(tic, t, f):
    """Keep the half-hour light curve of a variable star (a few tens of kB) for its picture and for re-checks."""
    VAR_DIR.mkdir(parents=True, exist_ok=True)
    tb, fb = binned(t, f)
    p = VAR_DIR / f'TIC{tic}.npz'
    np.savez_compressed(p, t=tb.astype(np.float64), f=fb.astype(np.float32))
    return str(p.relative_to(config.DATA))


def from_lightcurve(tic, lc, teff=None):
    """Called by the planet hunter for every light curve it downloads (before flattening). Returns the summary to store."""
    try:
        t = np.asarray(lc.time.value, float)
        f = np.asarray(getattr(lc.flux, 'value', lc.flux), float)
        r = analyze(t, f, teff)
        if r is None:
            return {'status': 'too little data'}
        if r['strong']:
            r['npz'] = save_curve(tic, t, f)
        return r
    except Exception as e:
        return {'status': f'error: {type(e).__name__}: {str(e)[:100]}'}


# ------------------------------------------------------------------ database
def ensure(con):
    con.execute('CREATE TABLE IF NOT EXISTS tess_var (tic INTEGER PRIMARY KEY, period REAL, amp REAL, snr REAL, fap REAL, '
                'kind TEXT, vsx_type TEXT, info TEXT, npz TEXT, status TEXT, known TEXT, checks TEXT, plot TEXT, ai TEXT, '
                'created REAL, updated REAL)')


def record(con, tic, r):
    """Store the search result for one star (from the planet hunter or the back-fill)."""
    ensure(con)
    if not r or r.get('status'):
        con.execute('INSERT OR REPLACE INTO tess_var(tic, status, info, created, updated) VALUES(?,?,?,?,?)',
                    (tic, 'nodata', json.dumps(r or {}), time.time(), time.time()))
        return
    old = db.one(con, 'SELECT status FROM tess_var WHERE tic=?', (tic,))
    if old and old['status'] in ('real', 'fake', 'unsure', 'submitted'):
        return                                   # you already judged it: keep that
    status = 'new' if r['strong'] else 'quiet'
    con.execute('INSERT OR REPLACE INTO tess_var(tic, period, amp, snr, fap, kind, vsx_type, info, npz, status, created, updated) '
                'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                (tic, r['period'], r['amp'], r['snr'], r['fap'], r['kind'], r['vsx_type'], json.dumps(r), r.get('npz'),
                 status, time.time(), time.time()))


# ------------------------------------------------------------------ is it known?
def _vsx_api(ra, dec, radius_arcsec):
    from . import net
    js = net.fetch_json(VSX_API, params={'view': 'api.list', 'ra': f'{ra:.5f}', 'dec': f'{dec:+.5f}',
                                         'radius': f'{radius_arcsec / 3600:.5f}', 'format': 'json'}, timeout=60)
    items = ((js or {}).get('VSXObjects') or {}).get('VSXObject') or []
    items = items if isinstance(items, list) else [items]
    return [{'name': x.get('Name'), 'type': x.get('VariabilityType'), 'period': _f(x.get('Period'))} for x in items]


def _vizier(source, ra, dec, radius_arcsec):
    from . import net
    from .catalogs import VIZIER, _rows, answered
    params = {'-source': source, '-c': f'{ra:.5f} {dec:+.5f}', '-c.rs': f'{radius_arcsec:.0f}', '-out.add': '_r', '-out.max': '20'}
    txt = None
    for base in VIZIER:                                   # the first mirror whose database really answered
        try:
            t = net.fetch_text(base + '/viz-bin/asu-tsv', params=params, timeout=90)
        except Exception:
            continue
        if answered(t, source):
            txt = t
            break
    if txt is None:
        raise ConnectionError(f'no VizieR mirror answered for {source}')
    rows = _rows(txt)
    out = []
    for r in rows:
        name = next((r[k] for k in ('Name', 'ID', 'ASASSN-V', 'Source', 'GaiaDR3') if r.get(k)), '?')
        typ = next((r[k] for k in ('Type', 'Class', 'VarType', 'BestClass') if r.get(k)), '')
        out.append({'name': name, 'type': typ, 'period': _f(r.get('Period') or r.get('Per')), 'sep': _f(r.get('_r'))})
    return out


def known(ra, dec, lookups=None):
    """What each catalogue of variable stars has within MATCH arcsec. Returns ({service: [matches] or None}, all_answered).
    None means the service could not be asked; then nothing can be concluded."""
    lookups = lookups or {
        'VSX': lambda: _try(lambda: _vsx_api(ra, dec, MATCH), lambda: _vizier('B/vsx/vsx', ra, dec, MATCH)),
        'Gaia DR3 variability': lambda: _vizier('I/358/vclassre', ra, dec, MATCH),
        'ASAS-SN variables': lambda: _vizier('II/366/catv2021', ra, dec, MATCH)}
    res = {}
    for name, fn in lookups.items():
        try:
            res[name] = list(fn() or [])
        except Exception:
            res[name] = None
    return res, all(v is not None for v in res.values())


def _try(a, b):
    try:
        return a()
    except Exception:
        return b()


# ------------------------------------------------------------------ is it this star?
def blend_check(ra, dec, amp_ppt, gaia):
    """Neighbours within two TESS pixels that could produce the signal by themselves. gaia: dict of arrays ra, dec, g
    (already moved to epoch 2000). Returns {'target_g', 'suspects': [{sep, g, needed}], 'verdict'}."""
    if gaia is None or not len(gaia.get('ra', [])):
        return {'verdict': 'not checked (no star catalogue)', 'suspects': [], 'target_g': None}
    cosd = math.cos(math.radians(dec))
    sep = np.hypot((np.asarray(gaia['ra']) - ra) * cosd, np.asarray(gaia['dec']) - dec) * 3600
    g = np.asarray(gaia['g'], float)
    inr = (sep <= BLEND_R) & np.isfinite(g)
    if not inr.any():
        return {'verdict': 'not checked (no Gaia star at this position)', 'suspects': [], 'target_g': None}
    me = int(np.flatnonzero(inr)[np.argmin(np.where(sep[inr] < 4, g[inr], np.inf))]) if (sep[inr] < 4).any() else None
    if me is None:
        return {'verdict': 'not checked (no Gaia star within 4″ of the TESS position)', 'suspects': [], 'target_g': None}
    flux = 10 ** (-0.4 * g)
    total = flux[inr].sum()
    sus = []
    for j in np.flatnonzero(inr):
        if j == me:
            continue
        needed = (amp_ppt / 1e3) * total / flux[j]                 # fractional change the neighbour would need alone
        if needed < 0.5:
            sus.append({'sep': round(float(sep[j]), 1), 'g': round(float(g[j]), 2), 'needed': round(float(needed), 3)})
    sus.sort(key=lambda s: s['needed'])
    verdict = ('only this star is bright enough to cause it' if not sus else
               f"{len(sus)} neighbouring star(s) could cause it too (the closest needs a {sus[0]['needed'] * 100:.0f}% change)")
    return {'target_g': round(float(g[me]), 2), 'suspects': sus[:5], 'verdict': verdict}


# ------------------------------------------------------------------ picture, type, report
def plot(path, tic, t, f, r):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from astropy.timeseries import LombScargle
    fig = plt.figure(figsize=(10.8, 6.3), dpi=100)
    ax1, ax2, ax3 = fig.add_subplot(2, 2, 1), fig.add_subplot(2, 2, 2), fig.add_subplot(2, 1, 2)
    ax1.plot(t, (f - 1) * 1e3, '.', ms=1.5, color='#333')
    ax1.set_xlabel('time (days, BTJD)'); ax1.set_ylabel('brightness change (ppt)')
    ax1.set_title(f'TIC {tic}: TESS light curve (half-hour averages)', fontsize=10)
    pmax = min(P_MAX, (t[-1] - t[0]) / 2)
    freq = np.linspace(1 / pmax, 1 / P_MIN, 20000)
    ax2.plot(1 / freq, LombScargle(t, f).power(freq), lw=0.7, color='#1f6f8b')
    ax2.axvline(r['ls_period'], color='r', lw=0.8)
    ax2.set_xscale('log'); ax2.set_xlabel('period (days)'); ax2.set_ylabel('signal strength')
    ax2.set_title(f"strongest repeat: {r['ls_period']:.4f} d", fontsize=10)
    ph = ((t - t[0]) / r['period']) % 1.0
    for k in (0, 1):
        ax3.plot(ph + k, (f - 1) * 1e3, '.', ms=1.5, color='#888' if k else '#333')
    pc, _ = phase_curve(t, f, r['period'], nb=100)
    x = (np.arange(100) + 0.5) / 100
    for k in (0, 1):
        ax3.plot(x + k, (pc - 1) * 1e3, '-', color='#d62728', lw=1.4)
    ax3.set_xlabel(f"phase (period {r['period']:.5f} days, shown twice)"); ax3.set_ylabel('brightness change (ppt)')
    ax3.set_title(f"folded: {r['kind']}, amplitude {r['amp']:.1f} ppt" + (f", {r['flares']} flares" if r.get('flares') else ''), fontsize=10)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path)
    plt.close(fig)


def epoch(t, f, period, kind):
    """Time (BJD) of a minimum for eclipsing binaries, of a maximum otherwise, as VSX asks."""
    pc, _ = phase_curve(t, f, period, nb=100)
    j = int(np.nanargmin(pc) if kind.startswith('eclipsing') else np.nanargmax(pc))
    return float(t[0] + (j + 0.5) / 100 * period + 2457000.0)


def vsx_fields(star, r, cfg, ep=None, sectors=''):
    """The values the VSX 'New Star' form asks for, in its order. You paste these after logging in at aavso.org."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    c = SkyCoord(ra=star['ra'] * u.deg, dec=star['dec'] * u.deg)
    tm = star.get('tmag')
    lo = tm + r['amp'] / 1e3 * 1.0857 if tm else None
    return [('Name', f"TIC {star['tic']}"),
            ('Coordinates (J2000)', c.to_string('hmsdms', sep=':', precision=2)),
            ('Variability type', r['vsx_type']),
            ('Period (days)', f"{r['period']:.6f}"),
            ('Epoch (BJD)', f'{ep:.4f}' if ep else ''),
            ('Magnitude range (TESS passband)', f'{tm:.3f} - {lo:.3f} T' if tm else ''),
            ('Passband', 'TESS (custom passband)'),
            ('Discoverer', cfg.get('observer_name') or 'YOUR NAME'),
            ('Remarks', f"Found in TESS light curves ({sectors or 'several sectors'}): period from a Lomb-Scargle periodogram, "
                        f"amplitude {r['amp']:.1f} ppt, {r['cycles']:.0f} cycles. Checked against VSX, Gaia DR3 and ASAS-SN. "
                        'Phase plot attached.')]


def ai_look(llm, png, r):
    try:
        out = llm.vision('You check a possible new variable star in NASA TESS data. Top left: the light curve. Top right: the '
                         'periodogram (red line: the strongest period). Bottom: the light curve folded on the period. Is this a '
                         'real, repeating change of brightness (and which kind: spotted rotating star, eclipsing binary, '
                         'pulsating star) or an instrument effect (jumps at gaps between sectors, a trend over a whole sector, '
                         'scattered light)? Answer JSON: {"verdict": "real" | "artefact" | "unclear", "type": "rotation" | '
                         '"eclipsing" | "pulsating" | "other", "confidence": 0.0-1.0, "reasons": ["short", "..."]}. At most 3 '
                         f"reasons. Numbers: period {r['period']:.5f} d, amplitude {r['amp']:.1f} ppt, noise {r['noise']:.2f} ppt "
                         f"per half hour, {r['cycles']:.0f} cycles, rule-based shape: {r['kind']}.", [str(png)], json_mode=True,
                         max_tokens=250)
        if isinstance(out, dict) and out.get('verdict'):
            return {'verdict': str(out['verdict'])[:20], 'type': str(out.get('type') or '')[:20], 'confidence': _f(out.get('confidence')),
                    'reasons': [str(x)[:120] for x in (out.get('reasons') or [])][:3]}
    except Exception:
        pass
    return None


# ------------------------------------------------------------------ the job
def twins(con, star, period, radius_arcsec=180.0, tol=0.005):
    """Other stars of this lab within a few TESS pixels that show the same period: the same light seen twice, so at most
    one of them is the variable star. Returns [{tic, sep, tmag}]."""
    out = []
    for o in db.rows(con, "SELECT v.tic, v.period, t.ra, t.dec, t.tmag FROM tess_var v JOIN tess_targets t ON t.tic = v.tic "
                          "WHERE v.tic != ? AND v.period IS NOT NULL AND v.status NOT IN ('quiet','nodata')", (star['tic'],)):
        if o['ra'] is None or abs(o['period'] - period) > tol * period:
            continue
        sep = math.hypot((o['ra'] - star['ra']) * math.cos(math.radians(star['dec'])), o['dec'] - star['dec']) * 3600
        if sep <= radius_arcsec:
            out.append({'tic': o['tic'], 'sep': round(sep, 1), 'tmag': o['tmag']})
    return out


def _star(con, tic):
    return db.one(con, 'SELECT tic, ra, dec, tmag, teff, sectors FROM tess_targets WHERE tic=?', (tic,)) or {'tic': tic}


def backfill(ctx, minutes):
    """Stars the planet hunter checked before this search existed: download their light curves again, a few per run."""
    import shutil
    from .tess_hunter import get_lightcurve
    con, log = ctx.con, ctx.log
    todo = db.rows(con, "SELECT t.tic, t.teff FROM tess_targets t LEFT JOIN tess_var v ON v.tic = t.tic "
                        "WHERE t.status='done' AND v.tic IS NULL ORDER BY t.tmag LIMIT 400")
    work = config.CACHE / 'var-tmp'
    end = time.time() + minutes * 60
    n = strong = 0
    for s in todo:
        if time.time() > end or ctx.time_left() < 300:
            break
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        try:
            lc, kind, sectors = get_lightcurve(s['tic'], work, lambda *a: None)
            r = from_lightcurve(s['tic'], lc, s.get('teff')) if lc is not None else {'status': 'no TESS data'}
        except Exception as e:
            r = {'status': f'error: {type(e).__name__}: {str(e)[:80]}'}
        record(con, s['tic'], r)
        n += 1
        strong += bool(r and r.get('strong'))
    shutil.rmtree(work, ignore_errors=True)
    left = max(0, len(todo) - n)
    if n:
        log(f'  back-fill: {n} earlier stars searched again for variability ({strong} with a clear signal); {left}+ still to do')
    return n


def check_one(con, cfg, log, llm, row, gaia_fn=None, lookups=None):
    """Catalogues, neighbours, picture, type, report for one star with a clear signal. Returns its new status."""
    star = _star(con, row['tic'])
    if star.get('ra') is None:
        return 'new'
    res, answered = known(star['ra'], star['dec'], lookups)
    hits = {k: v for k, v in res.items() if v}
    checks = {'catalogues': {k: (None if v is None else v[:3]) for k, v in res.items()}, 'cat_v': CAT_V}
    if hits:
        status = 'known'
    elif not answered:
        con.execute('UPDATE tess_var SET checks=?, updated=? WHERE tic=?', (json.dumps(checks), time.time(), row['tic']))
        return 'new'                                     # try again next run
    else:
        status = 'candidate'
    r = json.loads(row['info'] or '{}')
    tw = twins(con, star, row['period'] or r.get('period') or 0)
    if tw:
        checks['twins'] = tw
    if status == 'candidate':
        try:
            from .plates import gaia_cone, propagate
            g = gaia_fn(star['ra'], star['dec']) if gaia_fn else gaia_cone(star['ra'], star['dec'], BLEND_R / 3600 * 1.2, gmax=21.0, log=lambda *a: None)
            ra2, de2 = propagate(g['ra'], g['dec'], g['pmra'], g['pmdec'], 2451545.0)
            checks['blend'] = blend_check(star['ra'], star['dec'], r.get('amp') or 0, {'ra': ra2, 'dec': de2, 'g': g['g']})
        except Exception:
            checks['blend'] = {'verdict': 'not checked yet (the Gaia star catalogue did not answer; tried again next run)', 'suspects': []}
        try:
            d = np.load(config.DATA / row['npz'])
            t, f = d['t'], d['f'].astype(float)
            png = PLOTS / f"TIC{row['tic']}.png"
            plot(png, row['tic'], t, f, r)
            ep = epoch(t, f, r['period'], r['kind'])
            checks['epoch'] = ep
            checks['vsx'] = vsx_fields(star, r, cfg, ep, star.get('sectors') or '')
            ai = ai_look(llm, png, r) if llm else None
            con.execute('UPDATE tess_var SET plot=?, ai=? WHERE tic=?', (str(png.relative_to(config.DATA)), json.dumps(ai) if ai else None, row['tic']))
        except Exception as e:
            log(f"  TIC {row['tic']}: picture failed ({e})")
        db.add_task(con, f"var:{row['tic']}", 'tess', 'review', f"Possible new variable star: TIC {row['tic']}",
                    f"{r.get('vsx_type')} every {r.get('period', 0):.4f} days, amplitude {r.get('amp', 0):.1f} ppt. Not in VSX, Gaia DR3 "
                    'or ASAS-SN. Look at the folded light curve before anything is submitted.', {'tab': 'tess'}, 2)
    con.execute('UPDATE tess_var SET status=?, known=?, checks=?, updated=? WHERE tic=?',
                (status, json.dumps(hits) if hits else None, json.dumps(checks), time.time(), row['tic']))
    return status


def summary(con):
    ensure(con)
    cnt = {r['status']: r['n'] for r in db.rows(con, 'SELECT status, COUNT(*) n FROM tess_var GROUP BY status')}
    return {'searched': sum(v for k, v in cnt.items() if k != 'nodata'), 'clear': sum(cnt.get(k, 0) for k in ('new', 'known', 'candidate', 'real', 'fake', 'unsure', 'submitted')),
            'known': cnt.get('known', 0), 'candidates': cnt.get('candidate', 0), 'real': cnt.get('real', 0),
            'submitted': cnt.get('submitted', 0), 'waiting': cnt.get('new', 0), 't': time.time()}


def run(ctx):
    from .llm import LLM
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    vc = cfg.get('variables') if isinstance(cfg.get('variables'), dict) else {}
    ensure(con)
    backfill(ctx, float(vc.get('backfill_min', 25)))
    try:
        llm = LLM(cfg, log)
        llm = llm if llm.models() else None
    except Exception:
        llm = None
    for c in db.rows(con, "SELECT tic, checks FROM tess_var WHERE status='candidate'"):     # checked by older code: again
        if (json.loads(c['checks'] or '{}')).get('cat_v') != CAT_V:
            con.execute("UPDATE tess_var SET status='new' WHERE tic=?", (c['tic'],))
            db.close_task(con, f"var:{c['tic']}", 'dismissed')
    redo = [c['tic'] for c in db.rows(con, "SELECT tic, checks FROM tess_var WHERE status='candidate'")
            if str((json.loads(c['checks'] or '{}').get('blend') or {}).get('verdict', '')).startswith('not checked')]
    rows = db.rows(con, "SELECT * FROM tess_var WHERE status='new' ORDER BY snr DESC LIMIT ?", (int(vc.get('checks_per_run', 40)),))
    rows += [r for r in db.rows(con, "SELECT * FROM tess_var WHERE status='candidate'") if r['tic'] in redo]   # neighbours not yet weighed
    got = {}
    for row in rows:
        if ctx.time_left() < 120:
            break
        st = check_one(con, cfg, log, llm, row)
        got[st] = got.get(st, 0) + 1
        if st == 'candidate':
            log(f"  TIC {row['tic']}: not in any catalogue of variable stars: a candidate ({row['vsx_type']}, {row['period']:.4f} d, {row['amp']:.1f} ppt)")
    s = summary(con)
    db.kv_set(con, 'variables', s)
    return (f"variable stars: {s['searched']} light curves searched, {s['clear']} with a clear repeating signal; "
            f"{s['known']} already catalogued, {s['candidates']} new candidate(s) for you to look at"
            + (f"; {got.get('new', 0)} could not be checked (a catalogue did not answer)" if got.get('new') else ''))
