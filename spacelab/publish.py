"""Share safely: a read-only public copy of Space Lab on GitHub Pages, plus posts to a public Discord channel.

What is shared: science results only (TESS signals under review or marked real, asteroid watch and precovery
sightings, Exoplanet Watch light curves, timing targets, the space brief) and the public name you choose.
What is never shared: your name/email/AAVSO code from Setup, webhooks, tokens, your telescope location and
observing plan, tasks, logs, file paths. Every published text file is scanned for those before upload; if any
is found, nothing is published.
"""
import base64
import hashlib
import json
import shutil
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from . import classifier, config, db, net

API = 'https://api.github.com'
SITE = config.DATA / 'public'
WEB = config.ROOT / 'web'
SITE_FILES = ('public.html', 'public.js', 'style.css', 'lcviewer.js', 'orbit3d.js', 'extras.js', 'extras.css', 'beyond.js', 'home.js', 'stargazing.js', 'asteroids.js', 'atlas.js', 'atlas-frame.html', 'universe.js')
TITLE = 'Space Lab: hunting planets and asteroids with open NASA data'


def _j(x):
    try:
        return json.loads(x) if isinstance(x, str) else x
    except Exception:
        return None


def _beyond_public(b):
    """Sky watch / Beyond astronomy data is all public and location-free already; drop anything unexpected."""
    keep = ('t', 'space_weather', 'fireballs', 'comets', 'gw', 'supernovae', 'launches', 'habitable', 'sky', 'earth', 'iss', 'quakes',
            'satellites', 'events', 'darksky', 'zip', 'live', 'wiki', 'missions', 'orbits')
    return {k: b[k] for k in keep if k in b}


def _topics(items):
    """Give each news story a topic (the same topics the radar uses) so visitors can filter."""
    from .radar import keyword_score
    return [dict(i, topic=keyword_score({'title': i.get('title') or '', 'summary': ' '.join(p.get('title') or '' for p in i.get('papers') or [])})['topic'])
            for i in items]


def _contrib(con):
    try:
        return db.contributions(con)
    except Exception:
        return {}


def site_url(cfg):
    """Address of the public site: your own domain if set in Setup, otherwise the free GitHub Pages address."""
    sh = cfg.get('share') or {}
    if sh.get('site_url'):
        return sh['site_url'].rstrip('/') + '/'
    repo = (sh.get('github_repo') or '').strip()
    if '/' in repo:
        owner, name = repo.split('/', 1)
        return f'https://{owner.lower()}.github.io/{name}/'
    return ''


def _radar_public(r):
    items = [{k: x.get(k) for k in ('id', 'title', 'url', 'sources', 'date', 'score', 'topic', 'why')} |
             {'summary': clean(x.get('summary')), 'lab': bool(x.get('lab'))}
             for x in (r.get('items') or []) if (x.get('score') or 0) >= 4][:80]
    dg = r.get('digest') or {}
    return {'t': r.get('t'), 'items': items, 'digest': {'t': dg.get('t'), 'text': clean(dg.get('text'))} if dg.get('text') else None,
            'n_sources': len(r.get('sources_ok') or []), 'failed': [str(x).split(':')[0] for x in (r.get('sources_failed') or [])]}


def clean(s):
    """Remove folder paths (they contain the Mac user name) from free text before it is published."""
    s = str(s or '')
    for p in sorted({str(config.ROOT), str(config.DATA), str(Path.home())}, key=len, reverse=True):
        s = s.replace(p, '…')
    return s


def public_name(cfg):
    return ((cfg.get('share') or {}).get('public_name') or '').strip()[:60] or 'a Space Lab volunteer'


