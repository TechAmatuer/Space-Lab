"""Paper library: 50 years of astronomy papers, searchable by meaning.

* 1992 onward: every astro-ph paper on arXiv (OAI-PMH bulk metadata: titles, authors, abstracts; no PDFs).
* 1976-1991 (before arXiv): journal papers with abstracts from OpenAlex (free, open scholarly index).
  Both are resumed in chunks across runs.
* Index: SQLite full-text search + bge-m3 embeddings (float16) so a question finds papers by meaning.
* Topics: each paper gets 1-2 topic tags by comparing its embedding with short topic descriptions (instant).
* AI review: for a search, Gemma reads the top abstracts, scores how well each answers your question (0-10)
  and writes one plain-English sentence. Reviews are cached.
Disk: roughly 1 GB of text and index + 1 GB of embeddings for ~480,000 papers. Nothing leaves your Mac except the downloads.
"""
import hashlib
import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import numpy as np

from . import config, net

DB = config.DATA / 'library.db'
EMB = config.DATA / 'library_emb.f16'
DIM = 1024
OAI = ('https://oaipmh.arxiv.org/oai', 'https://export.arxiv.org/oai2')
NS = {'o': 'http://www.openarchives.org/OAI/2.0/', 'a': 'http://arxiv.org/OAI/arXiv/'}
TOPICS = {
    'exoplanets': 'exoplanets, transiting planets, planet detection, exoplanet atmospheres, radial velocity',
    'asteroids & comets': 'asteroids, near-Earth objects, comets, minor planets, planetary defense, orbits of small bodies',
    'solar system': 'planets of the solar system, moons, Mars, Jupiter, planetary surfaces and atmospheres',
    'the Sun': 'the Sun, solar flares, solar wind, space weather, heliophysics',
    'stars': 'stellar physics, stellar evolution, binary stars, variable stars, stellar populations',
    'star & planet formation': 'star formation, protoplanetary disks, molecular clouds, planet formation',
    'compact objects': 'black holes, neutron stars, pulsars, white dwarfs, X-ray binaries, accretion',
    'gravitational waves': 'gravitational waves, LIGO, compact binary mergers',
    'transients': 'supernovae, gamma-ray bursts, fast radio bursts, tidal disruption events, transients',
    'galaxies': 'galaxies, galaxy evolution, the Milky Way, active galactic nuclei, quasars',
    'cosmology': 'cosmology, dark energy, dark matter, cosmic microwave background, large-scale structure',
    'instruments & methods': 'telescopes, instrumentation, surveys, data analysis methods, machine learning in astronomy',
    'astrobiology': 'astrobiology, habitability, biosignatures, origin of life, SETI',
}


def connect():
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    con.executescript('''
    PRAGMA journal_mode=WAL;
    CREATE TABLE IF NOT EXISTS papers (id TEXT PRIMARY KEY, title TEXT, authors TEXT, abstract TEXT, cats TEXT,
        year INTEGER, created TEXT, doi TEXT, erow INTEGER, tags TEXT);
    CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(title, abstract, content='papers', content_rowid='rowid');
    CREATE TRIGGER IF NOT EXISTS papers_ai AFTER INSERT ON papers BEGIN
        INSERT INTO fts(rowid, title, abstract) VALUES (new.rowid, new.title, new.abstract); END;
    CREATE TABLE IF NOT EXISTS reviews (id TEXT, q TEXT, score REAL, summary TEXT, t REAL, PRIMARY KEY(id, q));
    CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
    CREATE INDEX IF NOT EXISTS papers_year ON papers(year);
    CREATE INDEX IF NOT EXISTS papers_erow ON papers(erow);
    ''')
    return con


_READ = object()


def kv(con, k, v=_READ, default=None):
    if v is _READ:
        r = con.execute('SELECT v FROM kv WHERE k=?', (k,)).fetchone()
        return json.loads(r[0]) if r else default
    con.execute('INSERT OR REPLACE INTO kv(k, v) VALUES(?,?)', (k, json.dumps(v)))
    con.commit()


