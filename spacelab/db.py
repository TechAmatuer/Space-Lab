"""SQLite storage shared by the dashboard server and the job processes (WAL mode, short transactions)."""
import json
import sqlite3
import time

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT, t REAL);
CREATE TABLE IF NOT EXISTS tasks (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE, project TEXT, kind TEXT, title TEXT, detail TEXT,
  ref TEXT, priority INTEGER DEFAULT 2, status TEXT DEFAULT 'open', created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY, job TEXT, started REAL, finished REAL, status TEXT, summary TEXT, log TEXT, trigger TEXT);
CREATE TABLE IF NOT EXISTS arxiv (
  id TEXT PRIMARY KEY, title TEXT, authors TEXT, abstract TEXT, cats TEXT, published TEXT, url TEXT,
  score REAL, digest TEXT, rank INTEGER, summary TEXT, why TEXT, project TEXT, is_read INTEGER DEFAULT 0, added REAL);
CREATE TABLE IF NOT EXISTS tess_targets (
  tic INTEGER PRIMARY KEY, ra REAL, dec REAL, tmag REAL, teff REAL, rad REAL, mass REAL, dist REAL,
  status TEXT DEFAULT 'queued', note TEXT, checked REAL, sectors TEXT);
CREATE TABLE IF NOT EXISTS tess_cands (
  id INTEGER PRIMARY KEY, tic INTEGER, kind TEXT, period REAL, t0 REAL, duration REAL, depth REAL,
  snr REAL, sde REAL, ntr INTEGER, rp REAL, metrics TEXT, flags TEXT, known TEXT, plot TEXT,
  explain TEXT, status TEXT DEFAULT 'new', created REAL);
CREATE TABLE IF NOT EXISTS exo (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE, folder TEXT, planet TEXT, obs_date TEXT, nimg INTEGER,
  status TEXT, stars TEXT, preview TEXT, result TEXT, aavso_file TEXT, plot TEXT, explain TEXT,
  error TEXT, created REAL, updated REAL);
CREATE TABLE IF NOT EXISTS pc_targets (
  des TEXT PRIMARY KEY, fullname TEXT, h REAL, arc REAL, cc TEXT, ip REAL, ps REAL, last_obs TEXT,
  status TEXT, note TEXT, searched REAL, nimg INTEGER DEFAULT 0, ncand INTEGER DEFAULT 0, other TEXT);
CREATE TABLE IF NOT EXISTS neocp (
  name TEXT PRIMARY KEY, first REAL, last REAL, gone REAL, data TEXT, alerted INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS arxiv_emb (id TEXT PRIMARY KEY, v BLOB);
CREATE TABLE IF NOT EXISTS pc_cands (
  id INTEGER PRIMARY KEY, des TEXT, night TEXT, archive TEXT, obs TEXT, offset REAL, mdist REAL,
  score REAL, gif TEXT, png TEXT, ades TEXT, status TEXT DEFAULT 'new', created REAL);
"""


def connect():
    config.ensure_dirs()
    con = sqlite3.connect(str(config.DB_PATH), timeout=30, isolation_level=None)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA busy_timeout=30000')
    con.executescript(SCHEMA)
    migrate(con)
    return con


COLUMNS = {'tess_cands': {'ai': 'TEXT', 'centroid': 'TEXT', 'lc': 'TEXT', 'shared': 'REAL', 'fpp': 'TEXT'},
           'exo': {'source': 'TEXT', 'shared': 'REAL'},
           'pc_cands': {'shared': 'REAL'},
           'pc_targets': {'origin': 'TEXT'}}


def migrate(con):
    for table, cols in COLUMNS.items():
        have = {r[1] for r in con.execute(f'PRAGMA table_info({table})')}
        for c, typ in cols.items():
            if c not in have:
                try:
                    con.execute(f'ALTER TABLE {table} ADD COLUMN {c} {typ}')
                except sqlite3.OperationalError:
                    pass


def rows(con, sql, args=()):
    return [dict(r) for r in con.execute(sql, args).fetchall()]


def one(con, sql, args=()):
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else None


def kv_get(con, k, default=None):
    r = con.execute('SELECT v FROM kv WHERE k=?', (k,)).fetchone()
    if not r:
        return default
    try:
        return json.loads(r[0])
    except Exception:
        return default


def kv_set(con, k, v):
    con.execute('INSERT INTO kv(k,v,t) VALUES(?,?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v, t=excluded.t',
                (k, json.dumps(v), time.time()))


def kv_time(con, k):
    r = con.execute('SELECT t FROM kv WHERE k=?', (k,)).fetchone()
    return r[0] if r else None


def add_task(con, key, project, kind, title, detail='', ref=None, priority=2, reopen=False):
    """Create a task once per key. Done/dismissed tasks are not recreated unless reopen=True."""
    now = time.time()
    old = con.execute('SELECT id, status FROM tasks WHERE key=?', (key,)).fetchone()
    if old:
        if old['status'] == 'open' or reopen:
            con.execute('UPDATE tasks SET title=?, detail=?, ref=?, priority=?, status=?, updated=? WHERE id=?',
                        (title, detail, json.dumps(ref or {}), priority, 'open', now, old['id']))
        return old['id']
    cur = con.execute('INSERT INTO tasks(key,project,kind,title,detail,ref,priority,status,created,updated) '
                      'VALUES(?,?,?,?,?,?,?,?,?,?)',
                      (key, project, kind, title, detail, json.dumps(ref or {}), priority, 'open', now, now))
    return cur.lastrowid


def close_task(con, key, status='done'):
    con.execute("UPDATE tasks SET status=?, updated=? WHERE key=? AND status='open'", (status, time.time(), key))


def close_tasks_like(con, pattern, status='done', except_key=None):
    con.execute("UPDATE tasks SET status=?, updated=? WHERE key LIKE ? AND status='open' AND key != ?",
                (status, time.time(), pattern, except_key or ''))


def contributions(con):
    """What you have done: reviews, uploads, reports. Counts only; nothing personal."""
    one = lambda sql, a=(): con.execute(sql, a).fetchone()[0]
    return {'tess_reviewed': one("SELECT COUNT(*) FROM tess_cands WHERE status IN ('real','fake','unsure')"),
            'tess_planet_like': one("SELECT COUNT(*) FROM tess_cands WHERE status='real'"),
            'asteroid_sightings_checked': one("SELECT COUNT(*) FROM pc_cands WHERE status IN ('real','fake','unsure')"),
            'exowatch_reduced': one("SELECT COUNT(*) FROM exo WHERE status='done'"),
            'uploads_done': one("SELECT COUNT(*) FROM tasks WHERE status='done' AND (key LIKE 'exo:upload:%' OR key LIKE 'pc:%' OR key LIKE '%submit%')"),
            'stars_searched': one("SELECT COUNT(*) FROM tess_targets WHERE status='done'")}