# ------------------------------------------------------------------ the public data (whitelisted fields only)
def build_data(con, cfg):
    files = set()

    def f(rel):
        if rel and (config.DATA / rel).is_file():
            files.add(rel)
            return 'files/' + rel
        return None

    model = db.kv_get(con, 'clf')
    tess = []
    stars = {r['tic']: r for r in db.rows(con, 'SELECT tic, teff, rad, dist, tmag, ra, dec FROM tess_targets WHERE tic IN '
                                                "(SELECT tic FROM tess_cands WHERE status IN ('new','real','unsure'))")}
    for c in db.rows(con, "SELECT * FROM tess_cands WHERE status IN ('new','real','unsure') ORDER BY created DESC LIMIT 120"):
        ai, cen = _j(c.get('ai')) or {}, _j(c.get('centroid')) or {}
        st = stars.get(c['tic']) or {}
        tess.append({'id': c['id'], 'tic': c['tic'], 'kind': c['kind'], 'period': c['period'], 't0': c['t0'], 'duration': c['duration'],
                     'depth': c['depth'], 'snr': c['snr'], 'sde': c['sde'], 'ntr': c['ntr'], 'rp': c['rp'],
                     'flags': _j(c['flags']) or [], 'known': c['known'], 'explain': c['explain'],
                     'status': {'new': 'under review', 'real': 'looks like a planet', 'unsure': 'unsure'}[c['status']],
                     'plot': f(c['plot']), 'lc': f(c.get('lc')), 'created': c['created'],
                     'ai': {k: ai.get(k) for k in ('verdict', 'confidence', 'reasons')} if ai else None,
                     'model_score': classifier.score(model, c),
                     'fpp': _j(c.get('fpp')), 'gaia_ruwe': (_j(c.get('metrics')) or {}).get('gaia_ruwe'),
                     'star': {'teff': st.get('teff'), 'radius_sun': st.get('rad'), 'dist_pc': st.get('dist'), 'tmag': st.get('tmag'), 'ra': st.get('ra'), 'dec': st.get('dec')},
                     'centroid': ({k: cen.get(k) for k in ('verdict', 'offset_arcsec', 'signif', 'snr')} | {'png': f(cen.get('png'))}) if cen else None})
    stats = {r['status']: r['n'] for r in db.rows(con, 'SELECT status, COUNT(*) AS n FROM tess_targets GROUP BY status')}
    cst = {r['status']: r['n'] for r in db.rows(con, 'SELECT status, COUNT(*) AS n FROM tess_cands GROUP BY status')}
    funnel = {'stars': stats.get('done', 0), 'signals': sum(cst.values()), 'auto_rejected': cst.get('auto-rejected', 0),
              'not_planet': cst.get('fake', 0), 'review': cst.get('new', 0) + cst.get('unsure', 0), 'planet_like': cst.get('real', 0)}
    ck = db.kv_get(con, 'cheops') or {}
    crosscheck = {k: ck.get(k) for k in ('t', 'tested', 'recovered', 'depth_ok', 'median_ratio')} | {'items': [
        {k: i.get(k) for k in ('planet', 'star', 'date', 'vmag', 'depth_lit_ppm', 'depth_ppm', 'ratio', 'dur_lit_h', 'dur_h', 'offset_min',
                               'eph_sigma_min', 'snr', 'npts', 'scatter_ppm', 'recovered', 'depth_ok')} | {'plot': f(i.get('plot'))}
        for i in (ck.get('items') or []) if i.get('npts')][:40]} if ck.get('tested') else None
    from .jobs import JOBS
    runs = []
    for job, spec in JOBS.items():
        r = db.one(con, 'SELECT started, finished, status, summary FROM runs WHERE job=? ORDER BY started DESC LIMIT 1', (job,))
        runs.append({'job': job, 'label': spec[1], 'schedule': (cfg.get('schedule') or {}).get(job),
                     'started': r and r['started'], 'finished': r and r['finished'], 'status': r and r['status'],
                     'summary': clean((r and r['summary']) or '')[:400]})
    brief_kv = db.kv_get(con, 'brief') or {}
    try:
        from . import library
        lib = library.status()
    except Exception:
        lib = {}
    w = db.kv_get(con, 'neo_watch') or {}
    pcs = []
    for c in db.rows(con, "SELECT * FROM pc_cands WHERE status IN ('new','real','unsure') ORDER BY created DESC LIMIT 40"):
        plate = str(c['archive'] or '').startswith('SuperCOSMOS')
        sent = bool((_j(c.get('mpc')) or {}).get('sent'))
        keep = ('obsTime', 'filter', 'snr') if plate and not sent else ('obsTime', 'ra', 'dec', 'filter', 'snr')   # plate positions stay private until sent
        pcs.append({'id': c['id'], 'des': c['des'], 'night': c['night'], 'archive': c['archive'], 'offset': c['offset'],
                    'obs': [{k: o.get(k) for k in keep} for o in (_j(c['obs']) or [])],
                    'gif': f(c['gif']), 'png': f(c['png']), 'status': c['status']})
    from . import orbits

    def orbit(des):
        try:
            return orbits.elements(con, des, max_age=7 * 86400)
        except Exception:
            return db.kv_get(con, f'orbit:{des}')
    targets = [{k: t[k] for k in ('des', 'fullname', 'h', 'arc', 'ps', 'searched', 'nimg', 'ncand')} | {'orbit': orbit(t['des'])}
               for t in db.rows(con, "SELECT * FROM pc_targets WHERE searched IS NOT NULL ORDER BY searched DESC LIMIT 30")]
    for c in pcs:
        if not any(t['des'] == c['des'] for t in targets):
            targets.append({'des': c['des'], 'fullname': c['des'], 'h': None, 'arc': None, 'ps': None, 'searched': None,
                            'nimg': None, 'ncand': None, 'orbit': orbit(c['des'])})
    neocp = [{'name': r['name'], 'first': r['first'], **{k: (_j(r['data']) or {}).get(k) for k in ('neoScore', 'phaScore', 'H', 'nObs', 'arc', 'rating')}}
             for r in db.rows(con, 'SELECT * FROM neocp WHERE gone IS NULL ORDER BY first DESC LIMIT 25')]
    exo = []
    for d in db.rows(con, "SELECT * FROM exo WHERE status IN ('done','uploaded') ORDER BY created DESC LIMIT 40"):
        p, r = _j(d['planet']) or {}, _j(d['result']) or {}
        exo.append({'id': d['id'], 'planet': p.get('pl_name') if isinstance(p, dict) else None, 'date': d['obs_date'],
                    'telescope': 'own telescope' if d.get('source') == 'scope' else 'MicroObservatory',
                    'tmid': r.get('tmid'), 'tmid_err': r.get('tmid_err'), 'depth_pct': r.get('depth_pct'),
                    'scatter_pct': r.get('scatter_pct'), 'oc': r.get('oc'), 'plot': f(d['plot']), 'explain': d['explain'],
                    'uploaded': d['status'] == 'uploaded'})
    dates = [r['digest'] for r in db.rows(con, 'SELECT DISTINCT digest FROM arxiv WHERE digest IS NOT NULL ORDER BY digest DESC LIMIT 1')]
    papers = db.rows(con, 'SELECT id,title,authors,url,published,summary,why,project,rank FROM arxiv WHERE digest=? ORDER BY rank',
                     (dates[0],)) if dates else []
    eph = db.kv_get(con, 'ephem') or {}
    from . import daily as daily_mod
    dly = daily_mod.public(con)
    for part in (dly, dly.get('prev') or {}):
        if part.get('cand'):
            part['cand']['plot'] = f(part['cand'].get('plot'))
    db.sync_results(con)
    results = db.rows(con, 'SELECT id, kind, target, title, dest, link, status, note, created, updated FROM results WHERE public=1 ORDER BY created DESC LIMIT 200')
    for r in results:
        r['note'] = clean(r.get('note'))
    beyond = _beyond_public(db.kv_get(con, 'beyond') or {})
    uv = db.kv_get(con, 'universe') or {}
    if f(uv.get('bin')) and f(uv.get('txt')):          # the 3D universe (spacelab/universe.py): files on this site, details in data.json
        beyond['universe'] = {**{k: uv.get(k) for k in ('n', 'made', 'h0', 'env_counts', 'landmarks', 'famous', 'blueshift', 'blueshift_note',
                                                         'messier', 'eq_to_sg', 'max_dist', 'n_in_groups', 'method', 'near_note')},
                              'bin': f(uv['bin']), 'txt': f(uv['txt'])}
    for k in ('darksky', 'zip'):                     # map the saved files to their public address
        if isinstance(beyond.get(k), dict):
            img = f(beyond[k].get('path'))
            beyond[k] = {kk: vv for kk, vv in beyond[k].items() if kk != 'path'} | {'url': img} if img else None
    if beyond.get('live'):
        beyond['live'] = [{k: x.get(k) for k in ('key', 'caption', 'source', 'page', 't')} | {'img': f(x.get('path'))}
                          for x in beyond['live'] if f(x.get('path'))]
    if beyond.get('earth'):
        beyond['earth'] = [{k: e.get(k) for k in ('key', 'caption', 'source', 'date')} | {'img': f(e.get('path'))}
                           for e in beyond['earth'] if f(e.get('path'))]
    data = {'name': public_name(cfg), 'updated': time.time(), 'schema': SCHEMA_VERSION,
            'beyond': beyond, 'daily': dly, 'radar': _radar_public(db.kv_get(con, 'radar') or {}), 'results': results,
            'site': {'url': site_url(cfg), 'name': 'Space Lab', 'repo': ((cfg.get('share') or {}).get('github_repo') or '').strip()},
            'community': db.kv_get(con, 'community') or {},
            'contributions': _contrib(con),
            'today': {'brief': {'t': brief_kv.get('t'), 'text': clean(brief_kv.get('text') or ''), 'model': brief_kv.get('model')},
                      'jobs': runs, 'always_on': bool(cfg.get('always_on'))},
            'tess': {'stats': {k: stats.get(k, 0) for k in ('done', 'queued', 'nodata', 'error', 'known')}, 'cands': tess,
                     'engine': db.kv_get(con, 'tess_engine'), 'funnel': funnel, 'crosscheck': crosscheck,
                     'model': {k: (model or {}).get(k) for k in ('ready', 'n_user', 'n_auto', 'acc', 'top', 'need')}},
            'asteroids': {'text': (db.kv_get(con, 'neo_watch_text') or {}).get('text'), 'cad': (w.get('cad') or [])[:15],
                          'sentry': [{k: r.get(k) for k in ('des', 'fullname', 'ip', 'ps', 'ts_max', 'range', 'h', 'diameter')} for r in (w.get('sentry') or [])[:12]],
                          'neocp': neocp, 'cands': pcs, 'targets': targets, 'scout': (w.get('scout') or [])[:15],
                          'neocp_t': (db.kv_get(con, 'neocp_snapshot') or {}).get('t'),
                          'new_neos': [{k: (clean(v) if k == 'note' else v) for k, v in r.items()} for r in db.rows(con,
                                       "SELECT des, fullname, h, arc, searched, nimg, ncand, note FROM pc_targets WHERE origin='new-neo' ORDER BY rowid DESC LIMIT 20")]},
            'exowatch': {'results': exo, 'stale': (eph.get('stale') or [])[:30], 'ephem_t': eph.get('t')},
            'brief': {'date': dates[0] if dates else None, 'papers': papers,
                      'news': _topics(((db.kv_get(con, 'news') or {}).get('items') or [])[:30]),
                      'paper_news': _topics(((db.kv_get(con, 'paper_news') or {}).get('items') or [])[:40]),
                      'library': {k: lib.get(k) for k in ('papers', 'embedded', 'years', 'complete', 'topics')}}}
    return data, files


