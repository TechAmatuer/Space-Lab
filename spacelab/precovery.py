"""Asteroid watch + precovery hunter.

1. Watch: today's close approaches (CNEOS), objects with nonzero impact chance (Sentry), new unconfirmed objects (Scout).
2. Precovery: for Sentry objects with short observed arcs, ask the CADC's SSOIS service which old survey images could
   contain them, predict exactly where they should be (JPL Horizons, with 3-sigma uncertainty), download Pan-STARRS1
   single-night cutouts, and look for the same moving dot in two images from the same night that is absent from the
   deep stacked image. Survivors go to you for a yes/no look; real ones get a draft ADES report for the MPC.
"""
import io
import json
import math
import re
import time

import numpy as np

from . import config, db, net
from .llm import LLM

SENTRY = 'https://ssd-api.jpl.nasa.gov/sentry.api'
SBDB = 'https://ssd-api.jpl.nasa.gov/sbdb.api'
SCOUT = 'https://ssd-api.jpl.nasa.gov/scout.api'
CAD = 'https://ssd-api.jpl.nasa.gov/cad.api'
SSOIS_BASES = [
    'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/cadcbin/ssos/ssosclf.pl',
    'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/ssos/ssosclf.pl',
    'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/cadcbin/ssos/ssos.pl',
]
PS1_FILES = 'https://ps1images.stsci.edu/cgi-bin/ps1filenames.py'
PS1_CUT = 'https://ps1images.stsci.edu/cgi-bin/fitscut.cgi'
PS1_PIX = 0.25                      # arcsec / pixel
PS1_LIMIT = {'g': 22.0, 'r': 21.8, 'i': 21.5, 'z': 20.9, 'y': 19.7}   # single-epoch 5-sigma depths
PS1_MJD = (54990.0, 57300.0)        # DR2 warp images: 2009 - 2015
LD_AU = 0.00256955529


def _f(x):
    try:
        v = float(str(x).split()[0])
        return v if math.isfinite(v) else None
    except (TypeError, ValueError, IndexError):
        return None


def h_to_size_m(h, albedo=0.14):
    return 1329e3 / math.sqrt(albedo) * 10 ** (-h / 5)


# ================================================================== 1. watch
def watch(ctx, llm):
    con, log = ctx.con, ctx.log
    snap = {}
    try:
        js = net.fetch_json(CAD, params={'date-min': 'now', 'date-max': '+30', 'dist-max': '0.05', 'sort': 'dist'}, timeout=60)
        f = js.get('fields', [])
        snap['cad'] = [dict(zip(f, r)) for r in js.get('data', [])][:40]
    except Exception as e:
        log(f'close approaches unavailable: {e}')
    try:
        js = net.fetch_json(SENTRY, timeout=90)
        for r in js.get('data', []):
            r['ps'] = r.get('ps_cum') or r.get('ps_max')
        snap['sentry'] = sorted(js.get('data', []), key=lambda r: -(_f(r.get('ps')) or -99))
    except Exception as e:
        log(f'Sentry unavailable: {e}')
    try:
        js = net.fetch_json(SCOUT, timeout=60)
        snap['scout'] = js.get('data', [])
    except Exception as e:
        log(f'Scout unavailable: {e}')
    if not snap:
        return None
    db.kv_set(con, 'neo_watch', {'t': time.time(), **{k: v[:60] if k == 'sentry' else v for k, v in snap.items()}})
    # plain-English summary by the local model, from a small set of facts only
    facts = []
    for r in (snap.get('cad') or [])[:5]:
        d = _f(r.get('dist')) or 0
        h = _f(r.get('h'))
        size = f'~{h_to_size_m(h):.0f} m' if h else 'size unknown'
        facts.append(f"{r.get('des')} passes {d / LD_AU:.1f} lunar distances from Earth on {r.get('cd')} ({size})")
    top = (snap.get('sentry') or [])[:3]
    for r in top:
        facts.append(f"Sentry: {r.get('fullname') or r.get('des')} impact chance {r.get('ip')} (Palermo {r.get('ps')}, Torino {r.get('ts_max')})")
    sc = snap.get('scout') or []
    if sc:
        best = max(sc, key=lambda r: _f(r.get('neoScore')) or 0)
        facts.append(f"{len(sc)} newly found objects await confirmation; highest NEO score {best.get('neoScore')} ({best.get('objectName')})")
    text = llm.chat('Write a calm, accurate 3-sentence "planetary defense" update for a 12th grader from these facts. '
                    'Explain that Palermo scale values below -2 mean no cause for concern. Do not add facts.\n\n- ' + '\n- '.join(facts),
                    system='You are a NASA planetary defense educator. Never exaggerate risk.', max_tokens=220)
    db.kv_set(con, 'neo_watch_text', {'t': time.time(), 'text': text or ' '.join(facts[:4]), 'facts': facts})
    return snap


