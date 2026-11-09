"""Precovery in Zwicky Transient Facility images (2018 to today), via IRSA.

Pan-STARRS DR2 only covers 2010-2014, which is too long ago for most recently found asteroids (their orbits are
too uncertain that far back). ZTF images the whole northern sky every couple of nights since 2018, so it is the
right archive for new near-Earth asteroids: IRSA's Moving Object Search Tool (MOST) lists every ZTF image that
covered the asteroid's predicted path; Space Lab then downloads small cutouts around the JPL Horizons prediction,
removes everything also seen in ZTF's deep reference image, and looks for a dot that moves consistently between
two images of the same night, exactly like the Pan-STARRS search.
"""
import io
import math
import time
from datetime import datetime, timedelta, timezone

import numpy as np

from . import config, db, net
from .precovery import (crop, detect_sources, horizons_predict, link_pairs, make_review, moving_candidates,
                        ades_psv, _f)

MOST = 'https://irsa.ipac.caltech.edu/cgi-bin/MOST/nph-most'
IBE = 'https://irsa.ipac.caltech.edu/ibe/data/ztf/products'
PIX = 1.012          # arcsec / pixel
STN = 'I41'          # ZTF at Palomar
LIMIT = {'zg': 20.8, 'zr': 20.6, 'zi': 20.2}


def parse_ipac(text):
    """IPAC table (as returned by MOST in Brief mode) -> list of dicts."""
    lines = text.splitlines()
    head = [i for i, ln in enumerate(lines) if ln.startswith('|')]
    if not head:
        return []
    hdr = lines[head[0]]
    bars = [i for i, ch in enumerate(hdr) if ch == '|']
    names = [hdr[a + 1:b].strip() for a, b in zip(bars, bars[1:])]
    rows = []
    for ln in lines[head[-1] + 1:]:
        if not ln.strip() or ln.startswith('\\'):
            continue
        vals = [ln[a + 1:b].strip() if b <= len(ln) + 1 else '' for a, b in zip(bars, bars[1:])]
        if len(vals) == len(names):
            rows.append(dict(zip(names, vals)))
    return rows


def images(des, begin='2018-03-17', end=None):
    end = end or datetime.now(timezone.utc).strftime('%Y-%m-%d')
    text = net.fetch_text(MOST, params={'catalog': 'ztf', 'input_type': 'name_input', 'obj_name': des,
                                        'obs_begin': begin, 'obs_end': end, 'output_mode': 'Brief'}, timeout=300)
    if 'ERROR' in text[:400].upper() and '|' not in text:
        raise RuntimeError(text.strip()[:200])
    out = []
    for r in parse_ipac(text):
        try:
            out.append({'jd': float(r['obsjd']), 'ffd': r['filefracday'], 'field': int(r['field']), 'ccd': int(r['ccdid']),
                        'q': int(r['qid']), 'f': r['filtercode'], 'itype': r.get('imgtypecode') or 'o',
                        'vmag': _f(r.get('vmag')), 'ra': _f(r.get('ra_obj')), 'dec': _f(r.get('dec_obj'))})
        except (KeyError, ValueError):
            continue
    return out


def sci_url(im, ra, dec, size):
    d = im['ffd']
    return (f"{IBE}/sci/{d[:4]}/{d[4:8]}/{d[8:]}/ztf_{d}_{im['field']:06d}_{im['f']}_c{im['ccd']:02d}_{im['itype']}_"
            f"q{im['q']}_sciimg.fits?center={ra:.6f},{dec:+.6f}&size={size}arcsec&gzip=false")


def ref_url(im, ra, dec, size):
    f = f"{im['field']:06d}"
    return (f"{IBE}/ref/{f[:3]}/field{f}/{im['f']}/ccd{im['ccd']:02d}/q{im['q']}/ztf_{f}_{im['f']}_c{im['ccd']:02d}_q{im['q']}"
            f"_refimg.fits?center={ra:.6f},{dec:+.6f}&size={size}arcsec&gzip=false")


def cutout(url):
    from astropy.io import fits
    raw = net.fetch(url, timeout=180, min_gap=0.5)
    with fits.open(io.BytesIO(raw)) as hd:
        return hd[0].data.astype(np.float32), hd[0].header.copy()