SCHEMA_VERSION = 5
# The public page (web/public.js) reads exactly these keys. Change them only together with public.js and bump SCHEMA_VERSION.
SCHEMA = {'name': str, 'updated': float, 'schema': int,
          'today': {'jobs': list, 'brief': dict}, 'beyond': dict, 'community': dict, 'contributions': dict,
          'daily': dict, 'radar': dict, 'results': list, 'site': dict,
          'tess': {'stats': dict, 'cands': list, 'model': dict},
          'asteroids': {'cad': list, 'sentry': list, 'neocp': list, 'cands': list, 'targets': list},
          'exowatch': {'results': list, 'stale': list},
          'brief': {'papers': list, 'news': list, 'paper_news': list, 'library': dict}}
CAND_KEYS = ('id', 'tic', 'period', 'depth', 'snr', 'flags', 'status')


def validate(data, schema=SCHEMA, path='data'):
    """Return a list of problems; an empty list means the public page can read this file."""
    errs = []
    for k, t in schema.items():
        if k not in data:
            errs.append(f'{path}.{k} is missing')
        elif isinstance(t, dict):
            if not isinstance(data[k], dict):
                errs.append(f'{path}.{k} should be an object')
            else:
                errs += validate(data[k], t, f'{path}.{k}')
        elif not isinstance(data[k], t) and not (t is float and isinstance(data[k], int)):
            errs.append(f'{path}.{k} should be {t.__name__}')
    if path == 'data' and not errs:
        for i, c in enumerate(data['tess']['cands']):
            miss = [k for k in CAND_KEYS if k not in c]
            if miss:
                errs.append(f"tess candidate {i} lacks {', '.join(miss)}")
                break
    return errs