# ================================================================== 2. targets
def select_targets(ctx, snap):
    con, cfg, log = ctx.con, ctx.cfg['precovery'], ctx.log
    sentry = (snap or {}).get('sentry') or []
    pool = [r for r in sentry if (_f(r.get('h')) or 99) <= cfg['h_max']][:40]
    have = {r['des']: r for r in db.rows(con, 'SELECT des, searched, arc FROM pc_targets')}
    fresh = 0
    for r in pool:
        des = r.get('des')
        if not des:
            continue
        old = have.get(des)
        if old and old.get('arc') is not None and old.get('searched') and time.time() - old['searched'] < cfg['research_days'] * 86400:
            continue
        try:
            js = net.fetch_json(SBDB, params={'sstr': des}, timeout=60, min_gap=1.2)
        except Exception as e:
            log(f'SBDB {des}: {e}')
            continue
        orb = js.get('orbit') or {}
        arc = _f(orb.get('data_arc'))
        status = 'ready' if (arc is not None and arc <= cfg['arc_max_days']) else 'long-arc'
        con.execute("""INSERT INTO pc_targets(des,fullname,h,arc,cc,ip,ps,last_obs,status) VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(des) DO UPDATE SET fullname=excluded.fullname, h=excluded.h, arc=excluded.arc, cc=excluded.cc,
                    ip=excluded.ip, ps=excluded.ps, last_obs=excluded.last_obs,
                    status=CASE WHEN pc_targets.status='searched' THEN pc_targets.status ELSE excluded.status END""",
                    (des, r.get('fullname') or des, _f(r.get('h')), arc, str(orb.get('condition_code') or ''),
                     _f(r.get('ip')), _f(r.get('ps')), r.get('last_obs') or orb.get('last_obs'), status))
        fresh += 1
    log(f'checked orbits of {fresh} Sentry objects')
    return db.rows(con, "SELECT * FROM pc_targets WHERE status IN ('ready','searched') AND arc <= ? AND "
                        "(searched IS NULL OR searched < ?) ORDER BY ps DESC LIMIT ?",
                   (cfg['arc_max_days'], time.time() - cfg['research_days'] * 86400, int(cfg['max_targets'])))


# ================================================================== 3. archive search (SSOIS)
def parse_tsv(text):
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.startswith('#')]
    if not lines or '\t' not in lines[0]:
        return None
    head = [h.strip() for h in lines[0].split('\t')]
    if not any('mjd' in h.lower() for h in head):
        return None
    out = []
    for ln in lines[1:]:
        parts = ln.split('\t')
        if len(parts) >= len(head) - 1:
            out.append(dict(zip(head, [p.strip() for p in parts])))
    return out


def _col(row, *names):
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        for k, v in low.items():
            if n in k:
                return v
    return None


def ssois(con, des, log, search='bynameCADC'):
    rows = _ssois(con, des, log, search)
    if not rows and search == 'bynameCADC':
        try:   # brand-new objects may not be in CADC's orbit list yet: ask with JPL Horizons' orbit instead
            rows = _ssois(con, des, log, 'bynameHorizons')
        except Exception as e:
            log(f'  SSOIS (Horizons orbit) failed: {e}')
    return rows