def search(ctx, t, max_nights=3, days_back=None):
    """Returns (images used, possible sightings). days_back limits the image query (much faster for new objects)."""
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    des = t['des']
    begin = (datetime.now(timezone.utc) - timedelta(days=days_back)).strftime('%Y-%m-%d') if days_back else '2018-03-17'
    try:
        ims = images(des, begin=begin)
    except Exception as e:
        log(f'  ZTF: image search failed ({e})')
        return 0, 0
    ims = [i for i in ims if i['vmag'] is not None and i['vmag'] <= LIMIT.get(i['f'], 20.5) - 0.3]
    nights = {}
    for i in ims:
        nights.setdefault(int(math.floor(i['jd'] - 2400000.5 - 0.3)), []).append(i)
    nights = {k: sorted(v, key=lambda x: x['jd'])[:4] for k, v in nights.items() if len(v) >= 2}
    log(f'  ZTF: {len(ims)} images where it should be bright enough, {len(nights)} nights with 2+ images')
    if not nights:
        con.execute("UPDATE pc_targets SET note=COALESCE(note,'') || ? WHERE des=?", ('; ZTF: no usable nights', des))
        return 0, 0
    # earliest nights first: they extend the observed arc the most
    nimg = ncand = 0
    for night in sorted(nights)[:max_nights]:
        if ctx.time_left() < 180:
            break
        group = nights[night]
        try:
            preds = horizons_predict(des, STN, [g['jd'] - 2400000.5 for g in group])
        except Exception as e:
            log(f'  ZTF night {night}: Horizons failed ({e})')
            continue
        loaded = []
        for g, p in zip(group, preds):
            if p.get('ra') is None:
                continue
            p.setdefault('smaa', 10.0)
            p['smaa'] = p['smaa'] or 10.0
            if p['smaa'] > 900:
                log(f'  ZTF night {night}: orbit too uncertain then (±{p["smaa"]:.0f}″)')
                break
            size = int(min(1800, max(90, 2 * (p['smaa'] + 30))))
            try:
                data, hdr = cutout(sci_url(g, p['ra'], p['dec'], size))
            except Exception as e:
                log(f'  ZTF image {g["ffd"]}: {e}')
                continue
            rate = math.hypot(p.get('ra_rate') or 0, p.get('dec_rate') or 0)
            exp = _f(hdr.get('EXPTIME')) or 30.0
            loaded.append({'u': {'pred': p, 'f': g['f'], 'mjd': g['jd'] - 2400000.5, 'image': g['ffd']}, 'data': data, 'hdr': hdr,
                           'dets': detect_sources(data, hdr), 'exptime': exp, 'mjd': g['jd'] - 2400000.5,
                           'trail': rate * exp / 3600.0, 'file': g['ffd'], 'im': g, 'size': size})
        if len(loaded) < 2:
            continue
        nimg += len(loaded)
        L0 = loaded[0]
        try:
            sdata, shdr = cutout(ref_url(L0['im'], L0['u']['pred']['ra'], L0['u']['pred']['dec'], int(L0['size'] * 1.3)))
            static = detect_sources(sdata, shdr, thresh=2.0)
        except Exception as e:
            log(f'  ZTF reference image unavailable: {e}')
            sdata = shdr = None
            static = []
        for L in loaded:
            L['cands'] = moving_candidates(L['dets'], static, L['u']['pred'], L['trail'], pix=PIX, static_r=2.5)
        links = link_pairs(loaded, tol_base=3.0)
        log(f'  ZTF night {night}: {len(loaded)} images, {sum(len(L["cands"]) for L in loaded)} unexplained dots, {len(links)} matched pairs')
        if links:
            ncand += save(ctx, t, night, loaded, links[0], sdata, shdr)
    con.execute("UPDATE pc_targets SET ncand=ncand+?, note=COALESCE(note,'') || ? WHERE des=?",
                (ncand, f'; ZTF: {nimg} images searched', des))
    return nimg, ncand


def save(ctx, t, night, loaded, best, sdata, shdr):
    import json
    from astropy.wcs import WCS
    from .precovery import obs_time_utc
    con, cfg = ctx.con, ctx.cfg
    des = t['des']
    A, B = loaded[best['i']], loaded[best['j']]
    half = 30
    frames = [crop(A['data'], best['c1']['x'], best['c1']['y'], half), crop(B['data'], best['c2']['x'], best['c2']['y'], half)]
    labels = [f'ZTF image 1 ({A["u"]["f"]})', 'image 2, same night']
    if sdata is not None:
        sx, sy = WCS(shdr).all_world2pix(best['c1']['ra'], best['c1']['dec'], 0)
        frames.append(crop(sdata, float(sx), float(sy), half))
        labels.append('ZTF deep reference (should be empty)')
    out = config.PLOTS / 'precovery'
    out.mkdir(parents=True, exist_ok=True)
    stem = f'{des.replace(" ", "_")}_ztf_{night}'
    png, gif = out / f'{stem}.png', out / f'{stem}.gif'
    when = time.strftime('%Y-%m-%d', time.gmtime((night + 0.5 - 40587) * 86400))
    off = math.hypot(best['c1']['dx'], best['c1']['dy'])
    make_review(png, gif, f'{des} — possible ZTF sighting on {when} (offset {off:.0f}" from prediction)', frames, labels)
    obs = [{'obsTime': obs_time_utc(L['hdr'], L['mjd'], L['exptime']), 'ra': c['ra'], 'dec': c['dec'], 'snr': round(c['snr'], 1),
            'file': L['file'], 'filter': L['u']['f']} for L, c in ((A, best['c1']), (B, best['c2']))]
    cid = con.execute('INSERT INTO pc_cands(des,night,archive,obs,offset,mdist,score,gif,png,ades,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      (des, when, 'ZTF', json.dumps(obs), off, max(best['c1']['m'], best['c2']['m']), best['score'],
                       str(gif.relative_to(config.DATA)), str(png.relative_to(config.DATA)), ades_psv(des, obs, cfg, stn=STN),
                       'new', time.time())).lastrowid
    db.add_task(con, f'pc:cand:{cid}', 'asteroids', 'verify', f'Verify a possible sighting of {des} in {when} ZTF images',
                'Same dot moving between the two images, and missing from the reference image? About 1 minute.',
                {'tab': 'asteroids', 'id': cid}, priority=1)
    return 1