def forbidden(cfg):
    """Strings that must never appear in anything published."""
    sh, sc = cfg.get('share') or {}, cfg.get('telescope') or {}
    bad = {'your email': cfg.get('contact_email'), 'your private Discord webhook': cfg.get('discord_webhook'),
           'the public Discord webhook': sh.get('discord_webhook'), 'your GitHub token': sh.get('github_token'),
           'your visitor-counter key': sh.get('goatcounter_token'),
           'your Space Lab folder path': str(config.ROOT), 'your Mac home folder': str(Path.home())}
    name = (cfg.get('observer_name') or '').strip()
    if len(name) >= 3 and name.lower() != (sh.get('public_name') or '').strip().lower():
        bad['your full name from Setup'] = name
    for k in ('lat', 'lon'):
        v = str(sc.get(k) or '').strip()
        if len(v.replace('-', '').replace('+', '')) >= 5:
            bad[f'your telescope {k}itude'] = v
    return {k: v for k, v in bad.items() if v and len(str(v)) >= 3}


def scan(folder, cfg):
    bad = forbidden(cfg)
    hits = []
    for p in Path(folder).rglob('*'):
        if '.git' in p.parts:
            continue
        if p.is_file() and p.suffix in ('.json', '.html', '.js', '.css', '.txt', '.md', '.xml', '.webmanifest', '.ics'):
            text = p.read_text(errors='ignore')
            for label, s in bad.items():
                if str(s) in text:
                    hits.append(f'{label} in {p.name}')
    return hits