def _ssois(con, des, log, search):
    order = SSOIS_BASES[:]
    good = db.kv_get(con, 'ssois_base')
    if good in order:
        order.remove(good)
        order.insert(0, good)
    params = {'lang': 'en', 'object': des.upper(), 'search': search, 'epoch1': '2003 01 01',
              'epoch2': time.strftime('%Y %m %d'), 'eellipse': '', 'eunits': 'arcseconds',
              'extres': 'no', 'xyres': 'no', 'format': 'tsv'}
    last = None
    for base in order:
        try:
            txt = net.fetch_text(base, params=params, timeout=240, retries=2, min_gap=5)
            rows = parse_tsv(txt)
            if rows is not None:
                db.kv_set(con, 'ssois_base', base)
                out = []
                for r in rows:
                    out.append({'image': _col(r, 'image') or '', 'mjd': _f(_col(r, 'mjd')), 'filter': (_col(r, 'filter') or '').strip(),
                                'exptime': _f(_col(r, 'exptime')), 'ra': _f(_col(r, 'object_ra', 'ra')),
                                'dec': _f(_col(r, 'object_dec', 'dec')), 'telinst': _col(r, 'telescope', 'instrument') or '',
                                'link': _col(r, 'datalink', 'metadata', 'image_target') or ''})
                return [r for r in out if r['mjd']]
            last = f'unexpected reply from {base}: {txt[:160]!r}'
        except Exception as e:
            last = f'{base}: {type(e).__name__}: {e}'
    raise RuntimeError(f'SSOIS query failed ({last})')


# ================================================================== 4. predictions (JPL Horizons)
def horizons_predict(des, location, mjds):
    from astroquery.jplhorizons import Horizons
    out = []
    jds = [m + 2400000.5 for m in mjds]
    for i in range(0, len(jds), 40):
        chunk = jds[i:i + 40]
        eph = Horizons(id=des, location=location, epochs=chunk, id_type='smallbody').ephemerides(
            quantities='1,3,9,36,37', extra_precision=True)
        cols = eph.colnames
        for r in eph:
            def g(name):
                if name not in cols:
                    return None
                v = r[name]
                if np.ma.is_masked(v):
                    return None
                return _f(v)
            out.append({'mjd': g('datetime_jd') - 2400000.5 if g('datetime_jd') else None, 'ra': g('RA'), 'dec': g('DEC'),
                        'ra_rate': g('RA_rate'), 'dec_rate': g('DEC_rate'), 'v': g('V'),
                        'smaa': g('SMAA_3sigma'), 'smia': g('SMIA_3sigma'), 'theta': g('Theta')})
    return out


# ================================================================== 5. Pan-STARRS images
def parse_ps1_table(text):
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return []
    head = lines[0].split()
    return [dict(zip(head, ln.split())) for ln in lines[1:] if len(ln.split()) >= len(head)]


def ps1_list(ra, dec, filt, kind):
    return parse_ps1_table(net.fetch_text(PS1_FILES, params={'ra': f'{ra:.6f}', 'dec': f'{dec:.6f}', 'filters': filt,
                                                             'type': kind}, timeout=90, min_gap=0.5))


def ps1_cutout(filename, ra, dec, size_px):
    from astropy.io import fits
    raw = net.fetch(PS1_CUT, params={'red': filename, 'ra': f'{ra:.6f}', 'dec': f'{dec:.6f}', 'size': int(size_px),
                                     'format': 'fits'}, timeout=180, min_gap=0.5)
    with fits.open(io.BytesIO(raw)) as hd:
        return np.array(hd[0].data, dtype=np.float64), hd[0].header.copy()


