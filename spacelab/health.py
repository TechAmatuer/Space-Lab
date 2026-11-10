"""Is everything working? (ideas 84 and 85 of the plan)

The hourly `health` job checks that the outside services Space Lab depends on really answer (a catalogue mirror must send a
table, not an error page), and then looks for problems: a job that failed twice in a row, a job that has not succeeded for
much longer than its schedule, a public website that stopped updating, a nearly full disk, a service down for hours.
New problems are sent as an alert (a Mac notification; also a phone push through ntfy.sh if you set a topic name in
settings: "alerts": {"ntfy_topic": "..."}). Each problem is sent once, repeated at most once a day while it lasts,
and followed by a short "back to normal" message. Meridian shows the same report in the System health card.
"""
import json
import shutil
import subprocess
import sys
import time
import urllib.request

from . import config, db, net

# name, address to ask, how to tell a real answer, which jobs depend on it
SERVICES = [
    ('NASA MAST (TESS light curves)', 'https://mast.stsci.edu/api/v0.1/Download/file?uri=mast:TESS/product/x', 'reach', ['tess', 'variables']),
    ('VizieR, Harvard mirror', 'https://vizier.cfa.harvard.edu/viz-bin/asu-tsv?-source=I/355/gaiadr3&-c=10+10&-c.rs=60&-out.max=1', 'table', ['variables', 'universe', 'catalogs']),
    ('VizieR, South Africa mirror', 'https://vizier.idia.ac.za/viz-bin/asu-tsv?-source=I/355/gaiadr3&-c=10+10&-c.rs=60&-out.max=1', 'table', ['variables', 'universe', 'catalogs']),
    ('VizieR, Strasbourg (CDS)', 'https://vizier.cds.unistra.fr/viz-bin/asu-tsv?-source=I/355/gaiadr3&-c=10+10&-c.rs=60&-out.max=1', 'table', ['variables', 'universe', 'catalogs']),
    ('VizieR, India mirror', 'https://vizier.iucaa.in/viz-bin/asu-tsv?-source=I/355/gaiadr3&-c=10+10&-c.rs=60&-out.max=1', 'table', ['variables', 'universe', 'catalogs']),
    ('JPL Horizons (positions)', 'https://ssd.jpl.nasa.gov/api/horizons.api?format=json&COMMAND=%27399%27&OBJ_DATA=NO&MAKE_EPHEM=NO', 'reach', ['solar', 'plates', 'precovery']),
    ('IRSA (ZTF images, Gaia copy)', 'https://irsa.ipac.caltech.edu/TAP/availability', 'reach', ['precovery', 'plates']),
    ('ESA Gaia archive', 'https://gea.esac.esa.int/tap-server/tap/availability', 'reach', ['plates']),
    ('Minor Planet Center', 'https://minorplanetcenter.net/iau/NEO/toconfirm_tabular.html', 'reach', ['neocp', 'precovery']),
    ('AAVSO VSX', 'https://www.aavso.org/vsx/index.php?view=api.object&ident=RR+Lyr&format=json', 'reach', ['variables']),
    ('arXiv', 'https://export.arxiv.org/api/query?search_query=cat:astro-ph.EP&max_results=1', 'reach', ['arxiv', 'library', 'radar']),
]
DISK_LOW_GB = 20
SITE_STALE_H = 12
SERVICE_DOWN_H = 6
REPEAT_H = 24


def _hours(spec):
    """How often a job is meant to run, in hours ('every 3h' -> 3, '01:30' -> 24), or None for jobs run by hand."""
    if not spec:
        return None
    s = str(spec)
    if s.startswith('every'):
        try:
            return float(s.split()[1].rstrip('h'))
        except (IndexError, ValueError):
            return None
    return 24.0