def build_site(con, cfg):
    data, files = build_data(con, cfg)
    errs = validate(data)
    if errs:
        raise RuntimeError('stopped: the public data would break the page (' + '; '.join(errs[:5]) + '). Nothing was published.')
    SITE.mkdir(parents=True, exist_ok=True)
    for old in SITE.iterdir():                 # start clean, but keep .git so each upload only sends what changed
        if old.name != '.git':
            shutil.rmtree(old) if old.is_dir() else old.unlink()
    for name in SITE_FILES:
        src = WEB / name
        if src.is_file():
            shutil.copy(src, SITE / ('index.html' if name == 'public.html' else name))
    for rel in files:
        dst = SITE / 'files' / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(config.DATA / rel, dst)
    (SITE / 'data.json').write_text(json.dumps(data, default=str, separators=(',', ':')))
    (SITE / '.nojekyll').write_text('')
    from . import site
    site.build(SITE, data, cfg, SITE, TITLE)
    hits = scan(SITE, cfg)
    if hits:
        shutil.rmtree(SITE, ignore_errors=True)
        raise RuntimeError('stopped: private information would have been published (' + '; '.join(hits[:5]) + ')')
    return data


# ------------------------------------------------------------------ GitHub Pages
def gh(method, path, token, body=None):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode() if body is not None else None, method=method,
                                 headers={'Authorization': f'Bearer {token}', 'Accept': 'application/vnd.github+json',
                                          'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': net.UA,
                                          'Content-Type': 'application/json'})
    try:
        with urllib.request.urlopen(req, timeout=120, context=net.ssl_ctx()) as r:
            raw = r.read()
            return r.status, json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b'{}')
        except Exception:
            return e.code, {}


def blob_sha(data):
    return hashlib.sha1(b'blob %d\0' % len(data) + data).hexdigest()


PAGES_BRANCH = 'gh-pages'   # the shared page lives on its own branch; the code on "main" is never touched