# ================================================================== 6. detection (pure functions, unit-tested)
def offsets(ra, dec, ra0, dec0):
    """Tangent-plane offsets in arcsec (east, north) of (ra, dec) from (ra0, dec0)."""
    dra = (np.asarray(ra) - ra0 + 180.0) % 360.0 - 180.0
    return dra * math.cos(math.radians(dec0)) * 3600.0, (np.asarray(dec) - dec0) * 3600.0


def mahal(dx, dy, smaa, smia, theta):
    """<= 1 inside the 3-sigma ellipse (theta = position angle of the major axis, degrees east of north)."""
    a = max(smaa or 2.0, 2.0)
    b = max(smia or 2.0, 2.0)
    th = math.radians(theta or 0.0)
    u = dx * math.sin(th) + dy * math.cos(th)
    v = dx * math.cos(th) - dy * math.sin(th)
    return math.sqrt((u / a) ** 2 + (v / b) ** 2)


def detect_sources(data, header, thresh=2.5):
    import sep
    from astropy.wcs import WCS
    d = np.ascontiguousarray(data, dtype=np.float64).copy()
    bad = ~np.isfinite(d)
    if bad.all():
        return []
    d[bad] = np.nanmedian(d)
    bkg = sep.Background(d, mask=bad)
    sub = d - bkg.back()
    try:
        objs = sep.extract(sub, thresh, err=bkg.globalrms, mask=bad, minarea=5)
    except Exception:
        sep.set_extract_pixstack(2_000_000)
        objs = sep.extract(sub, thresh, err=bkg.globalrms, mask=bad, minarea=5)
    if len(objs) == 0:
        return []
    flux, ferr, _ = sep.sum_circle(sub, objs['x'], objs['y'], 4.0, err=bkg.globalrms, mask=bad)
    ra, dec = WCS(header).all_pix2world(objs['x'], objs['y'], 0)
    h, w = d.shape
    out = []
    for i in range(len(objs)):
        x, y = float(objs['x'][i]), float(objs['y'][i])
        if x < 6 or y < 6 or x > w - 7 or y > h - 7:
            continue
        if bad[max(0, int(y) - 3):int(y) + 4, max(0, int(x) - 3):int(x) + 4].any():
            continue
        snr = flux[i] / ferr[i] if ferr[i] > 0 else 0
        out.append({'x': x, 'y': y, 'ra': float(ra[i]), 'dec': float(dec[i]), 'a': float(objs['a'][i]),
                    'b': float(objs['b'][i]), 'flux': float(flux[i]), 'snr': float(snr), 'npix': int(objs['npix'][i])})
    return out


def moving_candidates(dets, static, pred, trail_arcsec=0.0, snr_min=5.0, static_r=1.5, pix=PS1_PIX):
    """Detections inside the 3-sigma ellipse that are not static sources and look like real point/trail sources."""
    if static:
        sx, sy = offsets([s['ra'] for s in static], [s['dec'] for s in static], pred['ra'], pred['dec'])
        spts = np.column_stack([sx, sy])
    else:
        spts = np.zeros((0, 2))
    out = []
    for d in dets:
        if d['snr'] < snr_min:
            continue
        if d['b'] < 0.55:   # sharper than any star: cosmic ray / hot pixel
            continue
        elong = d['a'] / max(d['b'], 0.1)
        max_elong = 2.2 + trail_arcsec / (pix * 3.0)
        if elong > max_elong:
            continue
        dx, dy = offsets(d['ra'], d['dec'], pred['ra'], pred['dec'])
        dx, dy = float(dx), float(dy)
        if len(spts) and np.min(np.hypot(spts[:, 0] - dx, spts[:, 1] - dy)) < static_r:
            continue
        m = mahal(dx, dy, pred.get('smaa'), pred.get('smia'), pred.get('theta'))
        if m > 1.0:
            continue
        out.append({**d, 'dx': dx, 'dy': dy, 'm': m})
    return out


