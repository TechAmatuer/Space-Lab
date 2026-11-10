"""Local copies of the star and galaxy catalogues Space Lab depends on, so an outage at a catalogue service cannot stop a job
or make it conclude something false (idea 17 of the plan).

Two kinds of copy, both under data/catalogs/:
- whole tables that are small enough to keep (the four the 3D map is built from, and the ASAS-SN catalogue of 687,000 variable
  stars). They are refreshed by the weekly `catalogs` job; a download replaces the copy only when it is complete.
- every answer a catalogue gave about one position (VSX, Gaia, ASAS-SN), kept with its date. A star checked again within
  FRESH_DAYS reuses the answer; when every mirror is down, any earlier answer (up to KEEP_DAYS old) is used and the log says so.
  VSX itself holds about 10 million entries, too many to copy every week, so it is covered this way.
"""
import gzip
import json
import math
import sqlite3
import time

import numpy as np

from . import config

DIR = config.DATA / 'catalogs'
FRESH_DAYS = 90            # a position's catalogue answer is reused this long without asking again
KEEP_DAYS = 365            # ... and used up to this age when the catalogue cannot be reached
TABLE_DAYS = 30            # a whole-table copy younger than this is used without downloading

# the ASAS-SN catalogue of variable stars, kept whole (the 3D map's four tables are listed in universe.CAT)
ASASSN = {'-source': 'II/366/catv2021', '-out': 'ASASSN-V,RAJ2000,DEJ2000,Type,Per', '-out.max': 'unlimited'}
ASASSN_MIN = 600000


def _db():
    DIR.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DIR / 'answers.db'), timeout=30)
    con.execute('CREATE TABLE IF NOT EXISTS cone (source TEXT, ra REAL, dec REAL, radius REAL, at REAL, rows TEXT, '
                'PRIMARY KEY (source, ra, dec, radius))')
    return con


def _key(ra, dec):
    return round(float(ra), 4), round(float(dec), 4)          # 0.36 arcsec: the same star asked twice is the same key


def cone(source, ra, dec, radius, ask, log=None, now=None):
    """The catalogue's answer for this position: a recent saved one, a new one from `ask()`, or (when asking fails) an older
    saved one. Raises only when the catalogue cannot be reached and nothing was ever saved for this position."""
    now = now or time.time()
    k = _key(ra, dec)
    con = _db()
    try:
        row = con.execute('SELECT at, rows FROM cone WHERE source=? AND ra=? AND dec=? AND radius=?', (source, k[0], k[1], float(radius))).fetchone()
        if row and now - row[0] < FRESH_DAYS * 86400:
            return json.loads(row[1])
        try:
            rows = list(ask() or [])
        except Exception:
            if row and now - row[0] < KEEP_DAYS * 86400:
                if log:
                    log(f'    {source} could not be reached; using its answer from {time.strftime("%Y-%m-%d", time.gmtime(row[0]))}')
                return json.loads(row[1])
            raise
        con.execute('INSERT OR REPLACE INTO cone VALUES (?,?,?,?,?,?)', (source, k[0], k[1], float(radius), now, json.dumps(rows)))
        con.commit()
        return rows
    finally:
        con.close()


def _path(name):
    return DIR / (name.replace(' ', '_').replace('/', '_') + '.tsv.gz')


def age_days(name, now=None):
    p = _path(name)
    return ((now or time.time()) - p.stat().st_mtime) / 86400 if p.exists() else None


def table_text(name, download, valid, log=None, max_age=TABLE_DAYS):
    """A whole catalogue table as VizieR's tab-separated text. A copy younger than max_age days is used as it is; otherwise
    it is downloaded, and the download replaces the copy only if `valid(text)`. When the download fails or is incomplete
    the copy is used whatever its age (and the log says so); with no copy, the download's error (or text) is passed on."""
    p = _path(name)
    a = age_days(name)
    if a is not None and a < max_age:
        return gzip.decompress(p.read_bytes()).decode('utf-8')
    try:
        text = download()
    except Exception:
        if a is None:
            raise
        if log:
            log(f'    {name}: the catalogue service did not answer; using the copy kept on this Mac ({a:.0f} days old)')
        return gzip.decompress(p.read_bytes()).decode('utf-8')
    if valid(text):
        DIR.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.tmp')
        tmp.write_bytes(gzip.compress(text.encode('utf-8'), 6))
        tmp.replace(p)
        _ARR.pop(name, None)
        return text
    if a is not None:
        if log:
            log(f'    {name}: the download was incomplete; using the copy kept on this Mac ({a:.0f} days old)')
        return gzip.decompress(p.read_bytes()).decode('utf-8')
    return text