def git_push(folder, repo, log):
    """Publish with the Mac's own GitHub login (git + Keychain), no token needed. Returns True on success."""
    import os
    import subprocess
    git = shutil.which('git')
    if not git:
        return False
    env = {**os.environ, 'GIT_TERMINAL_PROMPT': '0'}
    run = lambda *a: subprocess.run([git, *a], cwd=str(folder), env=env, capture_output=True, text=True, timeout=600)
    run('init', '-q')
    run('checkout', '-q', '-B', PAGES_BRANCH)
    run('config', 'user.name', 'Space Lab')
    run('config', 'user.email', 'spacelab@users.noreply.github.com')
    run('add', '-A')
    # one commit, replaced each time (amend), so the website branch never grows; keeping .git makes uploads incremental
    amend = ['--amend'] if run('rev-parse', '-q', '--verify', 'HEAD').returncode == 0 else []
    run('commit', '-q', *amend, '-m', f'Space Lab results {time.strftime("%Y-%m-%d %H:%M")}')
    url = repo if repo.startswith(('/', 'file:')) else f'https://github.com/{repo}.git'
    p = run('push', '-q', '--force', url, f'HEAD:{PAGES_BRANCH}')
    if p.returncode != 0:
        log(f'git push did not work ({(p.stderr or "").strip()[:200]}); trying the token instead')
        return False
    log(f'published to the {PAGES_BRANCH} branch with your Mac\'s GitHub login')
    return True


def push(folder, repo, token, log):
    """Upload through the GitHub API with a token, to the gh-pages branch."""
    if not (repo and token and '/' in repo):
        raise RuntimeError('could not publish: sign in to GitHub on this Mac (git) or add a token in Setup → Sharing')
    code, info = gh('GET', f'/repos/{repo}', token)
    if code != 200:
        raise RuntimeError(f'GitHub repository {repo} not reachable with this token (HTTP {code}: {info.get("message", "")})')
    entries = []
    for p in sorted(Path(folder).rglob('*')):
        if not p.is_file() or '.git' in p.relative_to(folder).parts:
            continue
        code, b = gh('POST', f'/repos/{repo}/git/blobs', token, {'content': base64.b64encode(p.read_bytes()).decode(), 'encoding': 'base64'})
        if code not in (200, 201):
            raise RuntimeError(f'upload of {p.name} failed (HTTP {code}: {b.get("message", "")})')
        entries.append({'path': p.relative_to(folder).as_posix(), 'mode': '100644', 'type': 'blob', 'sha': b['sha']})
    code, nt = gh('POST', f'/repos/{repo}/git/trees', token, {'tree': entries})
    if code not in (200, 201):
        raise RuntimeError(f'could not build the file tree (HTTP {code}: {nt.get("message", "")})')
    code, nc = gh('POST', f'/repos/{repo}/git/commits', token,
                  {'message': f'Space Lab results {time.strftime("%Y-%m-%d %H:%M")}', 'tree': nt['sha'], 'parents': []})
    if code not in (200, 201):
        raise RuntimeError(f'commit failed (HTTP {code}: {nc.get("message", "")})')
    code, _ = gh('PATCH', f'/repos/{repo}/git/refs/heads/{PAGES_BRANCH}', token, {'sha': nc['sha'], 'force': True})
    if code != 200:
        code, r = gh('POST', f'/repos/{repo}/git/refs', token, {'ref': f'refs/heads/{PAGES_BRANCH}', 'sha': nc['sha']})
        if code not in (200, 201):
            raise RuntimeError(f'could not update {PAGES_BRANCH} (HTTP {code}: {r.get("message", "")})')
    log(f'uploaded {len(entries)} files to {PAGES_BRANCH}')
    return True


def pages_url(repo, token=None):
    owner, name = repo.split('/', 1)
    url, note = f'https://{owner.lower()}.github.io/{name}/', None
    if token:
        code, pages = gh('GET', f'/repos/{repo}/pages', token)
        want = {'source': {'branch': PAGES_BRANCH, 'path': '/'}}
        if code == 404:
            code, pages = gh('POST', f'/repos/{repo}/pages', token, want)
        elif code == 200 and (pages.get('source') or {}).get('branch') != PAGES_BRANCH:
            code, _ = gh('PUT', f'/repos/{repo}/pages', token, want)
            pages = {'html_url': url} if code in (200, 204) else {}
        if code not in (200, 201, 204):
            note = None
        url = (pages or {}).get('html_url') or url
    return url, ('if the page is blank: repository Settings → Pages → Deploy from a branch → '
                 f'"{PAGES_BRANCH}" / (root)') if not token else note