def link_pairs(images, tol_base=2.0):
    """images: list of dicts with 'mjd', 'cands' (from moving_candidates), 'exptime'. Returns best links.
    The along-orbit prediction error is almost the same for two exposures minutes apart, so a real asteroid
    has nearly the same offset from the prediction in both images."""
    links = []
    for i in range(len(images)):
        for j in range(i + 1, len(images)):
            a, b = images[i], images[j]
            if abs(b['mjd'] - a['mjd']) < 3 / 1440:
                continue
            for c1 in a['cands']:
                for c2 in b['cands']:
                    diff = math.hypot(c1['dx'] - c2['dx'], c1['dy'] - c2['dy'])
                    if diff > tol_base:
                        continue
                    f1 = c1['flux'] / max(a.get('exptime') or 1, 1)
                    f2 = c2['flux'] / max(b.get('exptime') or 1, 1)
                    if f1 <= 0 or f2 <= 0 or max(f1, f2) / min(f1, f2) > 3.5:
                        continue
                    links.append({'i': i, 'j': j, 'c1': c1, 'c2': c2, 'diff': diff,
                                  'score': diff + 0.8 * (c1['m'] + c2['m']) - 0.05 * min(c1['snr'], c2['snr'])})
    links.sort(key=lambda l: l['score'])
    return links


# ================================================================== 7. review images + report
def _stretch(img):
    v = img[np.isfinite(img)]
    if v.size == 0:
        return np.zeros_like(img)
    lo, hi = np.percentile(v, 5), np.percentile(v, 99.5)
    out = np.clip((np.nan_to_num(img, nan=lo) - lo) / max(hi - lo, 1e-9), 0, 1)
    return np.sqrt(out)


def crop(data, x, y, half):
    x, y = int(round(x)), int(round(y))
    pad = np.pad(data, half, mode='constant', constant_values=np.nan)
    return pad[y:y + 2 * half, x:x + 2 * half]


