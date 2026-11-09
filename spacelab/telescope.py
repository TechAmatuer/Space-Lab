"""Your own smart telescope (Seestar S50, Dwarf 3, or any camera that saves FITS frames).

Drop a night's folder (or zip) of individual frames into inbox/telescope. Space Lab:
  1. reads the FITS headers (time, exposure, pointing),
  2. turns colour (Bayer) frames into clean green-channel frames, the standard for one-shot-colour photometry,
  3. averages very long runs of short exposures into ~600 frames so EXOTIC stays fast,
  4. hands the result to the Exoplanet Watch pipeline (same star finding, EXOTIC, checks, AAVSO helper),
     using YOUR location and camera details instead of MicroObservatory's.
Tip for Seestar: turn on "Save each frame" and use 10-20 s exposures; for Dwarf 3 keep the raw FITS files.
"""
import json
import math
import re
import shutil
import time
import zipfile
from pathlib import Path

import numpy as np

from . import config, db

INBOX = config.INBOX / 'telescope'
FITS_EXT = ('.fits', '.fit', '.fts')


def _fits(folder):
    return sorted(p for p in Path(folder).rglob('*') if p.is_file() and p.suffix.lower() in FITS_EXT
                  and not p.name.startswith('._') and '__MACOSX' not in p.parts and 'mono' not in p.parts)


def bayer_green(data, pattern):
    """2x2 superpixel green: average of the two green pixels of each Bayer cell (half the resolution)."""
    pattern = (pattern or 'RGGB').upper().strip()
    h, w = data.shape[0] // 2 * 2, data.shape[1] // 2 * 2
    d = data[:h, :w].astype(np.float32)
    cells = {'00': d[0::2, 0::2], '01': d[0::2, 1::2], '10': d[1::2, 0::2], '11': d[1::2, 1::2]}
    greens = [cells[k] for k, c in zip(('00', '01', '10', '11'), pattern) if c == 'G']
    if len(greens) != 2:
        greens = [cells['01'], cells['10']]
    return 0.5 * (greens[0] + greens[1])


def _time(h):
    from astropy.time import Time
    for k in ('DATE-OBS', 'DATE-BEG', 'DATE'):
        v = h.get(k)
        if v:
            try:
                return Time(str(v).replace(' ', 'T'), format='isot', scale='utc').jd
            except Exception:
                continue
    return None


