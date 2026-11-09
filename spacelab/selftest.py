"""Self-test: checks every outside service, your local models and the EXOTIC install. Results show in Setup."""
import importlib
import time

from . import config, db, net
from .llm import LLM

CHECKS = [
    ('arXiv API', 'https://export.arxiv.org/api/query?search_query=cat:astro-ph.EP&max_results=1'),
    ('NASA Exoplanet Archive', 'https://exoplanetarchive.ipac.caltech.edu/TAP/sync?query=select+top+1+pl_name+from+ps&format=json'),
    ('ExoFOP (TESS TOI list)', 'https://exofop.ipac.caltech.edu/tess/'),
    ('MAST (TESS light curves)', 'https://mast.stsci.edu/tesscut/api/v0.1/sector?ra=10&dec=10&radius=1m'),
    ('JPL Sentry', 'https://ssd-api.jpl.nasa.gov/sentry.api'),
    ('JPL Scout', 'https://ssd-api.jpl.nasa.gov/scout.api'),
    ('JPL close approaches', 'https://ssd-api.jpl.nasa.gov/cad.api?date-min=now&date-max=%2B7&dist-max=0.02'),
    ('JPL Horizons', 'https://ssd.jpl.nasa.gov/api/horizons.api?format=json&COMMAND=%27433%27&EPHEM_TYPE=OBSERVER&MAKE_EPHEM=NO'),
    ('CADC SSOIS (old-image search)', 'https://www.cadc-ccda.hia-iha.nrc-cnrc.gc.ca/en/ssois/'),
    ('Pan-STARRS image service', 'https://ps1images.stsci.edu/cgi-bin/ps1filenames.py?ra=10&dec=10&filters=r'),
    ('Gaia via VizieR', 'https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=I/355/gaiadr3&-c=10+10&-c.rs=10&-out.max=1'),
]


def run(ctx):
    con, log = ctx.con, ctx.log
    out = {'t': time.time(), 'net': [], 'llm': {}, 'python': {}}
    for name, url in CHECKS:
        ok, msg = net.reachable(url, timeout=30)
        if not ok and 'VizieR' in name:
            ok, msg = net.reachable(url.replace('vizier.cds.unistra.fr', 'vizier.cfa.harvard.edu'), timeout=30)
            msg += ' (Harvard mirror)'
        out['net'].append({'name': name, 'ok': ok, 'msg': msg})
        log(f'{"OK " if ok else "BAD"} {name}: {msg}')
    llm = LLM(ctx.cfg, log)
    have = llm.models()
    out['llm']['models'] = have
    out['llm']['writer'] = llm.chat('Reply with exactly: ready', max_tokens=20)
    out['llm']['writer_model'] = llm.used[-1] if llm.used else None
    vec = llm.embed(['test'])
    out['llm']['embed'] = bool(vec)
    log(f'models: {have}; writer reply: {out["llm"]["writer"]!r} via {out["llm"]["writer_model"]}; embeddings: {bool(vec)}')
    for mod in ('numpy', 'astropy', 'astroquery', 'lightkurve', 'sep', 'astroalign', 'matplotlib', 'PIL'):
        try:
            m = importlib.import_module(mod)
            out['python'][mod] = getattr(m, '__version__', 'ok')
        except Exception as e:
            out['python'][mod] = f'missing: {e}'
    out['python']['exotic'] = 'installed' if config.EXOTIC_BIN.exists() else 'missing (restart Meridian; it repairs Space Lab)'
    try:  # SSOIS query format check with a well-known asteroid
        from .precovery import ssois
        rows = ssois(con, '2004 MN4', log)
        out['ssois'] = f'ok: {len(rows)} archive images listed for (99942) Apophis'
    except Exception as e:
        out['ssois'] = f'failed: {e}'
    log('SSOIS:', out['ssois'])
    db.kv_set(con, 'selftest', out)
    bad = [c['name'] for c in out['net'] if not c['ok']]
    return ('all services reachable' if not bad else 'unreachable: ' + ', '.join(bad)) + \
        f"; AI writer: {out['llm']['writer_model'] or 'none'}; EXOTIC {out['python']['exotic']}"