def make_review(png_path, gif_path, title, frames, labels, pred_xy=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from PIL import Image
    n = len(frames)
    fig, ax = plt.subplots(1, n, figsize=(3.4 * n, 3.7))
    for k in range(n):
        a = ax[k] if n > 1 else ax
        a.imshow(_stretch(frames[k]), cmap='gray', origin='lower')
        h = frames[k].shape[0] // 2
        a.add_patch(plt.Circle((h, h), 7, fill=False, color='#ff5a36', lw=1.4))
        if pred_xy and k < len(pred_xy) and pred_xy[k]:
            a.plot(*pred_xy[k], marker='+', color='#4db3d3', ms=12, mew=1.5)
        a.set_title(labels[k], fontsize=9)
        a.set_xticks([]); a.set_yticks([])
    fig.suptitle(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(png_path, dpi=90)
    plt.close(fig)
    ims = []
    for f in frames[:2]:
        a8 = (_stretch(f) * 255).astype(np.uint8)[::-1]
        ims.append(Image.fromarray(a8).resize((300, 300), Image.NEAREST).convert('P'))
    if ims:
        ims[0].save(gif_path, save_all=True, append_images=ims[1:], duration=700, loop=0)


def obs_time_utc(header, mjd_fallback, exptime):
    from astropy.time import Time
    mjd = _f(header.get('MJD-OBS')) or mjd_fallback
    scale = 'tai' if header.get('MJD-OBS') is not None else 'utc'   # PS1 warp headers give TAI
    mid = mjd + (exptime or _f(header.get('EXPTIME')) or 0) / 2 / 86400
    return Time(mid, format='mjd', scale=scale).utc.isot + 'Z'


SURVEYS = {
    'F51': {'observers': 'Pan-STARRS1 Science Consortium', 'design': 'Reflector', 'aperture': '1.8', 'cat': 'Gaia1',
            'note': 'Pan-STARRS1 DR2 single-epoch (warp) images from MAST'},
    'I41': {'observers': 'Zwicky Transient Facility', 'design': 'Schmidt', 'aperture': '1.2', 'cat': 'Gaia2',
            'note': 'ZTF science images from IRSA'},
}


def ades_psv(des, rows, cfg, stn='F51'):
    """Draft ADES PSV. The MPC's archival committee (SARC) must verify you before you submit archival data."""
    sv = SURVEYS[stn]
    perm = des if des.strip().isdigit() else ''
    prov = '' if perm else des
    name = cfg.get('observer_name') or 'YOUR NAME'
    head = ['# version=2017', '# observatory', f'! mpcCode {stn}', '# submitter', f'! name {name}',
            '# observers', f'! name {sv["observers"]}', '# measurers', f'! name {name}',
            '# telescope', f'! design {sv["design"]}', f'! aperture {sv["aperture"]}', '! detector CCD',
            '# comment', f'! line Precovery positions measured on {sv["note"]}.',
            '! line DRAFT produced by Space Lab - check every value and the header with the MPC SARC before submitting.']
    cols = ['permID', 'provID', 'mode', 'stn', 'obsTime', 'ra', 'dec', 'rmsRA', 'rmsDec', 'astCat']
    lines = [' |'.join(cols)]
    for r in rows:
        lines.append(' |'.join([perm, prov, 'CCD', stn, r['obsTime'], f"{r['ra']:.6f}", f"{r['dec']:+.6f}",
                                '0.200' if stn == 'F51' else '0.400', '0.200' if stn == 'F51' else '0.400', sv['cat']]))
    return '\n'.join(head + lines) + '\n'


# ================================================================== 8. search one target
def search_target(ctx, t):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    pc = cfg['precovery']
    des = t['des']
    log(f'--- {des} (H={t["h"]}, arc {t["arc"]} d, Palermo {t["ps"]})')
    rows = ssois(con, des, log)
    ps1 = [r for r in rows if ('pan-starrs' in r['telinst'].lower() or 'ps1' in r['telinst'].lower())
           and PS1_MJD[0] <= r['mjd'] <= PS1_MJD[1] and r['filter'][:1].lower() in PS1_LIMIT]
    other = {}
    for r in rows:
        if r in ps1:
            continue
        k = r['telinst'] or 'unknown'
        other.setdefault(k, {'n': 0, 'links': []})
        other[k]['n'] += 1
        if len(other[k]['links']) < 3 and r['link']:
            other[k]['links'].append({'mjd': r['mjd'], 'link': r['link']})
    log(f'  SSOIS: {len(rows)} images in total, {len(ps1)} from Pan-STARRS1 DR2 years')
    # Show which telescopes actually have data for this target
    if not ps1 and rows:
        tel_counts = {}
        for r in rows:
            tel = r.get('telinst', 'unknown') or 'unknown'
            tel_counts[tel] = tel_counts.get(tel, 0) + 1
        log(f'  SSOIS found images but NOT from Pan-STARRS1: {", ".join(f"{v} on {k}" for k, v in list(tel_counts.items())[:5])}')
    note, nimg, ncand = '', 0, 0
    if ps1:
        preds = horizons_predict(des, 'F51', [r['mjd'] for r in ps1])
        usable = []
        for r, p in zip(ps1, preds):
            f = r['filter'][:1].lower()
            if p['ra'] is None or p['smaa'] is None:
                continue
            if p['v'] is not None and p['v'] > PS1_LIMIT[f] - 0.2:
                continue
            if p['smaa'] > pc['sigma_max_arcsec']:
                continue
            usable.append({**r, 'pred': p, 'f': f})
        log(f'  {len(usable)} images where it should be bright enough and well-enough predicted')
        nights = {}
        for u in usable:
            nights.setdefault(int(math.floor(u['mjd'] - 0.3)), []).append(u)
        nights = {k: v for k, v in nights.items() if len(v) >= 2}
        log(f'  {len(nights)} good nights (2+ images) found')
        for night, imgs in sorted(nights.items(), key=lambda kv: min(i['pred']['smaa'] for i in kv[1]))[:6]:
            if ctx.time_left() < 180:
                break
            ncand += search_night(ctx, t, night, imgs)
            nimg += len(imgs)
        if not usable:
            note = 'Pan-STARRS covered the area, but the asteroid would have been too faint or too uncertain then'
    else:
        # No Pan-STARRS1 images — try other surveys from CADC as fallback
        note = 'no Pan-STARRS1 images in the 2009-2015 archive'
        if not rows:
            note = 'SSOIS returned no images at all for this object (likely too faint or not observed yet)'
    con.execute("UPDATE pc_targets SET status='searched', searched=?, nimg=?, ncand=ncand+?, note=?, other=? WHERE des=?",
                (time.time(), nimg, ncand, note or f'searched {nimg} images', json.dumps(other), des))
    return nimg, ncand


def search_night(ctx, t, night, imgs):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    des = t['des']
    imgs = sorted(imgs, key=lambda u: u['mjd'])[:4]
    big = max(u['pred']['smaa'] for u in imgs)
    size = int(min(2400, max(160, 2 * (big + 25) / PS1_PIX)))
    loaded = []
    for u in imgs:
        p = u['pred']
        try:
            best = None
            m = re.match(r'rings\.v3\.skycell\.(\d{4})\.(\d{3})\.wrp\.', u['image'] or '')
            if m:
                best = {'filename': f"/rings.v3.skycell/{m.group(1)}/{m.group(2)}/{u['image']}.fits"}
            else:
                warps = ps1_list(p['ra'], p['dec'], u['f'], 'warp')
                best = min(warps, key=lambda w: abs(_f(w.get('mjd')) - u['mjd'])) if warps else None
                if not best or abs(_f(best.get('mjd')) - u['mjd']) > 0.003:
                    continue
            try:
                data, hdr = ps1_cutout(best['filename'], p['ra'], p['dec'], size)
            except Exception:
                warps = ps1_list(p['ra'], p['dec'], u['f'], 'warp')
                best = min(warps, key=lambda w: abs(_f(w.get('mjd')) - u['mjd'])) if warps else None
                if not best or abs(_f(best.get('mjd')) - u['mjd']) > 0.003:
                    continue
                data, hdr = ps1_cutout(best['filename'], p['ra'], p['dec'], size)
            rate = math.hypot(p['ra_rate'] or 0, p['dec_rate'] or 0)          # arcsec / hour
            exp = u['exptime'] or _f(hdr.get('EXPTIME')) or 45
            dets = detect_sources(data, hdr)
            loaded.append({'u': u, 'data': data, 'hdr': hdr, 'dets': dets, 'exptime': exp, 'mjd': u['mjd'],
                           'trail': rate * exp / 3600.0, 'file': best['filename']})
        except Exception as e:
            log(f'  image {u["image"]}: {e}')
    if len(loaded) < 2:
        return 0
    try:
        p0 = loaded[0]['u']['pred']
        stacks = ps1_list(p0['ra'], p0['dec'], loaded[0]['u']['f'], 'stack')
        sdata, shdr = ps1_cutout(stacks[0]['filename'], p0['ra'], p0['dec'], int(size * 1.4)) if stacks else (None, None)
        static = detect_sources(sdata, shdr, thresh=2.0) if sdata is not None else []
    except Exception as e:
        log(f'  stack image unavailable: {e}')
        sdata, shdr, static = None, None, []
    for L in loaded:
        L['cands'] = moving_candidates(L['dets'], static, L['u']['pred'], L['trail'])
    links = link_pairs(loaded)
    log(f'  night {night}: {len(loaded)} images, {sum(len(L["cands"]) for L in loaded)} unexplained dots, {len(links)} matched pairs')
    if not links:
        return 0
    best = links[0]
    A, B = loaded[best['i']], loaded[best['j']]
    from astropy.wcs import WCS
    half = 60  # 30 arcsec boxes
    frames = [crop(A['data'], best['c1']['x'], best['c1']['y'], half), crop(B['data'], best['c2']['x'], best['c2']['y'], half)]
    labels = [f'night image 1 ({A["u"]["f"]})', 'image 2, minutes later']
    if sdata is not None:
        sx, sy = WCS(shdr).all_world2pix(best['c1']['ra'], best['c1']['dec'], 0)
        frames.append(crop(sdata, float(sx), float(sy), half))
        labels.append('deep stack (should be empty)')
    out = config.PLOTS / 'precovery'
    out.mkdir(parents=True, exist_ok=True)
    stem = f'{des.replace(" ", "_")}_{night}'
    png, gif = out / f'{stem}.png', out / f'{stem}.gif'
    when = time.strftime('%Y-%m-%d', time.gmtime((night + 0.5 - 40587) * 86400))
    make_review(png, gif, f'{des} — possible precovery on {when} (offset {math.hypot(best["c1"]["dx"], best["c1"]["dy"]):.0f}" from prediction)',
                frames, labels)
    obs = []
    for L, c in ((A, best['c1']), (B, best['c2'])):
        obs.append({'obsTime': obs_time_utc(L['hdr'], L['mjd'], L['exptime']), 'ra': c['ra'], 'dec': c['dec'],
                    'snr': round(c['snr'], 1), 'file': L['file'], 'filter': L['u']['f']})
    ades = ades_psv(des, obs, cfg)
    cid = con.execute('INSERT INTO pc_cands(des,night,archive,obs,offset,mdist,score,gif,png,ades,status,created) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (des, when, 'Pan-STARRS1 DR2', json.dumps(obs), math.hypot(best['c1']['dx'], best['c1']['dy']),
                       max(best['c1']['m'], best['c2']['m']), best['score'], str(gif.relative_to(config.DATA)),
                       str(png.relative_to(config.DATA)), ades, 'new', time.time())).lastrowid
    db.add_task(con, f'pc:cand:{cid}', 'asteroids', 'verify',
                f'Verify a possible sighting of {des} in {when} Pan-STARRS images',
                'Does the same dot move between the two images and is it missing from the deep stack? About 1 minute.',
                {'tab': 'asteroids', 'id': cid}, priority=1)
    return 1


# ================================================================== main
def run(ctx):
    cfg = ctx.cfg
    llm = LLM(cfg, ctx.log)
    snap = watch(ctx, llm)
    if not cfg.get('sarc_verified'):
        db.add_task(ctx.con, 'pc:sarc', 'asteroids', 'setup',
                    'One-time: ask the Minor Planet Center to verify you for archival (precovery) reports',
                    'The MPC now requires everyone who submits measurements from old archive images to be verified by its '
                    'Singletons and Archival submission Committee (SARC) first. Contact them before your first report; '
                    'then tick "SARC verified" in Setup.',
                    {'tab': 'asteroids', 'url': 'https://buttondown.com/MPC_newsletter/archive/newsletter-september-2025/'}, priority=4)
    targets = select_targets(ctx, snap)
    ctx.log(f'{len(targets)} precovery targets tonight')
    tot_img = tot_c = 0
    errors = []
    for t in targets:
        if ctx.time_left() < 180:
            ctx.log('time budget used up; stopping here to leave margin for next run')
            break
        try:
            n, c = search_target(ctx, t)
            if c == 0 and ctx.cfg['precovery'].get('ztf', True) and ctx.time_left() > 600:
                from . import ztf
                n2, c = ztf.search(ctx, t)
                n += n2
            tot_img += n
            tot_c += c
        except Exception as e:
            errors.append(f'{t["des"]}: {type(e).__name__}: {e}')
            ctx.log(f'ERROR searching {t["des"]}: {e}')
    if errors:
        ctx.log(f'Total precovery errors ({len(errors)}): ' + '; '.join(errors[:3]))
    w = 'watch updated' if snap else 'watch sources unavailable'
    return f'{w}; {len(targets)} asteroids searched in {tot_img} old images; {tot_c} possible sightings for you to verify (had {len(errors)} errors)'