def probe(fetch=None):
    """Ask each service once. Returns [{name, ok, msg, used_by}]."""
    out = []
    for name, url, how, used in SERVICES:
        t0 = time.time()
        try:
            if how == 'table':
                from .catalogs import answered
                t = (fetch or (lambda u: net.fetch_text(u, timeout=40, retries=1)))(url)
                ok = answered(t, 'I/355/gaiadr3')
                msg = f'answered in {time.time() - t0:.1f} s' if ok else 'reachable, but its database sent no table'
            else:
                ok, msg = (fetch_reach(fetch, url) if fetch else net.reachable(url, timeout=30))
        except Exception as e:
            ok, msg = False, f'{type(e).__name__}: {str(e)[:100]}'
        out.append({'name': name, 'ok': bool(ok), 'msg': msg, 'used_by': used})
    return out


def fetch_reach(fetch, url):
    t = fetch(url)
    return (t is not None), 'answered'


def job_report(con, cfg, now=None):
    """For every scheduled job: last run, last success, failures in a row, and whether it is overdue."""
    from .jobs import JOBS
    now = now or time.time()
    sched = cfg.get('schedule', {})
    out = []
    for job, (_mod, label) in JOBS.items():
        rows = db.rows(con, 'SELECT started, finished, status, summary FROM runs WHERE job=? ORDER BY id DESC LIMIT 12', (job,))
        every = _hours(sched.get(job))
        last_ok = next((r for r in rows if r['status'] == 'ok'), None)
        fails = 0
        for r in rows:
            if r['status'] == 'ok':
                break
            if r['status'] not in ('running', None):
                fails += 1
        ok_age_h = (now - (last_ok['finished'] or last_ok['started'])) / 3600 if last_ok else None
        overdue = bool(every) and rows and (ok_age_h is None or ok_age_h > 2 * every + 2)
        out.append({'job': job, 'label': label, 'every_h': every, 'last': rows[0] if rows else None,
                    'last_ok': (last_ok['finished'] or last_ok['started']) if last_ok else None, 'ok_age_h': ok_age_h,
                    'fails_in_a_row': fails, 'overdue': bool(overdue)})
    return out


def disk():
    u = shutil.disk_usage(str(config.DATA))
    return {'free_gb': round(u.free / 1e9, 1), 'total_gb': round(u.total / 1e9, 1), 'used_pct': round(100 * u.used / u.total, 1)}


def problems(jobs, services, dk, site_age_h, svc_down_since, now=None):
    """Plain-language problems, each with a stable key (for alerts) and how serious it is."""
    now = now or time.time()
    out = []
    for j in jobs:
        if j['fails_in_a_row'] >= 2:
            out.append({'key': f"job:{j['job']}:failing", 'level': 'bad',
                        'text': f"{j['label']} failed {j['fails_in_a_row']} times in a row. Last message: {(j['last'] or {}).get('summary') or 'none'}"})
        elif j['overdue']:
            age = 'never' if j['ok_age_h'] is None else f"{j['ok_age_h']:.0f} hours ago"
            out.append({'key': f"job:{j['job']}:overdue", 'level': 'warn', 'text': f"{j['label']} last succeeded {age}; it should run every {j['every_h']:.0f} hours."})
    if site_age_h is not None and site_age_h > SITE_STALE_H:
        out.append({'key': 'site:stale', 'level': 'bad', 'text': f'The public website was last updated {site_age_h:.0f} hours ago.'})
    if dk and dk['free_gb'] < DISK_LOW_GB:
        out.append({'key': 'disk:low', 'level': 'bad', 'text': f"Only {dk['free_gb']} GB free on this Mac's disk; jobs will start failing when it is full."})
    for s in services:
        since = svc_down_since.get(s['name'])
        if not s['ok'] and since and now - since >= SERVICE_DOWN_H * 3600:
            out.append({'key': f"service:{s['name']}", 'level': 'warn',
                        'text': f"{s['name']} has not answered properly for {(now - since) / 3600:.0f} hours ({s['msg']}). Jobs that use it: {', '.join(s['used_by'])}."})
    return out