# ------------------------------------------------------------------ harvest (arXiv OAI-PMH)
def _get(params, log, deadline=None):
    """One OAI-PMH page. Gives up quickly when arXiv is slow, so the rest of the run (other sources,
    and building the search index on this Mac) still gets its time."""
    deadline = deadline or time.time() + 600
    last = None
    for base in OAI:
        for attempt in range(3):
            if time.time() > deadline - 30:
                break
            url = base + '?' + urllib.parse.urlencode(params)
            req = urllib.request.Request(url, headers={'User-Agent': net.UA})
            try:
                with urllib.request.urlopen(req, timeout=min(300, max(30, deadline - time.time())), context=net.ssl_ctx()) as r:
                    return r.read()
            except urllib.error.HTTPError as e:
                last = e
                if e.code in (429, 503):      # arXiv asks clients to wait
                    wait = int(e.headers.get('Retry-After') or 20) if str(e.headers.get('Retry-After') or '').isdigit() else 20
                    time.sleep(min(max(wait, 5), 60, max(0, deadline - time.time() - 30)))
                    continue
                break
            except Exception as e:      # a timeout: this server is struggling, try the other one
                last = e
                break
        log(f'  OAI endpoint {base} failed: {last}')
    raise RuntimeError(f'arXiv OAI-PMH unavailable: {last}')



def parse_records(xml_bytes, min_year):
    root = ET.fromstring(xml_bytes)
    err = root.find('o:error', NS)
    if err is not None and err.get('code') != 'noRecordsMatch':
        raise RuntimeError(f'OAI error {err.get("code")}: {err.text}')
    out = []
    for rec in root.iterfind('.//o:record', NS):
        m = rec.find('.//a:arXiv', NS)
        if m is None:
            continue
        g = lambda tag: (m.findtext(f'a:{tag}', default='', namespaces=NS) or '').strip()
        created = g('created')
        year = int(created[:4]) if created[:4].isdigit() else 0
        if year < min_year:
            continue
        authors = []
        for a in m.iterfind('a:authors/a:author', NS):
            kn = a.findtext('a:keyname', default='', namespaces=NS)
            fn = a.findtext('a:forenames', default='', namespaces=NS)
            authors.append(f'{fn} {kn}'.strip())
        out.append({'id': g('id'), 'title': re.sub(r'\s+', ' ', g('title')), 'abstract': re.sub(r'\s+', ' ', g('abstract')),
                    'authors': ', '.join(authors[:12]) + (' et al.' if len(authors) > 12 else ''), 'cats': g('categories'),
                    'year': year, 'created': created, 'doi': g('doi')})
    tok = root.find('.//o:resumptionToken', NS)
    token = tok.text.strip() if tok is not None and tok.text else None
    total = tok.get('completeListSize') if tok is not None else None
    return out, token, total


def harvest(con, log, deadline, min_year=2006):
    token = kv(con, 'token')
    since = kv(con, 'from')       # None = the whole archive (arXiv rejects dates before its 2007 datestamps)
    added = pages = 0
    while time.time() < deadline:
        params = {'verb': 'ListRecords', 'resumptionToken': token} if token else \
                 {'verb': 'ListRecords', 'metadataPrefix': 'arXiv', 'set': 'physics:astro-ph', **({'from': since} if since else {})}
        try:
            recs, token, total = parse_records(_get(params, log, deadline), min_year)
        except RuntimeError as e:
            if 'badResumptionToken' in str(e) and token:   # tokens expire; start the pass again (duplicates are skipped)
                log('  arXiv resume point expired; restarting the pass')
                token = None
                kv(con, 'token', None)
                continue
            raise
        for r in recs:
            cur = con.execute('INSERT OR IGNORE INTO papers(id,title,authors,abstract,cats,year,created,doi) VALUES(?,?,?,?,?,?,?,?)',
                              (r['id'], r['title'], r['authors'], r['abstract'], r['cats'], r['year'], r['created'], r['doi']))
            added += cur.rowcount
        con.commit()
        pages += 1
        if total:
            kv(con, 'total', int(total))
        if not token:
            kv(con, 'token', None)
            kv(con, 'from', time.strftime('%Y-%m-%d', time.gmtime(time.time() - 3 * 86400)))
            kv(con, 'complete', True)
            log(f'  harvest up to date ({pages} pages this run)')
            break
        kv(con, 'token', token)
        time.sleep(5)    # be polite to arXiv
    return added, pages


