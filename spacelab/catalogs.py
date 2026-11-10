"""Reference catalogs used to vet TESS signals.

* TESS Eclipsing Binary catalog (Prša et al. 2022, ApJS 258, 16): 4,584 stars already known to be eclipsing
  binaries. A signal on one of them is almost certainly the binary, not a planet.
* Gaia DR3 (via VizieR): RUWE > 1.4 or a non-single-star flag means Gaia sees signs of a companion star,
  which can dilute or mimic a planet's dip.
"""
import json
import time

from . import config, net

VIZIER = ('https://vizier.cds.unistra.fr', 'https://vizier.cfa.harvard.edu', 'https://vizier.iucaa.in')


def answered(tsv, source):
    """True when a VizieR reply really comes from that table (a mirror whose database is down sends an error page, which
    must never be read as 'no stars here')."""
    t = tsv or ''
    return '****' not in t[:4000] and (f'#Name: {source}' in t or f'#Table\t{source.replace("/", "_")}' in t)


def _asu(params, timeout=120):
    err = None
    for base in VIZIER:
        try:
            t = net.fetch_text(base + '/viz-bin/asu-tsv', params=params, timeout=timeout)
        except Exception as e:
            err = e
            continue
        if '****' in t[:4000]:                           # this mirror's database is down: its reply is an error page
            err = ConnectionError(f'{base}: database not reachable')
            continue
        return t
    raise err


def _rows(tsv):
    lines = [ln for ln in tsv.splitlines() if ln and not ln.startswith('#')]
    if len(lines) < 3:
        return []
    head = [h.strip() for h in lines[0].split('\t')]
    return [dict(zip(head, [v.strip() for v in ln.split('\t')])) for ln in lines[3:] if not ln.startswith('-')]


def tess_ebs(max_age_days=30):
    """Set of TIC IDs in the TESS EB catalog (cached in data/cache)."""
    path = config.CACHE / 'tess_ebs.json'
    if path.is_file() and time.time() - path.stat().st_mtime < max_age_days * 86400:
        return set(json.loads(path.read_text()))
    rows = _rows(_asu({'-source': 'J/ApJS/258/16/tess-ebs', '-out': 'TIC', '-out.max': '10000'}))
    tics = sorted({int(r['TIC']) for r in rows if r.get('TIC', '').strip().isdigit()})
    if len(tics) > 1000:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(tics))
    return set(tics)


def gaia_binarity(ra, dec, radius_arcsec=4.0):
    """Gaia DR3 RUWE and non-single-star flag of the star at (ra, dec): brightest source in the radius."""
    rows = _rows(_asu({'-source': 'I/355/gaiadr3', '-c': f'{ra} {dec:+}', '-c.rs': str(radius_arcsec),
                       '-out': 'Source,Gmag,RUWE,NSS', '-out.max': '5'}, timeout=60))
    best = None
    for r in rows:
        try:
            g = float(r.get('Gmag') or 99)
        except ValueError:
            continue
        if best is None or g < best['gmag']:
            def num(x):
                try:
                    return float(x)
                except (TypeError, ValueError):
                    return None
            best = {'source': r.get('Source'), 'gmag': g, 'ruwe': num(r.get('RUWE')), 'nss': int(num(r.get('NSS')) or 0)}
    return best