def prepare(folder, out, log=print, max_frames=600):
    """Write green-channel (or copied mono) frames into `out`. Returns (n_frames, info dict)."""
    from astropy.io import fits
    files = _fits(folder)
    if not files:
        return 0, {}
    out.mkdir(parents=True, exist_ok=True)
    h0 = fits.getheader(files[0])
    pat = h0.get('BAYERPAT') or h0.get('COLORTYP') or h0.get('CFA-PAT')
    colour = bool(pat) or str(h0.get('INSTRUME', '')).lower().startswith(('seestar', 'dwarf'))
    frames = []
    for f in files:
        try:
            with fits.open(f, memmap=False) as hd:
                hdu = next(x for x in hd if getattr(x, 'data', None) is not None and x.data.ndim == 2)
                frames.append((_time(hdu.header), f, hdu.header.copy()))
        except Exception as e:
            log(f'  skipped {f.name}: {e}')
    frames = [x for x in frames if x[0]]
    frames.sort(key=lambda x: x[0])
    if not frames:
        return 0, {}
    g = max(1, math.ceil(len(frames) / max_frames))
    n = 0
    for i in range(0, len(frames), g):
        grp = frames[i:i + g]
        imgs = []
        for _, f, h in grp:
            with fits.open(f, memmap=False) as hd:
                hdu = next(x for x in hd if getattr(x, 'data', None) is not None and x.data.ndim == 2)
                d = hdu.data.astype(np.float32)
            imgs.append(bayer_green(d, pat) if colour else d)
        img = np.mean(imgs, axis=0).astype(np.float32)
        h = grp[len(grp) // 2][2]
        exp = sum(float(x[2].get('EXPTIME') or x[2].get('EXPOSURE') or 0) for x in grp)
        from astropy.time import Time
        mid = Time(np.mean([x[0] for x in grp]), format='jd').isot
        nh = fits.Header()
        for k in ('OBJECT', 'RA', 'DEC', 'OBJCTRA', 'OBJCTDEC', 'INSTRUME', 'TELESCOP', 'FOCALLEN', 'GAIN', 'CCD-TEMP', 'FILTER'):
            if k in h:
                nh[k] = h[k]
        nh['DATE-OBS'] = mid
        nh['EXPTIME'] = round(exp / len(grp), 3) if g == 1 else round(exp, 3)
        nh['NCOMBINE'] = len(grp)
        if colour:
            nh['FILTER'] = 'TG'
            nh['XBINNING'] = nh['YBINNING'] = 2
            nh['HISTORY'] = 'Space Lab: green channel of the Bayer frame (2x2 superpixel)'
        fits.PrimaryHDU(img, nh).writeto(out / f'frame_{n:04d}.fits', overwrite=True)
        n += 1
    info = {'instrument': str(h0.get('INSTRUME') or h0.get('TELESCOP') or 'unknown'), 'object': str(h0.get('OBJECT') or ''),
            'ra': h0.get('RA'), 'dec': h0.get('DEC'), 'colour': colour, 'bayer': pat, 'raw_frames': len(frames),
            'frames': n, 'grouped': g, 'exptime': float(h0.get('EXPTIME') or h0.get('EXPOSURE') or 0),
            'focal_mm': h0.get('FOCALLEN'), 'pixel_um': h0.get('XPIXSZ')}
    log(f'  {len(frames)} raw frames -> {n} {"green-channel " if colour else ""}frames ({g} per frame)')
    return n, info


def scan_inbox(con, log):
    INBOX.mkdir(parents=True, exist_ok=True)
    added = 0
    for item in sorted(INBOX.iterdir()):
        if item.name.startswith('.') or item.name in ('processed', 'failed'):
            continue
        name = 'scope_' + re.sub(r'[^A-Za-z0-9_.-]+', '_', item.stem if item.is_file() else item.name)[:70]
        if db.one(con, 'SELECT id FROM exo WHERE name=?', (name,)):
            continue
        work = config.EXO_WORK / name
        raw = work / 'raw'
        try:
            if item.is_file() and item.suffix.lower() == '.zip':
                raw.mkdir(parents=True, exist_ok=True)
                with zipfile.ZipFile(item) as z:
                    for m in z.infolist():
                        target = (raw / m.filename).resolve()
                        if str(target).startswith(str(raw.resolve())):
                            z.extract(m, raw)
                done = INBOX / 'processed'
                done.mkdir(exist_ok=True)
                shutil.move(str(item), str(done / item.name))
            elif item.is_dir():
                work.mkdir(parents=True, exist_ok=True)
                shutil.move(str(item), str(raw))
            else:
                continue
            n, info = prepare(raw, work / 'mono', log)
        except Exception as e:
            log(f'telescope dataset {item.name}: {e}')
            n, info = 0, {'error': str(e)}
        status = 'new' if n >= 10 else 'failed'
        con.execute('INSERT INTO exo(name,folder,nimg,status,error,created,updated,source,planet) VALUES(?,?,?,?,?,?,?,?,?)',
                    (name, str(work / 'mono'), n, status, None if n >= 10 else f'only {n} usable FITS frames',
                     time.time(), time.time(), 'scope', None))
        db.kv_set(con, f'scope_info:{name}', info)
        log(f'new telescope dataset {name}: {n} frames')
        added += 1
    return added


def match_planet(ra, dec, planets, radius_arcmin=6):
    """Nearest known transiting planet host to the pointing (FITS RA/DEC), from the cached archive list."""
    try:
        ra, dec = float(ra), float(dec)
    except (TypeError, ValueError):
        return None
    best, bd = None, radius_arcmin / 60
    for p in planets or []:
        try:
            pr, pd = float(p['ra']), float(p['dec'])
        except (TypeError, ValueError, KeyError):
            continue
        d = math.degrees(math.acos(min(1, max(-1, math.sin(math.radians(dec)) * math.sin(math.radians(pd)) +
                                               math.cos(math.radians(dec)) * math.cos(math.radians(pd)) * math.cos(math.radians(ra - pr))))))
        if d < bd:
            best, bd = p, d
    return best['pl_name'] if best else None