def report(con, cfg, now=None):
    """Everything Meridian's System health card shows."""
    now = now or time.time()
    jobs = job_report(con, cfg, now)
    svc = db.kv_get(con, 'health_services') or {}
    site = next((j for j in jobs if j['job'] == 'publish'), None)
    site_age = site['ok_age_h'] if site else None
    dk = disk()
    probs = problems(jobs, svc.get('services', []), dk, site_age if (cfg.get('schedule') or {}).get('publish') else None,
                     svc.get('down_since', {}), now)
    return {'at': now, 'checked': svc.get('at'), 'services': svc.get('services', []), 'jobs': jobs, 'disk': dk,
            'site_age_h': site_age, 'catalogs': db.kv_get(con, 'catalogs'), 'problems': probs,
            'alerts': {'mac': sys.platform == 'darwin', 'phone': bool(((cfg.get('alerts') or {}).get('ntfy_topic') or '').strip())}}


# ------------------------------------------------------------------ alerts
def _mac(title, text):
    if sys.platform != 'darwin' or not shutil.which('osascript'):
        return False
    q = lambda s: '"' + str(s).replace('\\', '\\\\').replace('"', '\\"')[:230] + '"'
    try:
        subprocess.run(['osascript', '-e', f'display notification {q(text)} with title {q(title)}'], timeout=10, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except Exception:
        return False


def _ntfy(topic, title, text):
    topic = (topic or '').strip()
    if not topic or not all(c.isalnum() or c in '-_' for c in topic):
        return False
    try:
        req = urllib.request.Request('https://ntfy.sh/' + topic, data=text.encode('utf-8'), headers={'Title': title, 'User-Agent': net.UA})
        with urllib.request.urlopen(req, timeout=15, context=net.ssl_ctx()) as r:
            return r.status < 300
    except Exception:
        return False


def send(cfg, title, text, sink=None):
    """Send one alert through every channel that is set up. sink: a list to append to instead (tests)."""
    if sink is not None:
        sink.append((title, text))
        return True
    a = cfg.get('alerts') or {}
    ok = False
    if a.get('mac', True):
        ok = _mac(title, text) or ok
    if a.get('ntfy_topic'):
        ok = _ntfy(a['ntfy_topic'], title, text) or ok
    return ok


def alert(con, cfg, probs, now=None, sink=None):
    """New problems are sent; a problem still there is repeated once a day; a solved one gets a 'back to normal'."""
    now = now or time.time()
    state = db.kv_get(con, 'health_alerts') or {}
    sent = []
    keys = {p['key'] for p in probs}
    for p in probs:
        s = state.get(p['key'])
        if not s or now - s.get('sent', 0) >= REPEAT_H * 3600:
            if send(cfg, 'Space Lab: ' + ('problem' if p['level'] == 'bad' else 'warning'), p['text'], sink):
                sent.append(p['key'])
            state[p['key']] = {'since': (s or {}).get('since', now), 'sent': now, 'text': p['text']}
    for k in [k for k in state if k not in keys]:
        send(cfg, 'Space Lab: back to normal', 'Solved: ' + state[k].get('text', k), sink)
        sent.append('ok:' + k)
        del state[k]
    db.kv_set(con, 'health_alerts', state)
    return sent


def run(ctx, fetch=None, sink=None):
    log, con, cfg = ctx.log, ctx.con, config.load()
    now = time.time()
    services = probe(fetch)
    prev = db.kv_get(con, 'health_services') or {}
    down = dict(prev.get('down_since') or {})
    for s in services:
        if s['ok']:
            down.pop(s['name'], None)
        else:
            down.setdefault(s['name'], now)
        log(f"  {'ok  ' if s['ok'] else 'DOWN'} {s['name']}: {s['msg']}")
    db.kv_set(con, 'health_services', {'at': now, 'services': services, 'down_since': down})
    rep = report(con, cfg, now)
    for p in rep['problems']:
        log(f"  problem: {p['text']}")
    sent = alert(con, cfg, rep['problems'], now, sink)
    if sent:
        log(f'  alerts sent: {", ".join(sent)}')
    n_ok = sum(1 for s in services if s['ok'])
    return (f"{n_ok} of {len(services)} outside services answering; "
            + (f"{len(rep['problems'])} problem(s): " + '; '.join(p['text'][:80] for p in rep['problems'][:3]) if rep['problems'] else 'no problems'))