def rows(text):
    """VizieR tab-separated text -> list of dicts (the header line, the units line and the dashes are skipped)."""
    lines = [ln for ln in text.splitlines() if ln and not ln.startswith('#')]
    if len(lines) < 3:
        return []
    head = [h.strip() for h in lines[0].split('\t')]
    return [dict(zip(head, [v.strip() for v in ln.split('\t')])) for ln in lines[3:] if not ln.startswith('-')]


def _num(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else np.nan
    except (TypeError, ValueError):
        return np.nan


_ARR = {}


def _arrays(name):
    """The ASAS-SN copy as arrays sorted by declination (read once per run)."""
    if name in _ARR:
        return _ARR[name]
    p = _path(name)
    if not p.exists():
        return None
    rs = rows(gzip.decompress(p.read_bytes()).decode('utf-8'))
    ra = np.array([_num(r.get('RAJ2000')) for r in rs]); dec = np.array([_num(r.get('DEJ2000')) for r in rs])
    ok = np.isfinite(ra) & np.isfinite(dec)
    o = np.argsort(dec[ok])
    keep = np.flatnonzero(ok)[o]
    a = {'ra': ra[keep], 'dec': dec[keep], 'name': [rs[i].get('ASASSN-V', '') for i in keep],
         'type': [rs[i].get('Type', '') for i in keep], 'per': np.array([_num(rs[i].get('Per')) for i in keep])}
    _ARR[name] = a
    return a


def local_cone(name, ra, dec, radius_arcsec, max_age=KEEP_DAYS):
    """Entries of a whole-table copy within the radius, as {'name', 'type', 'period', 'sep'}; None when there is no usable copy."""
    a_d = age_days(name)
    if a_d is None or a_d > max_age:
        return None
    a = _arrays(name)
    if a is None or not len(a['dec']):
        return None
    r = radius_arcsec / 3600.0
    lo, hi = np.searchsorted(a['dec'], dec - r), np.searchsorted(a['dec'], dec + r, side='right')
    out = []
    cosd = math.cos(math.radians(dec))
    for i in range(lo, hi):
        dra = ((a['ra'][i] - ra + 180) % 360 - 180) * cosd
        sep = math.hypot(dra, a['dec'][i] - dec) * 3600
        if sep <= radius_arcsec:
            p = a['per'][i]
            out.append({'name': a['name'][i], 'type': a['type'][i], 'period': float(p) if np.isfinite(p) else None, 'sep': round(sep, 2)})
    return sorted(out, key=lambda x: x['sep'])


def refresh(log, tables):
    """Bring every whole-table copy up to date. tables: name -> (download(), rows a complete copy has at least)."""
    out = {}
    for name, (download, need) in tables.items():
        before = age_days(name)
        t0 = time.time()
        try:
            text = table_text(name, download, lambda t: len(rows(t)) >= need, log=log, max_age=6)
            n = len(rows(text))
            fresh = age_days(name)
            renewed = fresh is not None and fresh < 0.1
            out[name] = {'rows': n, 'age_days': round(fresh, 1) if fresh is not None else None, 'renewed': renewed}
            if n < need:
                out[name]['error'] = f'incomplete ({n:,} rows)'
            log(f"  {name}: {n:,} rows ({'downloaded now' if renewed else f'copy kept, {fresh:.0f} days old' if fresh is not None else 'no copy'}, {time.time() - t0:.0f} s)")
        except Exception as e:
            out[name] = {'rows': 0, 'age_days': round(before, 1) if before is not None else None, 'error': str(e)[:200]}
            log(f"  {name}: could not be downloaded ({e}); {'the old copy stays' if before is not None else 'there is no copy yet'}")
    con = _db()
    try:
        gone = con.execute('DELETE FROM cone WHERE at < ?', (time.time() - KEEP_DAYS * 86400,)).rowcount
        n_ans = con.execute('SELECT COUNT(*) FROM cone').fetchone()[0]
        con.commit()
    finally:
        con.close()
    log(f'  saved answers about single positions: {n_ans:,} ({gone} older than a year removed)')
    return out, n_ans


def run(ctx):
    from . import db, universe
    log = ctx.log
    log('Keeping local copies of the catalogues (so an outage at a catalogue service cannot stop a job)')
    tables = {n: ((lambda p=p: universe._asu(p, timeout=900)), universe.MIN_ROWS[n]) for n, p in universe.CAT.items()}
    tables['ASAS-SN variables'] = ((lambda: universe._asu(ASASSN, timeout=1800)), ASASSN_MIN)
    out, n_ans = refresh(log, tables)
    ok = [k for k, v in out.items() if v.get('rows') and not v.get('error')]
    db.kv_set(ctx.con, 'catalogs', {'at': time.time(), 'tables': out, 'answers': n_ans})
    return f'catalogue copies: {len(ok)} of {len(out)} tables complete on this Mac; {n_ans:,} saved answers about single positions'