# ------------------------------------------------------------------ Discord (public science channel)
def discord_post(url, content, image=None, embeds=None):
    payload = {'content': content[:1990], 'username': 'Space Lab', 'allowed_mentions': {'parse': []}}
    if embeds:
        payload['embeds'] = embeds
    boundary = uuid.uuid4().hex
    parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="payload_json"\r\nContent-Type: application/json\r\n\r\n'.encode()
             + json.dumps(payload).encode() + b'\r\n']
    if image and Path(image).is_file():
        p = Path(image)
        ctype = 'image/gif' if p.suffix.lower() == '.gif' else 'image/png'
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files[0]"; filename="{p.name}"\r\nContent-Type: {ctype}\r\n\r\n'.encode()
                     + p.read_bytes() + b'\r\n')
    body = b''.join(parts) + f'--{boundary}--\r\n'.encode()
    req = urllib.request.Request(url + ('&' if '?' in url else '?') + 'wait=true', data=body, method='POST',
                                 headers={'Content-Type': f'multipart/form-data; boundary={boundary}', 'User-Agent': net.UA})
    with urllib.request.urlopen(req, timeout=60, context=net.ssl_ctx()) as r:
        r.read()


def share_item(kind, oid, cfg=None):
    """Post one finding to the public science channel. Returns (ok, message)."""
    from . import notify
    cfg = cfg or config.load()
    sh = cfg.get('share') or {}
    url = sh.get('discord_webhook') or ''
    if not notify.valid(url):
        return False, 'add the public science channel webhook in Setup → Sharing first'
    if url == cfg.get('discord_webhook'):
        return False, 'the science channel webhook is the same as your private alerts webhook; use a separate public channel'
    con = db.connect()
    page = (db.kv_get(con, 'publish') or {}).get('url')
    who = public_name(cfg)
    if kind == 'tess':
        c = db.one(con, 'SELECT * FROM tess_cands WHERE id=?', (int(oid),))
        if not c:
            return False, 'not found'
        ai, cen = _j(c.get('ai')) or {}, _j(c.get('centroid')) or {}
        sc = classifier.score(db.kv_get(con, 'clf'), c)
        per = f'repeating dip every **{c["period"]:.4f} d**' if c['period'] else 'a **single dip** (period unknown)'
        lines = [f'🔭 **TESS signal on TIC {c["tic"]}**, found by {who}\'s Space Lab', f'{per}, depth {c["depth"] * 1e6:.0f} ppm, '
                 f'{c["duration"] * 24:.1f} h, SNR {c["snr"]:.1f}; ~{(c["rp"] or 0):.1f}× Earth if it is a planet around this star.']
        flags = _j(c['flags']) or []
        lines.append('Checks: ' + ('; '.join(flags) if flags else 'passed odd/even, half-orbit and size checks'))
        if cen:
            lines.append(f'Centroid test: {cen.get("verdict")} (offset {cen.get("offset_arcsec")}″)')
        if ai:
            lines.append(f'AI first look ({ai.get("model", "local model")}): {ai.get("verdict")} ({ai.get("confidence")}): {"; ".join(ai.get("reasons") or [])}')
        if sc is not None:
            lines.append(f'My own model: {sc:.0%} planet-like')
        lines.append(f'Status: {c["status"]}. ExoFOP: https://exofop.ipac.caltech.edu/tess/target.php?id={c["tic"]}')
        lines.append('Thoughts welcome in the thread: real planet, eclipsing binary, or noise?')
        img = config.DATA / c['plot']
        table, key = 'tess_cands', c['id']
    elif kind == 'pc':
        c = db.one(con, 'SELECT * FROM pc_cands WHERE id=?', (int(oid),))
        if not c:
            return False, 'not found'
        lines = [f'☄️ **Possible precovery of asteroid {c["des"]}** in {c["archive"]} images from {c["night"]}, found by {who}\'s Space Lab',
                 f'A moving dot {c["offset"]:.0f}″ from where JPL\'s orbit predicts, seen in two images the same night and missing from the deep stack.',
                 f'Status: {c["status"]}. Does the blink look like the same moving object to you?']
        img = config.DATA / (c['gif'] or c['png'] or '')
        table, key = 'pc_cands', c['id']
    elif kind == 'exo':
        d = db.one(con, 'SELECT * FROM exo WHERE id=?', (int(oid),))
        if not d:
            return False, 'not found'
        p, r = _j(d['planet']) or {}, _j(d['result']) or {}
        oc = r.get('oc') or {}
        lines = [f'🪐 **Transit of {p.get("pl_name", "?")}** on {d["obs_date"]}, measured by {who} for NASA Exoplanet Watch',
                 f'Mid-transit time ±{(r.get("tmid_err") or 0) * 1440:.1f} min; {oc.get("oc_min", "?")} min vs. the prediction '
                 f'(prediction uncertain by ±{oc.get("pred_err_min", "?")} min). Depth {r.get("depth_pct")}%.']
        img = config.DATA / (d['plot'] or '')
        table, key = 'exo', d['id']
    else:
        return False, 'unknown kind'
    if page:
        lines.append(f'All results: {page}')
    try:
        discord_post(url, '\n'.join(lines), img if img.is_file() else None)
    except Exception as e:
        return False, f'{type(e).__name__}: {e}'
    con.execute(f'UPDATE {table} SET shared=? WHERE id=?', (time.time(), key))
    return True, 'posted'