# ------------------------------------------------------------------ harvest (arXiv search API, month by month)
# arXiv's bulk OAI service is often overloaded; its regular search API (the one the daily digest uses) is reliable.
# It is used to fill everything from where the library stops up to today, one month at a time.
ARXIV_API = 'https://export.arxiv.org/api/query'
ATOM = {'w': 'http://www.w3.org/2005/Atom', 'x': 'http://arxiv.org/schemas/atom'}
ASTRO_CATS = '(' + ' OR '.join('cat:' + c for c in ('astro-ph', 'astro-ph.CO', 'astro-ph.EP', 'astro-ph.GA', 'astro-ph.HE',
                                                     'astro-ph.IM', 'astro-ph.SR')) + ')'


def parse_api(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.iterfind('w:entry', ATOM):
        raw = (e.findtext('w:id', default='', namespaces=ATOM) or '').strip()
        m = re.search(r'arxiv\.org/abs/(.+?)(v\d+)?$', raw)
        if not m:
            continue
        created = (e.findtext('w:published', default='', namespaces=ATOM) or '')[:10]
        authors = [a.findtext('w:name', default='', namespaces=ATOM).strip() for a in e.iterfind('w:author', ATOM)]
        out.append({'id': m.group(1), 'title': re.sub(r'\s+', ' ', e.findtext('w:title', default='', namespaces=ATOM)).strip(),
                    'abstract': re.sub(r'\s+', ' ', e.findtext('w:summary', default='', namespaces=ATOM)).strip(),
                    'authors': ', '.join(authors[:12]) + (' et al.' if len(authors) > 12 else ''),
                    'cats': ' '.join(c.get('term', '') for c in e.iterfind('w:category', ATOM)),
                    'year': int(created[:4]) if created[:4].isdigit() else 0, 'created': created,
                    'doi': (e.findtext('x:doi', default='', namespaces=ATOM) or '').strip()})
    return out


def _next_month(ym):
    y, m = int(ym[:4]), int(ym[5:7])
    return f'{y + (m == 12)}-{1 if m == 12 else m + 1:02d}'


def harvest_api(con, log, deadline, min_year=1992, per_page=1000):
    month = kv(con, 'api_month')
    if not month:
        last = con.execute("SELECT MAX(created) FROM papers WHERE id NOT GLOB 'W[0-9]*'").fetchone()[0]
        month = (last or f'{min_year}-01')[:7]
    now = time.strftime('%Y-%m', time.gmtime())
    added = pages = 0
    while time.time() < deadline and month <= now:
        y, m = int(month[:4]), int(month[5:7])
        days = [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
        q = f'{ASTRO_CATS} AND submittedDate:[{y}{m:02d}010000 TO {y}{m:02d}{days}2359]'
        start, done_month = int(kv(con, 'api_start') or 0), False
        while time.time() < deadline:
            try:
                xml = net.fetch_text(ARXIV_API, params={'search_query': q, 'start': start, 'max_results': per_page,
                                                        'sortBy': 'submittedDate', 'sortOrder': 'ascending'}, timeout=120, min_gap=3.0)
            except Exception as e:
                log(f'  arXiv search API paused: {e}')
                return added, pages
            recs = parse_api(xml.encode('utf-8') if isinstance(xml, str) else xml)
            for r in recs:
                cur = con.execute('INSERT OR IGNORE INTO papers(id,title,authors,abstract,cats,year,created,doi) VALUES(?,?,?,?,?,?,?,?)',
                                  (r['id'], r['title'], r['authors'], r['abstract'], r['cats'], r['year'], r['created'], r['doi']))
                added += cur.rowcount
            con.commit()
            pages += 1
            start += per_page
            kv(con, 'api_start', start)
            if len(recs) < per_page or start >= 30000:
                done_month = True
                break
        if not done_month:
            break
        kv(con, 'api_start', 0)
        if month == now:            # caught up: keep re-reading the current month for new papers
            break
        month = _next_month(month)
        kv(con, 'api_month', month)
    if pages:
        log(f'  arXiv search API: {added} papers added, now at {month}')
    return added, pages


# ------------------------------------------------------------------ harvest (OpenAlex, before arXiv existed)
OPENALEX = 'https://api.openalex.org/works'
OA_SUBFIELD = '3103'     # OpenAlex subfield "Astronomy and Astrophysics"


def oa_abstract(inv):
    """OpenAlex stores abstracts as {word: [positions]}; rebuild the text."""
    if not inv:
        return ''
    pos = [(p, w) for w, ps in inv.items() for p in ps]
    return ' '.join(w for _, w in sorted(pos))


def oa_records(js):
    out = []
    for w in js.get('results') or []:
        title = re.sub(r'\s+', ' ', w.get('display_name') or w.get('title') or '').strip()
        abstract = re.sub(r'\s+', ' ', oa_abstract(w.get('abstract_inverted_index'))).strip()
        if not title or len(abstract) < 80:
            continue
        names = [(a.get('author') or {}).get('display_name') or a.get('raw_author_name') or '' for a in w.get('authorships') or []]
        names = [n for n in names if n]
        sub_ = ((w.get('primary_topic') or {}).get('subfield') or {}).get('display_name') or 'Astronomy and Astrophysics'
        out.append({'id': (w.get('id') or '').rsplit('/', 1)[-1], 'title': title, 'abstract': abstract[:4000],
                    'authors': ', '.join(names[:12]) + (' et al.' if len(names) > 12 else ''), 'cats': 'journal: ' + sub_,
                    'year': int(w.get('publication_year') or 0), 'created': w.get('publication_date') or '',
                    'doi': (w.get('doi') or '').replace('https://doi.org/', '')})
    return out, (js.get('meta') or {}).get('next_cursor'), (js.get('meta') or {}).get('count')


def harvest_openalex(con, log, deadline, y0=1976, y1=1991):
    """Year by year, page by page (OpenAlex allows plain page numbers up to 10,000 results without an API key;
    each year here has about 5,000 papers)."""
    if kv(con, 'oa_complete'):
        return 0, 0
    year, page = int(kv(con, 'oa_year') or y0), int(kv(con, 'oa_page') or 1)
    added = pages = 0
    while time.time() < deadline and year <= y1:
        params = {'filter': f'primary_topic.subfield.id:{OA_SUBFIELD},publication_year:{year},has_abstract:true',
                  'select': 'id,doi,display_name,publication_year,publication_date,authorships,abstract_inverted_index,primary_topic',
                  'per-page': 200, 'page': page, 'sort': 'publication_date:asc'}
        try:
            js = net.fetch_json(OPENALEX, params=params, timeout=120, min_gap=1.0)
        except urllib.error.HTTPError as e:
            try:
                body = e.read()[:300].decode('utf-8', 'replace')
            except Exception:
                body = ''
            log(f'  OpenAlex answered HTTP {e.code}: {body}')
            if e.code in (401, 403, 429):
                log('  OpenAlex paused (free daily budget or access limit); continuing next run')
                break
            raise
        recs, _, total = oa_records(js)
        for r in recs:
            cur = con.execute('INSERT OR IGNORE INTO papers(id,title,authors,abstract,cats,year,created,doi) VALUES(?,?,?,?,?,?,?,?)',
                              (r['id'], r['title'], r['authors'], r['abstract'], r['cats'], r['year'], r['created'], r['doi']))
            added += cur.rowcount
        con.commit()
        pages += 1
        counts = kv(con, 'oa_counts') or {}
        counts[str(year)] = int(total or 0)
        kv(con, 'oa_counts', counts)
        kv(con, 'oa_total', sum(counts.values()) + 4800 * max(0, y1 - year))
        if not js.get('results') or page * 200 >= min(int(total or 0), 10000):
            year, page = year + 1, 1
        else:
            page += 1
        kv(con, 'oa_year', year)
        kv(con, 'oa_page', page)
    if year > y1:
        kv(con, 'oa_complete', True)
        log(f'  OpenAlex {y0}-{y1}: complete')
    return added, pages


# ------------------------------------------------------------------ embeddings + topics
def _topic_vecs(llm, con):
    vecs = kv(con, 'topic_vecs')
    if vecs and len(vecs) == len(TOPICS):
        return np.array(vecs, dtype=np.float32)
    v = llm.embed(list(TOPICS.values()))
    if not v:
        return None
    v = np.asarray(v, dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    kv(con, 'topic_vecs', v.tolist())
    return v


def embed_pending(con, llm, log, deadline, batch=32):
    tv = _topic_vecs(llm, con)
    names = list(TOPICS)
    done = 0
    EMB.parent.mkdir(parents=True, exist_ok=True)
    nrow = EMB.stat().st_size // (DIM * 2) if EMB.exists() else 0
    while time.time() < deadline:
        rows = con.execute('SELECT rowid, id, title, abstract FROM papers WHERE erow IS NULL ORDER BY year DESC LIMIT ?', (batch,)).fetchall()
        if not rows:
            break
        vecs = llm.embed([(r['title'] + '. ' + (r['abstract'] or ''))[:1800] for r in rows])
        if not vecs or len(vecs) != len(rows):
            log('  embedding model did not answer; will retry next run')
            break
        v = np.asarray(vecs, dtype=np.float32)
        v /= np.linalg.norm(v, axis=1, keepdims=True) + 1e-9
        with open(EMB, 'ab') as f:
            f.write(v.astype(np.float16).tobytes())
        for i, r in enumerate(rows):
            tags = None
            if tv is not None:
                s = tv @ v[i]
                o = np.argsort(-s)
                tags = [names[o[0]]] + ([names[o[1]]] if s[o[1]] > s[o[0]] - 0.03 else [])
            con.execute('UPDATE papers SET erow=?, tags=? WHERE rowid=?', (nrow + i, json.dumps(tags), r['rowid']))
        con.commit()
        nrow += len(rows)
        done += len(rows)
    return done


def status(con=None):
    con = con or connect()
    n = con.execute('SELECT COUNT(*) FROM papers').fetchone()[0]
    e = con.execute('SELECT COUNT(*) FROM papers WHERE erow IS NOT NULL').fetchone()[0]
    yr = con.execute('SELECT MIN(year), MAX(year) FROM papers').fetchone()
    size = sum(p.stat().st_size for p in (DB, EMB) if p.exists()) / 1e9
    return {'papers': n, 'embedded': e, 'years': [yr[0], yr[1]], 'total_expected': (kv(con, 'total') or 0) + (kv(con, 'oa_total') or 0),
            'complete': bool(kv(con, 'complete')) and bool(kv(con, 'oa_complete')), 'disk_gb': round(size, 2), 'topics': list(TOPICS)}


# ------------------------------------------------------------------ search + AI review
def _fts_query(q):
    words = [w for w in re.findall(r'[A-Za-z0-9]{3,}', q)][:12]
    return ' OR '.join(f'"{w}"' for w in words)


def search(qtext, llm, y0=None, y1=None, cat=None, topic=None, k=40):
    con = connect()
    scores = {}
    qv = llm.embed([qtext]) if llm else None
    if qv and EMB.exists():
        q = np.asarray(qv[0], dtype=np.float32)
        q /= np.linalg.norm(q) + 1e-9
        emb = np.memmap(EMB, dtype=np.float16, mode='r').reshape(-1, DIM)
        best = []
        for i in range(0, len(emb), 50_000):
            s = emb[i:i + 50_000].astype(np.float32) @ q
            top = np.argpartition(-s, min(1500, len(s) - 1))[:1500]
            best += [(float(s[j]), i + int(j)) for j in top]
        best.sort(reverse=True)
        rows = {r['erow']: r['rowid'] for r in con.execute(
            f'SELECT rowid, erow FROM papers WHERE erow IN ({",".join(str(e) for _, e in best[:3000])})')} if best else {}
        for s, e in best[:3000]:
            if e in rows:
                scores[rows[e]] = s
    fq = _fts_query(qtext)
    if fq:
        try:
            for r in con.execute('SELECT rowid, bm25(fts) AS b FROM fts WHERE fts MATCH ? ORDER BY b LIMIT 400', (fq,)):
                scores[r['rowid']] = scores.get(r['rowid'], 0.35) + min(0.15, -r['b'] / 100)
        except sqlite3.OperationalError:
            pass
    if not scores:
        return []
    ids = sorted(scores, key=lambda r: -scores[r])[:4000]
    where, args = [f'rowid IN ({",".join(map(str, ids))})'], []
    if y0:
        where.append('year >= ?'); args.append(int(y0))
    if y1:
        where.append('year <= ?'); args.append(int(y1))
    if cat:
        where.append('cats LIKE ?'); args.append(f'%{cat}%')
    if topic:
        where.append('tags LIKE ?'); args.append(f'%{topic}%')
    rows = [dict(r) for r in con.execute(f'SELECT rowid, id, title, authors, abstract, cats, year, doi, tags FROM papers WHERE {" AND ".join(where)}', args)]
    for r in rows:
        r['score'] = round(scores[r['rowid']], 4)
        r['tags'] = json.loads(r['tags']) if r['tags'] else []
        r['url'] = paper_url(r)
    rows.sort(key=lambda r: -r['score'])
    return rows[:k]


def paper_url(r):
    pid = r['id']
    if re.fullmatch(r'W\d+', pid):
        return f'https://doi.org/{r["doi"]}' if r.get('doi') else f'https://openalex.org/{pid}'
    return f'https://arxiv.org/abs/{pid}'


def review(q, papers, llm, log=lambda *a: None):
    """Gemma reads each abstract and rates how well it answers the question. Cached per (paper, question)."""
    con = connect()
    qh = hashlib.sha1(q.strip().lower().encode()).hexdigest()[:16]
    out, todo = {}, []
    for p in papers:
        r = con.execute('SELECT score, summary FROM reviews WHERE id=? AND q=?', (p['id'], qh)).fetchone()
        if r:
            out[p['id']] = {'score': r['score'], 'summary': r['summary']}
        else:
            todo.append(p)
    for i in range(0, len(todo), 5):
        chunk = todo[i:i + 5]
        prompt = ('Question: ' + q + '\n\nFor each paper below, rate 0-10 how directly its abstract helps answer the question, '
                  'and write ONE plain-English sentence (max 30 words) a curious non-expert would understand. '
                  'Answer as JSON: {"papers": [{"id": "...", "score": 0-10, "summary": "..."}]}.\n\n' +
                  '\n\n'.join(f'id: {p["id"]}\ntitle: {p["title"]}\nabstract: {(p.get("abstract") or "")[:1500]}' for p in chunk))
        r = llm.chat(prompt, kind='writer', json_mode=True, max_tokens=900, temperature=0.1, num_ctx=12288)
        for item in (r or {}).get('papers', []) if isinstance(r, dict) else []:
            pid = str(item.get('id', '')).strip()
            if pid in {p['id'] for p in chunk}:
                try:
                    sc = max(0.0, min(10.0, float(item.get('score', 0))))
                except (TypeError, ValueError):
                    sc = 0.0
                sm = str(item.get('summary', ''))[:300]
                out[pid] = {'score': sc, 'summary': sm}
                con.execute('INSERT OR REPLACE INTO reviews(id, q, score, summary, t) VALUES(?,?,?,?,?)', (pid, qh, sc, sm, time.time()))
        con.commit()
    return out


# ------------------------------------------------------------------ job
def run(ctx):
    from .llm import LLM
    lc = ctx.cfg.get('library') or {}
    con = connect()
    llm = LLM(ctx.cfg, ctx.log)
    start = time.time()
    total = 60 * float(lc.get('time_budget_min', 45))
    end = start + total
    added = pages = 0
    y0 = int(lc.get('min_year', 1976))
    arxiv_y0 = max(y0, 1992)
    if kv(con, 'min_year') != arxiv_y0:            # range changed: restart the arXiv pass (existing papers are kept)
        kv(con, 'token', None); kv(con, 'from', None); kv(con, 'complete', False); kv(con, 'min_year', arxiv_y0)
    arx_end = start + 0.45 * total             # downloads get up to ~45%,
    try:
        added, pages = harvest(con, ctx.log, arx_end, arxiv_y0)
    except Exception as e:
        ctx.log(f'arXiv bulk service (OAI) paused: {e}')
    if time.time() < arx_end - 60:
        try:
            a1, p1 = harvest_api(con, ctx.log, arx_end, arxiv_y0)
            added += a1; pages += p1
        except Exception as e:
            ctx.log(f'arXiv search API paused: {e}')
    if y0 < 1992:
        try:
            a2, p2 = harvest_openalex(con, ctx.log, start + 0.6 * total, y0, 1991)  # the search index the rest
            added += a2; pages += p2
            if p2:
                ctx.log(f'  OpenAlex {y0}-1991: {a2} papers added this run')
        except Exception as e:
            ctx.log(f'OpenAlex harvest paused: {e}')
    ctx.progress(f'{added} new papers; embedding')
    emb = embed_pending(con, llm, ctx.log, end - 60)
    st = status(con)
    return (f'{added} new papers ({pages} pages); {emb} embedded this run; library: {st["papers"]:,} papers '
            f'({st["years"][0]}-{st["years"][1]}), {st["embedded"]:,} searchable by meaning, {st["disk_gb"]} GB')
