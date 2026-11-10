"""Space Lab dashboard + scheduler.  python -m spacelab.server  (http://localhost:8790)
Serves the web page, answers its API calls, runs the nightly jobs one at a time, and watches the
Exoplanet Watch inbox for new datasets."""
import json
import mimetypes
import os
import queue
import re
import shutil
import signal
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import aavso, classifier, config, db, mpccheck, net, notify
from .jobs import JOBS

WEB = config.ROOT / 'web'
JOBQ = queue.Queue()
QUEUED = set()
ORDER = []                  # the queued jobs in the order they will run
STATE = {'running': None, 'proc': None, 'started': time.time()}
LOCK = threading.Lock()


def log(*a):
    print(time.strftime('%Y-%m-%d %H:%M:%S'), *a, flush=True)


# ------------------------------------------------------------------ jobs
def enqueue(job, trigger='schedule'):
    with LOCK:
        if job in QUEUED or STATE['running'] == job:
            return False
        QUEUED.add(job)
        ORDER.append((job, trigger))
    JOBQ.put((job, trigger))
    log(f'queued {job} ({trigger})')
    return True


def worker():
    while True:
        job, trigger = JOBQ.get()
        with LOCK:
            QUEUED.discard(job)
            ORDER[:] = [x for x in ORDER if x[0] != job]
            STATE['running'] = job
            STATE['run_started'], STATE['run_trigger'] = time.time(), trigger
        con = db.connect()
        cfg = config.load()
        jd = config.LOGS / job
        jd.mkdir(parents=True, exist_ok=True)
        logf = jd / f'{time.strftime("%Y%m%d-%H%M%S")}.log'
        run_id = con.execute('INSERT INTO runs(job,started,status,summary,log,trigger) VALUES(?,?,?,?,?,?)',
                             (job, time.time(), 'running', 'starting…', str(logf), trigger)).lastrowid
        budget = (cfg.get(job) or {}).get('time_budget_min', 60) if isinstance(cfg.get(job), dict) else 60
        timeout = (budget + 45) * 60 if job != 'exowatch' else (cfg['exowatch']['timeout_min'] + 30) * 60
        env = config.job_env()
        env['SPACELAB_RUN_ID'] = str(run_id)
        env['SPACELAB_TRIGGER'] = str(trigger)
        status = 'error'
        try:
            with open(logf, 'w') as fh:
                p = subprocess.Popen([sys.executable, '-m', 'spacelab.jobs', job], cwd=str(config.ROOT), env=env,
                                     stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                STATE['proc'] = p
                try:
                    code = p.wait(timeout=timeout)
                    status = 'ok' if code == 0 else 'error'
                except subprocess.TimeoutExpired:
                    p.kill()
                    status = 'timeout'
        except Exception as e:
            log(f'{job} could not start: {e}')
        finally:
            STATE['proc'] = None
            con.execute('UPDATE runs SET finished=?, status=? WHERE id=?', (time.time(), status, run_id))
            try:
                alert(con, job, status, run_id)
            except Exception as e:
                log(f'discord alert failed: {e}')
            with LOCK:
                STATE['running'] = None
            prune_logs(jd)
            log(f'{job} finished: {status}')


def alert(con, job, status, run_id):
    cfg = config.load()
    if not notify.valid(cfg.get('discord_webhook')):
        return
    want = cfg.get('discord_alerts') or {}
    run = db.one(con, 'SELECT started, summary FROM runs WHERE id=?', (run_id,))
    lines = []
    if status != 'ok' and job != 'selftest' and want.get('errors', True):
        prev = db.rows(con, 'SELECT status FROM runs WHERE job=? AND id<? ORDER BY id DESC LIMIT 1', (job, run_id))
        if prev and prev[0]['status'] not in ('ok', 'running'):      # one failure is often a busy server; two in a row is worth a ping
            lines.append(f"⚠️ **{JOBS[job][1]}** failed twice in a row: {run['summary']}")
    if want.get('tasks', True):
        new = db.rows(con, "SELECT title FROM tasks WHERE status='open' AND created >= ?", (run['started'],))
        if new:
            lines.append(f"🔭 **{len(new)} new for you** from {JOBS[job][1]}:")
            lines += [f"• {t['title']}" for t in new[:10]]
    if job == 'brief' and status == 'ok' and want.get('brief', True):
        b = db.kv_get(con, 'brief') or {}
        if b.get('text'):
            lines.append('☕ **Morning brief**\n' + b['text'])
    if lines:
        lines.append('Open Meridian → 🔭 Space Lab tab (or http://localhost:8790)')
        ok, msg = notify.send('\n'.join(lines), cfg['discord_webhook'])
        log(f'discord: {msg}')


def prune_logs(d, keep=30):
    files = sorted(d.glob('*.log'))
    for f in files[:-keep]:
        try:
            f.unlink()
        except OSError:
            pass


FILLERS = ('tess', 'library')     # what full-time mode runs when nothing else is due


def library_busy():
    try:
        from . import library
        s = library.status()
        return not s['complete'] or s['embedded'] < s['papers']
    except Exception:
        return True


def keep_awake(on):
    """macOS: caffeinate stops idle and system sleep (on power) while Space Lab runs. The screen can still turn off."""
    p = STATE.get('caffeinate')
    alive = p is not None and p.poll() is None
    if on and not alive and sys.platform == 'darwin' and shutil.which('caffeinate'):
        STATE['caffeinate'] = subprocess.Popen(['caffeinate', '-i', '-m', '-s', '-w', str(os.getpid())],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log('full-time mode: keeping the Mac awake')
    elif not on and alive:
        p.terminate()
        STATE['caffeinate'] = None
        log('full-time mode off: the Mac may sleep normally')


def ai_when_idle(cfg, con):
    """When nobody has touched the Mac for `idle_minutes`, let the local AI catch up (TESS first looks, news radar)."""
    if not cfg.get('ai_when_idle', True):
        return False
    from .idle import user_idle_seconds
    s = user_idle_seconds()
    STATE['user_idle'] = s
    if s is None or s < float(cfg.get('idle_minutes') or 5) * 60:
        return False
    with LOCK:
        free = not QUEUED and not STATE['running']
    if not free:
        return False
    last = con.execute("SELECT MAX(started) FROM runs WHERE job='ai'").fetchone()[0]
    if last and time.time() - last < 20 * 60:
        return False
    enqueue('ai', 'Mac idle')
    return True


def fill_idle(cfg, con):
    with LOCK:
        idle = not QUEUED and not STATE['running']
    if not idle:
        return
    i = STATE.get('filler', 0)
    for k in range(len(FILLERS)):
        job = FILLERS[(i + k) % len(FILLERS)]
        if job == 'library' and not library_busy():
            continue
        last = con.execute('SELECT MAX(started) FROM runs WHERE job=?', (job,)).fetchone()[0]
        if last and time.time() - last < 600:     # a run that ends at once (no internet, nothing to do) won't loop
            continue
        STATE['filler'] = (i + k + 1) % len(FILLERS)
        enqueue(job, 'full-time mode')
        return


def scheduler():
    last_inbox = 0
    while True:
        try:
            cfg = config.load()
            con = db.connect()
            now = datetime.now()
            for job, hhmm in (cfg.get('schedule') or {}).items():
                if job not in JOBS:
                    continue
                if str(hhmm).startswith('every'):
                    try:
                        hours = float(str(hhmm).split()[1].rstrip('h'))
                    except (IndexError, ValueError):
                        continue
                    r = con.execute('SELECT MAX(started) FROM runs WHERE job=?', (job,)).fetchone()[0]
                    if r is None or time.time() - r > hours * 3600:
                        enqueue(job, 'schedule')
                    continue
                if str(hhmm) in ('', 'off'):
                    continue
                try:
                    h, m = map(int, str(hhmm).split(':'))
                except ValueError:
                    continue
                at = now.replace(hour=h, minute=m, second=0, microsecond=0)
                if at > now:
                    at -= timedelta(days=1)
                if now - at > timedelta(hours=3):   # missed by more than 3 h (e.g. Mac was off): wait for tomorrow
                    continue
                r = con.execute('SELECT MAX(started) FROM runs WHERE job=?', (job,)).fetchone()[0]
                if r is None or r < at.timestamp():
                    enqueue(job, 'schedule')
            if time.time() - last_inbox > 90:
                last_inbox = time.time()
                tele = config.INBOX / 'telescope'
                tele.mkdir(parents=True, exist_ok=True)
                if any(p for d in (config.EXO_INBOX, tele) for p in d.iterdir()
                       if not p.name.startswith('.') and p.name not in ('processed', 'failed')):
                    enqueue('exowatch', 'new dataset')
            keep_awake(bool(cfg.get('always_on')))
            if not ai_when_idle(cfg, con) and cfg.get('always_on'):
                fill_idle(cfg, con)
        except Exception as e:
            log(f'scheduler: {e}')
        time.sleep(30)


# ------------------------------------------------------------------ API
def j(x):
    return json.loads(x) if isinstance(x, str) and x[:1] in '[{' else x


def typical_seconds(con, job, trigger=None):
    """How long this job usually takes: the middle value of its last finished runs, of the same kind of start when there
    are enough (a background run is much shorter than the nightly one). Returns (seconds or None, runs counted)."""
    rows = db.rows(con, "SELECT finished - started AS d, trigger FROM runs WHERE job=? AND status='ok' AND finished IS NOT NULL "
                        'ORDER BY id DESC LIMIT 12', (job,))
    same = [r['d'] for r in rows if r['trigger'] == trigger]
    ds = sorted(same if len(same) >= 3 else [r['d'] for r in rows])
    return (ds[len(ds) // 2] if ds else None), len(ds)


def eta(con, cfg, now=None):
    """Countdown for Meridian: the running job (when it started, how long it usually takes, the longest it may run) and the
    queued jobs in order with their usual length."""
    now = now or time.time()
    out = {'running': None, 'queue': [], 'total': None}
    left = 0.0
    known = True
    job = STATE.get('running')
    if job:
        typ, n = typical_seconds(con, job, STATE.get('run_trigger'))
        budget = (cfg.get(job) or {}).get('time_budget_min') if isinstance(cfg.get(job), dict) else None
        if budget and STATE.get('run_trigger') == 'full-time mode':
            budget = min(float(budget), 50.0)
        started = STATE.get('run_started') or now
        out['running'] = {'job': job, 'started': started, 'typical': typ, 'n': n, 'budget': budget * 60 if budget else None}
        if typ is None:
            known = False
        else:
            left += max(0.0, typ - (now - started))
    with LOCK:
        order = list(ORDER)
    for q, trig in order:
        typ, n = typical_seconds(con, q, trig)
        out['queue'].append({'job': q, 'typical': typ, 'n': n})
        if typ is None:
            known = False
        else:
            left += typ
    if (job or order) and known:
        out['total'] = left
    return out


def state():
    con = db.connect()
    cfg = config.load()
    tasks = db.rows(con, "SELECT * FROM tasks WHERE status='open' ORDER BY priority, created DESC")
    for t in tasks:
        t['ref'] = j(t['ref'])
    runs = {}
    for job in JOBS:
        r = db.one(con, 'SELECT * FROM runs WHERE job=? ORDER BY started DESC LIMIT 1', (job,))
        runs[job] = r
    done_today = con.execute("SELECT COUNT(*) FROM tasks WHERE status='done' AND updated > ?", (time.time() - 86400,)).fetchone()[0]
    return {'now': time.time(), 'brief': db.kv_get(con, 'brief'), 'tasks': tasks, 'runs': runs, 'done_today': done_today,
            'running': STATE['running'], 'queue': [x[0] for x in ORDER], 'eta': eta(con, cfg), 'schedule': cfg['schedule'], 'always_on': bool(cfg.get('always_on')),
            'jobs': {k: v[1] for k, v in JOBS.items()}, 'inbox': str(config.EXO_INBOX),
            'profile': {k: cfg.get(k) for k in ('observer_name', 'aavso_code', 'contact_email', 'sarc_verified')},
            'selftest': db.kv_get(con, 'selftest'), 'disk': db.kv_get(con, 'disk'), 'contributions': db.contributions(con),
            'visitors': db.kv_get(con, 'visitors'), 'user_idle': STATE.get('user_idle'), 'ai_when_idle': cfg.get('ai_when_idle', True)}


def api_arxiv(q):
    con = db.connect()
    dates = [r['digest'] for r in db.rows(con, 'SELECT DISTINCT digest FROM arxiv WHERE digest IS NOT NULL ORDER BY digest DESC LIMIT 30')]
    d = (q.get('date') or [dates[0] if dates else ''])[0]
    papers = db.rows(con, 'SELECT id,title,authors,abstract,cats,published,url,score,rank,summary,why,project,is_read '
                          'FROM arxiv WHERE digest=? ORDER BY rank', (d,))
    try:
        from . import library
        lib = library.status()
    except Exception as e:
        lib = {'error': str(e)}
    return {'dates': dates, 'date': d, 'papers': papers, 'news': db.kv_get(con, 'news'), 'paper_news': db.kv_get(con, 'paper_news'), 'library': lib,
            'tracked': db.kv_get(con, 'arxiv_tracked')}


SEVERE = ('eclipsing binary', 'odd and even', 'secondary eclipse', 'off target', 'nearby star', 'known')


def ctoi_draft(c):
    """Community TOI (CTOI) draft for ExoFOP, for candidates you marked as planet-like and that passed the checks.
    Returns None when a check argues against it."""
    if c.get('kind') != 'periodic' or not c.get('period'):
        return None
    if any(any(s in str(f).lower() for s in SEVERE) for f in c.get('flags') or []):
        return None
    fpp = c.get('fpp') or {}
    if fpp.get('nfpp') is not None and fpp['nfpp'] > 0.1:
        return None
    epoch = (c['t0'] or 0) + 2457000.0
    notes = 'Found in TESS 2-min/FFI light curves with a BLS search; passed odd/even, secondary-eclipse, centroid'
    notes += (f", TRICERATOPS FPP={fpp.get('fpp'):.3f}" if fpp.get('fpp') is not None else '') + ' checks. Citizen-science vetting (Space Lab).'
    return {'tic': c['tic'], 'period_days': round(c['period'], 6), 'epoch_bjd_tdb': round(epoch, 5),
            'depth_ppm': round((c['depth'] or 0) * 1e6), 'duration_hours': round((c['duration'] or 0) * 24, 3),
            'notes': notes, 'url': 'https://exofop.ipac.caltech.edu/tess/upload_ctoi.php'}


def api_var():
    """Variable-star candidates from the TESS light curves, for Meridian (spacelab/variables.py)."""
    from . import variables
    con = db.connect()
    variables.ensure(con)
    out = []
    for r in db.rows(con, "SELECT v.*, t.ra, t.dec, t.tmag FROM tess_var v LEFT JOIN tess_targets t ON t.tic = v.tic "
                          "WHERE v.status IN ('candidate','real','unsure','submitted') ORDER BY v.status='candidate' DESC, v.snr DESC LIMIT 200"):
        r = dict(r)
        for k in ('info', 'checks', 'ai', 'known'):
            r[k] = j(r.get(k))
        r.pop('npz', None)
        out.append(r)
    return {'summary': variables.summary(con), 'cands': out, 'injection': db.kv_get(con, 'injection')}


def api_tess():
    con = db.connect()
    stats = {r['status']: r['n'] for r in db.rows(con, 'SELECT status, COUNT(*) AS n FROM tess_targets GROUP BY status')}
    cands = db.rows(con, "SELECT * FROM tess_cands ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'real' THEN 1 WHEN 'unsure' THEN 2 "
                         "ELSE 3 END, created DESC LIMIT 150")
    model = db.kv_get(con, 'clf')
    for c in cands:
        c['model_score'] = classifier.score(model, c)
        c['flags'] = j(c['flags']) or []
        c['metrics'] = j(c['metrics']) or {}
        c['ai'] = j(c.get('ai'))
        c['centroid'] = j(c.get('centroid'))
        c['fpp'] = j(c.get('fpp'))
        c['ctoi'] = ctoi_draft(c) if c['status'] == 'real' else None
    return {'stats': stats, 'cands': cands, 'engine': db.kv_get(con, 'tess_engine'),
            'model': {k: (model or {}).get(k) for k in ('ready', 'n_user', 'n_auto', 'acc', 'top', 'need')},
            'shared': bool(((config.load().get('share') or {}).get('discord_webhook')))}


def api_exo():
    con = db.connect()
    ds = db.rows(con, 'SELECT * FROM exo ORDER BY created DESC LIMIT 60')
    for d in ds:
        d['planet'] = j(d['planet'])
        d['stars'] = j(d['stars'])
        d['result'] = j(d['result'])
    cfg = config.load()
    sc = cfg.get('telescope') or {}
    return {'datasets': ds, 'inbox': str(config.EXO_INBOX), 'aavso_code': cfg.get('aavso_code'),
            'telescope_inbox': str(config.INBOX / 'telescope'), 'telescope': {'model': sc.get('model'), 'located': bool(sc.get('lat') and sc.get('lon'))},
            'ephem': db.kv_get(con, 'ephem'), 'plan': db.kv_get(con, 'ephem_plan'), 'aavso_submit': aavso.SUBMIT_URL,
            'shared': bool(((cfg.get('share') or {}).get('discord_webhook'))),
            'checkout': 'https://science.nasa.gov/citizen-science/exoplanet-watch/how-to-contribute/data-checkout/'}


def api_neo():
    con = db.connect()
    w = db.kv_get(con, 'neo_watch') or {}
    targets = db.rows(con, "SELECT * FROM pc_targets WHERE status IN ('ready','searched') ORDER BY ps DESC LIMIT 40")
    for t in targets:
        t['other'] = j(t['other'])
    cands = db.rows(con, "SELECT * FROM pc_cands ORDER BY CASE status WHEN 'new' THEN 0 WHEN 'real' THEN 1 ELSE 2 END, created DESC LIMIT 60")
    for c in cands:
        c['obs'] = j(c['obs'])
        c['mpc'] = {k: {kk: vv for kk, vv in v.items() if kk != 'hash'} for k, v in (j(c.get('mpc')) or {}).items()}
    neocp = []
    for r in db.rows(con, 'SELECT * FROM neocp WHERE gone IS NULL ORDER BY first DESC LIMIT 40'):
        d = j(r['data']) or {}
        neocp.append({'name': r['name'], 'first': r['first'], **{k: d.get(k) for k in ('neoScore', 'phaScore', 'H', 'nObs', 'arc', 'rating', 'Vmag', 'unc')}})
    fresh = db.rows(con, "SELECT des, fullname, h, arc, searched, nimg, ncand, note FROM pc_targets WHERE origin='new-neo' ORDER BY rowid DESC LIMIT 20")
    cfg = config.load()
    return {'watch': {'t': w.get('t'), 'cad': w.get('cad') or [], 'sentry': (w.get('sentry') or [])[:15], 'scout': w.get('scout') or []},
            'text': db.kv_get(con, 'neo_watch_text'), 'targets': targets, 'cands': cands, 'neocp': neocp, 'new_neos': fresh,
            'plates': db.kv_get(con, 'plates'), 'email_set': bool((cfg.get('contact_email') or '').strip()),
            'neocp_t': (db.kv_get(con, 'neocp_snapshot') or {}).get('t'),
            'sarc_verified': cfg.get('sarc_verified'), 'shared': bool(((cfg.get('share') or {}).get('discord_webhook')))}


def api_orbit(des):
    from . import orbits
    return orbits.elements(db.connect(), des)


def api_publish():
    con = db.connect()
    cfg = config.load()
    sh = cfg.get('share') or {}
    return {'last': db.kv_get(con, 'publish'), 'run': db.one(con, "SELECT started, status, summary FROM runs WHERE job='publish' ORDER BY started DESC LIMIT 1"),
            'repo': sh.get('github_repo'), 'token_set': bool(sh.get('github_token')), 'channel_set': bool(sh.get('discord_webhook'))}


def tail(path, n=200):
    try:
        lines = Path(path).read_text(errors='ignore').splitlines()
        return '\n'.join(lines[-n:])
    except Exception as e:
        return f'(no log: {e})'


def api_logs(q):
    job = (q.get('job') or ['server'])[0]
    if job == 'server':
        return {'job': job, 'text': tail(config.LOGS / 'server.log')}
    d = config.LOGS / job
    files = sorted(d.glob('*.log')) if d.exists() else []
    return {'job': job, 'file': str(files[-1]) if files else None, 'text': tail(files[-1]) if files else '(no runs yet)'}


SETTABLE = {'always_on', 'ai_when_idle', 'idle_minutes', 'observer_name', 'aavso_code', 'contact_email', 'sarc_verified', 'discord_webhook', 'discord_alerts', 'schedule', 'arxiv',
            'tess', 'precovery', 'llm', 'telescope', 'share', 'neocp', 'targets'}
MASK = '••••••••'


def masked_settings():
    cfg = config.load()
    sh = cfg.get('share') or {}
    for k in ('github_token', 'goatcounter_token'):
        if sh.get(k):
            sh[k] = MASK + sh[k][-4:]
    return cfg


CLOUD_PROMPT = ('This is a photo of the sky taken by an amateur astronomer. Name the main cloud type (one of: clear sky, cirrus, '
                'cirrostratus, cirrocumulus, altostratus, altocumulus, stratus, stratocumulus, nimbostratus, cumulus, cumulonimbus, fog/haze, '
                'not a sky photo). Reply as JSON: {"type": "...", "confidence": 0-1, "why": "one short sentence on the visual clues", '
                '"observing": "one short sentence on what this means for stargazing tonight"}')


def cloud_check(b64):
    """Meridian only: the local vision model names the clouds in a sky photo. The photo is not saved."""
    import base64
    if not b64 or len(b64) > 12_000_000:
        return 400, {'error': 'send one photo under about 8 MB'}
    try:
        base64.b64decode(b64[:4000] + '=' * (-len(b64[:4000]) % 4), validate=True)
    except Exception:
        return 400, {'error': 'not an image'}
    from .llm import LLM
    r = LLM(config.load(), log).chat(CLOUD_PROMPT, kind='vision', json_mode=True, max_tokens=200, images=[b64], temperature=0.1)
    if not isinstance(r, dict) or not r.get('type'):
        return 503, {'error': 'the local vision model did not answer (is Ollama running with a vision model?)'}
    return 200, {k: r.get(k) for k in ('type', 'confidence', 'why', 'observing')}


def post_action(path, body):
    con = db.connect()
    parts = path.strip('/').split('/')   # api, kind, id
    kind = parts[1] if len(parts) > 1 else ''
    oid = parts[2] if len(parts) > 2 else ''
    now = time.time()
    if kind == 'results':
        if body.get('action') == 'delete' and oid.isdigit():
            con.execute('DELETE FROM results WHERE id=?', (int(oid),))
            return 200, {'ok': True}
        if body.get('action') == 'save':
            r = dict(body)
            if oid.isdigit() and int(oid):
                r['id'] = int(oid)
            return 200, {'ok': True, 'id': db.save_result(con, r)}
        return 400, {'error': 'bad action'}
    if kind == 'cloud-check':
        return cloud_check(body.get('image') or '')
    if kind == 'var' and oid.isdigit():                  # your verdict on a variable-star candidate (nothing is sent anywhere)
        st = {'real': 'real', 'fake': 'fake', 'unsure': 'unsure', 'submitted': 'submitted', 'reopen': 'candidate'}.get(body.get('v'))
        if not st:
            return 400, {'error': 'bad verdict'}
        from . import variables
        variables.ensure(con)
        con.execute('UPDATE tess_var SET status=?, updated=? WHERE tic=?', (st, now, int(oid)))
        if st in ('real', 'fake', 'submitted'):
            db.close_task(con, f'var:{oid}')
        return 200, {'ok': True}
    if kind == 'task':
        st = {'done': 'done', 'dismiss': 'dismissed', 'reopen': 'open'}.get(body.get('action'))
        if not st:
            return 400, {'error': 'bad action'}
        con.execute('UPDATE tasks SET status=?, updated=? WHERE id=?', (st, now, int(oid)))
        return 200, {'ok': True}
    if kind == 'run':
        if oid not in JOBS:
            return 404, {'error': 'unknown job'}
        return 200, {'queued': enqueue(oid, 'you')}
    if kind == 'stop':
        p = STATE.get('proc')
        if p:
            p.terminate()
        return 200, {'ok': True}
    if kind == 'arxiv':
        con.execute('UPDATE arxiv SET is_read=? WHERE id=?', (1 if body.get('read') else 0, oid))
        return 200, {'ok': True}
    if kind == 'tess':
        v = body.get('verdict')
        if v not in ('real', 'fake', 'unsure', 'new'):
            return 400, {'error': 'bad verdict'}
        c = db.one(con, 'SELECT * FROM tess_cands WHERE id=?', (int(oid),))
        if not c:
            return 404, {'error': 'not found'}
        con.execute('UPDATE tess_cands SET status=? WHERE id=?', (v, c['id']))
        db.close_task(con, f'tess:cand:{c["id"]}', 'done' if v != 'new' else 'open')
        try:
            classifier.train(con)
        except Exception as e:
            log(f'classifier: {e}')
        if v == 'real':
            db.add_task(con, f'tess:share:{c["id"]}', 'tess', 'share', f'Check TIC {c["tic"]} on ExoFOP before sharing',
                        'Make sure nobody has reported it yet, then post it on the Planet Hunters TESS forum or tell me and '
                        'I will help you write it up.', {'tab': 'tess', 'id': c['id'],
                                                         'url': f'https://exofop.ipac.caltech.edu/tess/target.php?id={c["tic"]}'}, 2)
        return 200, {'ok': True}
    if kind == 'pc' and oid.isdigit() and len(parts) > 3 and parts[3] == 'submit':
        from . import mpcsubmit
        c = db.one(con, 'SELECT * FROM pc_cands WHERE id=?', (int(oid),))
        if not c:
            return 404, {'error': 'not found'}
        mode = 'test' if body.get('test') else 'real'
        if mode == 'real' and body.get('confirm') is not True:
            return 400, {'error': 'sending needs your confirmation'}
        try:
            code, out, state = mpcsubmit.submit(c, config.load(), mode)
        except Exception as e:
            return 502, {'ok': False, 'error': f'the MPC did not answer: {type(e).__name__}: {str(e)[:200]}'}
        con.execute('UPDATE pc_cands SET mpc=? WHERE id=?', (json.dumps(state), c['id']))
        if mode == 'real' and out.get('ok'):
            db.close_task(con, f'pc:report:{c["id"]}')
            db.save_result(con, {'kind': 'mpc', 'key': f'mpc:{c["id"]}', 'target': c['des'], 'public': False, 'status': 'submitted',
                                 'title': f"Precovery position of {c['des']} ({c.get('archive') or ''})",
                                 'note': 'Sent from Meridian. MPC submission ID ' + (out.get('id') or '(see the acknowledgement email)')})
        return code, out
    if kind == 'pc':
        if oid.isdigit() and body.get('verdict'):
            v = body['verdict']
            if v not in ('real', 'fake', 'unsure', 'new'):
                return 400, {'error': 'bad verdict'}
            c = db.one(con, 'SELECT * FROM pc_cands WHERE id=?', (int(oid),))
            if not c:
                return 404, {'error': 'not found'}
            con.execute('UPDATE pc_cands SET status=? WHERE id=?', (v, c['id']))
            db.close_task(con, f'pc:cand:{c["id"]}')
            if v == 'real':
                db.add_task(con, f'pc:report:{c["id"]}', 'asteroids', 'report', f'Prepare your precovery report for {c["des"]}',
                            'Download the draft ADES file from the Asteroids tab. Before your first archival report the MPC '
                            'committee (SARC) must verify you; send it only after that.', {'tab': 'asteroids', 'id': c['id']}, 1)
                cfg = config.load()
                if not cfg.get('sarc_verified'):
                    db.add_task(con, 'pc:sarc', 'asteroids', 'setup',
                                'One-time: ask the Minor Planet Center to verify you for archival (precovery) reports',
                                'Contact the MPC Singletons and Archival submission Committee (SARC) before your first report.',
                                {'tab': 'asteroids', 'url': 'https://buttondown.com/MPC_newsletter/archive/newsletter-september-2025/'}, 1,
                                reopen=True)
            return 200, {'ok': True}
        return 400, {'error': 'bad request'}
    if kind == 'exo':
        ds = db.one(con, 'SELECT * FROM exo WHERE id=?', (int(oid),))
        if not ds:
            return 404, {'error': 'not found'}
        a = body.get('action')
        if a == 'stars':
            tgt, comps = body.get('target'), body.get('comps') or []
            if not (isinstance(tgt, list) and len(tgt) == 2 and len(comps) >= 1):
                return 400, {'error': 'need one target and at least one comparison star'}
            stars = {'target': [int(tgt[0]), int(tgt[1])], 'comps': [[int(x), int(y)] for x, y in comps][:8], 'auto': False}
            old = j(ds['stars']) or {}
            if old.get('scale'):
                stars['scale'] = old['scale']
            con.execute("UPDATE exo SET stars=?, status='ready', error=NULL, updated=? WHERE id=?", (json.dumps(stars), now, ds['id']))
            db.close_task(con, f'exo:stars:{ds["id"]}')
            enqueue('exowatch', 'you')
        elif a == 'planet':
            name = str(body.get('name') or '').strip()[:60]
            if not name:
                return 400, {'error': 'empty name'}
            con.execute("UPDATE exo SET planet=?, status='new', error=NULL, updated=? WHERE id=?", (name, now, ds['id']))
            db.close_task(con, f'exo:planet:{ds["id"]}')
            enqueue('exowatch', 'you')
        elif a == 'uploaded':
            con.execute("UPDATE exo SET status='uploaded', updated=? WHERE id=?", (now, ds['id']))
            db.close_task(con, f'exo:upload:{ds["id"]}')
        elif a == 'rerun':
            con.execute("UPDATE exo SET status='new', stars=NULL, error=NULL, updated=? WHERE id=?", (now, ds['id']))
            enqueue('exowatch', 'you')
        elif a == 'fix-aavso':
            changed = aavso.fix(ds['aavso_file'], config.load())
            return 200, {'ok': True, 'changed': changed}
        elif a == 'discard':
            con.execute("UPDATE exo SET status='discarded', updated=? WHERE id=?", (now, ds['id']))
            for k in ('upload', 'stars', 'planet'):
                db.close_task(con, f'exo:{k}:{ds["id"]}', 'dismissed')
        else:
            return 400, {'error': 'bad action'}
        return 200, {'ok': True}
    if kind == 'notify-test':
        url = body.get('url') or config.load().get('discord_webhook')
        ok, msg = notify.send('✅ Space Lab is connected. Alerts about new things to check will appear here.', url)
        return (200 if ok else 400), ({'ok': True} if ok else {'error': msg})
    if kind == 'library':
        from . import library
        from .llm import LLM
        llm = LLM(config.load(), log)
        q = str(body.get('q') or '').strip()[:300]
        if not q:
            return 400, {'error': 'empty search'}
        rows = library.search(q, llm, body.get('y0'), body.get('y1'), body.get('cat'), body.get('topic'), k=int(body.get('k') or 40))
        if body.get('review'):
            rv = library.review(q, rows[:int(body.get('n_review') or 15)], llm)
            for r in rows:
                if r['id'] in rv:
                    r['ai'] = rv[r['id']]
            rows.sort(key=lambda r: -((r.get('ai') or {}).get('score', -1) * 10 + r['score']))
        return 200, {'results': rows}
    if kind == 'ask':
        from . import ask
        return 200, ask.answer(body.get('q'))
    if kind == 'share':
        from . import publish
        ok, msg = publish.share_item(body.get('kind'), body.get('id'))
        return (200 if ok else 400), ({'ok': True} if ok else {'error': msg})
    if kind == 'settings':
        changes = {k: v for k, v in body.items() if k in SETTABLE}
        if isinstance(changes.get('share'), dict):
            for k in ('github_token', 'goatcounter_token'):
                if str(changes['share'].get(k, '')).startswith(MASK):
                    changes['share'].pop(k)
            gc = str(changes['share'].get('goatcounter') or '').strip().lower()
            gc = re.sub(r'^https?://', '', gc).split('.goatcounter.com')[0].strip('/')
            if gc and not re.match(r'^[a-z0-9][a-z0-9-]{1,49}$', gc):
                return 400, {'error': 'The visitor counter code is the first part of your GoatCounter address, e.g. "spacelab" for spacelab.goatcounter.com'}
            if 'goatcounter' in changes['share']:
                changes['share']['goatcounter'] = gc
            su = str(changes['share'].get('site_url') or '').strip()
            if su and not re.match(r'^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/-]*)?$', su):
                return 400, {'error': 'The website address should look like https://www.example.com/'}
        sh = changes.get('share') or {}
        if sh.get('discord_webhook') and not notify.valid(sh['discord_webhook']):
            return 400, {'error': 'The science channel webhook should start with https://discord.com/api/webhooks/'}
        if changes.get('discord_webhook') and not notify.valid(changes['discord_webhook']):
            return 400, {'error': 'That does not look like a Discord webhook URL (it starts with https://discord.com/api/webhooks/)'}
        cfg = config.save_user(changes)
        if cfg.get('aavso_code'):
            db.close_task(con, 'exo:aavso')
        if cfg.get('sarc_verified'):
            db.close_task(con, 'pc:sarc')
        if cfg.get('observer_name') and cfg.get('contact_email'):
            db.close_task(con, 'setup:profile')
        return 200, {'ok': True}
    return 404, {'error': 'unknown'}


class H(BaseHTTPRequestHandler):
    server_version = 'SpaceLab/1.0'

    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype='application/json', extra=None):
        data = body if isinstance(body, bytes) else (json.dumps(body, default=str) if ctype == 'application/json' else body).encode()
        self.send_response(code)
        self.send_header('Content-Type', ctype + ('; charset=utf-8' if ctype.startswith(('text', 'application/json')) else ''))
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def file(self, path, download=False):
        path = Path(path)
        if not path.is_file():
            return self.send(404, {'error': 'not found'})
        ctype = mimetypes.guess_type(path.name)[0] or 'application/octet-stream'
        extra = {'Content-Disposition': f'attachment; filename="{path.name}"'} if download else None
        self.send(200, path.read_bytes(), ctype, extra)

    @staticmethod
    def inside(base, rel):
        p = (base / rel).resolve()
        return p if str(p).startswith(str(base.resolve()) + os.sep) else None

    def do_GET(self):
        u = urllib.parse.urlsplit(self.path)
        q = urllib.parse.parse_qs(u.query)
        p = u.path
        try:
            if p in ('/', '/index.html'):
                return self.file(WEB / 'index.html')
            if p.startswith('/web/'):
                f = self.inside(WEB, p[5:])
                return self.file(f) if f else self.send(403, {'error': 'no'})
            if p.startswith('/files/'):
                f = self.inside(config.DATA, urllib.parse.unquote(p[7:]))
                return self.file(f) if f else self.send(403, {'error': 'no'})
            if p == '/download':
                f = self.inside(config.DATA, os.path.relpath(Path(q.get('path', [''])[0]).resolve(), config.DATA.resolve()))
                return self.file(f, download=True) if f else self.send(403, {'error': 'no'})
            if p == '/api/ping':
                return self.send(200, {'app': 'spacelab'})
            if p == '/api/state':
                return self.send(200, state())
            if p == '/api/arxiv':
                return self.send(200, api_arxiv(q))
            if p == '/api/var':
                return self.send(200, api_var())
            if p == '/api/health':
                from . import health
                return self.send(200, health.report(db.connect(), config.load()))
            if p == '/api/tess':
                return self.send(200, api_tess())
            if p == '/api/exo':
                return self.send(200, api_exo())
            if p == '/api/neo':
                return self.send(200, api_neo())
            if p == '/api/public-data':      # the same data the public page gets, so Meridian shows the same extras
                from . import publish
                data, _ = publish.build_data(db.connect(), config.load())
                return self.send(200, data)
            if p == '/api/results':
                con = db.connect()
                db.sync_results(con)
                return self.send(200, {'items': db.rows(con, 'SELECT * FROM results ORDER BY created DESC'),
                                       'kinds': db.RESULT_KINDS, 'status': db.RESULT_STATUS})
            if p == '/api/export/eclipsing-binaries.csv':     # rejected signals that look like two stars, for AAVSO VSX
                from . import export
                txt, _n = export.eb_csv(db.connect())
                return self.send(200, txt, 'text/csv', {'Content-Disposition': 'attachment; filename="eclipsing-binary-candidates.csv"'})
            if p == '/api/logs':
                return self.send(200, api_logs(q))
            if p == '/api/settings':
                return self.send(200, masked_settings())
            if p == '/api/orbit':
                return self.send(200, api_orbit((q.get('des') or [''])[0][:40]))
            if p == '/api/publish':
                return self.send(200, api_publish())
            if p.startswith('/api/pc/') and p.endswith('/check'):
                con = db.connect()
                c = db.one(con, 'SELECT * FROM pc_cands WHERE id=?', (int(p.split('/')[3]),))
                if not c:
                    return self.send(404, {'error': 'not found'})
                c['obs'] = j(c['obs'])
                return self.send(200, {'checks': mpccheck.check(c['ades'], c, config.load())})
            if p.startswith('/api/exo/') and p.endswith('/aavso'):
                d = db.one(db.connect(), 'SELECT * FROM exo WHERE id=?', (int(p.split('/')[3]),))
                if not d:
                    return self.send(404, {'error': 'not found'})
                return self.send(200, {'checks': aavso.check(d['aavso_file'], config.load(), d.get('source') or 'mobs'),
                                       'steps': aavso.steps(d), 'submit': aavso.SUBMIT_URL, 'preview': d.get('preview')})
            if p.startswith('/api/pc/') and p.endswith('/ades'):
                c = db.one(db.connect(), 'SELECT des, night, ades FROM pc_cands WHERE id=?', (int(p.split('/')[3]),))
                if not c:
                    return self.send(404, {'error': 'not found'})
                name = f'{c["des"].replace(" ", "_")}_{c["night"]}_precovery_DRAFT.psv'
                return self.send(200, c['ades'], 'text/plain', {'Content-Disposition': f'attachment; filename="{name}"'})
            return self.send(404, {'error': 'not found'})
        except Exception as e:
            log(f'GET {p}: {type(e).__name__}: {e}')
            return self.send(500, {'error': str(e)})

    def do_POST(self):
        # Simple cross-site protection: browsers can't add this header to requests from other websites.
        if self.headers.get('X-SpaceLab') != '1':
            return self.send(403, {'error': 'missing header'})
        try:
            n = int(self.headers.get('Content-Length') or 0)
            if n > 16_000_000:
                return self.send(413, {'error': 'too large'})
            body = json.loads(self.rfile.read(n) or b'{}') if n else {}
            code, out = post_action(urllib.parse.urlsplit(self.path).path, body)
            return self.send(code, out)
        except Exception as e:
            log(f'POST {self.path}: {type(e).__name__}: {e}')
            return self.send(500, {'error': str(e)})


def code_watcher():
    """Restart this server when Space Lab's code is updated, so changes apply without closing Meridian."""
    src = config.ROOT / 'spacelab'
    snap = lambda: {p.name: p.stat().st_mtime for p in src.glob('*.py')}
    first = snap()
    while True:
        time.sleep(15)
        try:
            if snap() != first and STATE['running'] is None and JOBQ.empty():
                log('code updated: restarting Space Lab')
                time.sleep(2)
                os.execv(sys.executable, [sys.executable, '-m', 'spacelab.server', '--no-browser'])
        except Exception as e:
            log(f'code watcher: {e}')


def already_running(port):
    try:
        with urllib.request.urlopen(f'http://127.0.0.1:{port}/api/ping', timeout=2) as r:
            return json.loads(r.read()).get('app') == 'spacelab'
    except Exception:
        return False


def main():
    config.ensure_dirs()
    cfg = config.load()
    port = int(cfg['port'])
    url = f'http://localhost:{port}'
    no_browser = '--no-browser' in sys.argv
    if already_running(port):
        print(f'Space Lab is already running at {url}')
        if not no_browser:
            webbrowser.open(url)
        return
    con = db.connect()
    con.execute("UPDATE runs SET status='interrupted', finished=? WHERE status='running'", (time.time(),))
    if not cfg.get('observer_name') or not cfg.get('contact_email'):
        db.add_task(con, 'setup:profile', 'setup', 'setup', 'Add your name and email in Setup',
                    'Used only for credit on the result files you upload yourself (AAVSO, Minor Planet Center).', {'tab': 'setup'}, 3)
    httpd = ThreadingHTTPServer(('127.0.0.1', port), H)
    threading.Thread(target=worker, daemon=True).start()
    threading.Thread(target=scheduler, daemon=True).start()
    threading.Thread(target=code_watcher, daemon=True).start()
    if con.execute('SELECT COUNT(*) FROM runs').fetchone()[0] == 0:
        for job in ('selftest', 'arxiv', 'precovery', 'brief'):
            enqueue(job, 'first start')
    log(f'Space Lab running at {url}  (Ctrl+C to stop)')
    def stop(*_):
        p = STATE.get('proc')
        if p:
            p.terminate()
        os._exit(0)
    signal.signal(signal.SIGTERM, stop)
    if not no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