# ------------------------------------------------------------------ job
def run(ctx):
    con, cfg, log = ctx.con, ctx.cfg, ctx.log
    sh = cfg.get('share') or {}
    try:                                   # make sure today's candidate of the day exists before the page is built
        from . import daily
        from .llm import LLM
        daily.ensure(con, cfg, log, LLM(cfg, log))
    except Exception as e:
        log(f'  candidate of the day skipped: {e}')
    data = build_site(con, cfg)
    n = len(data['tess']['cands']) + len(data['asteroids']['cands']) + len(data['exowatch']['results'])
    log(f'public copy built: {n} results, scanned for private information: clean')
    out = [f'public copy built ({n} results)']
    url = None
    repo = (sh.get('github_repo') or '').strip()
    if repo and sh.get('publish', True):
        if not git_push(SITE, repo, log):
            push(SITE, repo, sh.get('github_token'), log)
        url, note = pages_url(repo, sh.get('github_token'))
        db.kv_set(con, 'publish', {'t': time.time(), 'url': url, 'note': note})
        out.append(f'published to {url}')
    else:
        out.append('not uploaded: add your GitHub repository in Setup → Sharing')
    from . import notify
    if sh.get('post_summary') and notify.valid(sh.get('discord_webhook')) and sh.get('discord_webhook') != cfg.get('discord_webhook'):
        last = db.kv_get(con, 'publish_summary_counts') or {}
        st = data['tess']['stats']
        msg = (f'🌌 **Space Lab nightly summary** ({public_name(cfg)})\n'
               f'TESS: {st.get("done", 0)} stars checked so far, {sum(1 for c in data["tess"]["cands"] if c["status"] == "under review")} signals under review.\n'
               f'Asteroids: {len(data["asteroids"]["neocp"])} objects on the NEO confirmation page; '
               f'{len(data["asteroids"]["cands"])} possible precovery sightings.\n'
               f'Exoplanet Watch: {len(data["exowatch"]["results"])} transits measured.' + (f'\n{url}' if url else ''))
        counts = {'done': st.get('done', 0), 'c': len(data['tess']['cands']), 'p': len(data['asteroids']['cands']), 'e': len(data['exowatch']['results'])}
        counts['day'] = time.strftime('%Y-%m-%d')
        if counts['day'] != last.get('day') and time.localtime().tm_hour >= 7:     # at most one summary a day, in the morning
            try:
                discord_post(sh['discord_webhook'], msg)
                db.kv_set(con, 'publish_summary_counts', counts)
                out.append('summary posted to the science channel')
            except Exception as e:
                out.append(f'Discord summary failed: {e}')
    return '; '.join(out)
