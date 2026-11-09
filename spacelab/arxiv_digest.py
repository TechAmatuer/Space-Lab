"""Daily arXiv digest: fetch new astro-ph papers, rank them against your interests with local embeddings
(bge-m3), and have the local LLM explain the top ones in plain English."""
import hashlib
import math
import re
import time
import xml.etree.ElementTree as ET

from . import db, net
from .llm import LLM

API = 'https://export.arxiv.org/api/query'
A = '{http://www.w3.org/2005/Atom}'
AX = '{http://arxiv.org/schemas/atom}'


def parse_feed(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall(A + 'entry'):
        raw_id = (e.findtext(A + 'id') or '').strip()
        m = re.search(r'abs/([^\s]+?)(v\d+)?$', raw_id)
        if not m:
            continue
        aid = m.group(1)
        title = re.sub(r'\s+', ' ', e.findtext(A + 'title') or '').strip()
        abstract = re.sub(r'\s+', ' ', e.findtext(A + 'summary') or '').strip()
        authors = [re.sub(r'\s+', ' ', a.findtext(A + 'name') or '').strip() for a in e.findall(A + 'author')]
        cats = [c.get('term') for c in e.findall(A + 'category') if c.get('term')]
        prim = e.find(AX + 'primary_category')
        if prim is not None and prim.get('term') in cats:
            cats.remove(prim.get('term'))
            cats.insert(0, prim.get('term'))
        out.append({'id': aid, 'title': title, 'abstract': abstract, 'authors': authors, 'cats': cats,
                    'published': (e.findtext(A + 'published') or '')[:10], 'url': f'https://arxiv.org/abs/{aid}'})
    return out


def fetch_new(cfg, log):
    q = ' OR '.join(f'cat:{c}' for c in cfg['arxiv']['categories'])
    params = {'search_query': q, 'sortBy': 'submittedDate', 'sortOrder': 'descending',
              'start': 0, 'max_results': int(cfg['arxiv']['max_fetch'])}
    data = net.fetch(API, params=params, timeout=90, min_gap=3.5)
    papers = parse_feed(data)
    log(f'fetched {len(papers)} papers from arXiv')
    return papers


def _cos(a, b):
    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    dbb = math.sqrt(sum(y * y for y in b))
    return num / (da * dbb) if da and dbb else 0.0


def first_sentences(text, n=2):
    parts = re.split(r'(?<=[.!?])\s+', text.strip())
    return ' '.join(parts[:n])


def score_papers(papers, cfg, llm, con, log):
    kws = [k.lower() for k in cfg['arxiv']['keywords']]
    interests = cfg['arxiv']['interests']
    key = 'arxiv_interest_vecs:' + hashlib.sha1(('|'.join(interests) + cfg['llm']['embed']).encode()).hexdigest()[:12]
    ivecs = db.kv_get(con, key)
    if ivecs is None:
        ivecs = llm.embed(interests)
        if ivecs:
            db.kv_set(con, key, ivecs)
    pvecs = llm.embed([(p['title'] + '. ' + p['abstract'])[:2000] for p in papers]) if ivecs else None
    for i, p in enumerate(papers):
        if pvecs:
            p['vec'] = pvecs[i]   # kept so "Ask Space Lab" can search papers by meaning
        text = (p['title'] + ' ' + p['abstract']).lower()
        boost = min(0.12, 0.03 * sum(1 for k in kws if k in text))
        if pvecs:
            p['score'] = max(_cos(pvecs[i], v) for v in ivecs) + boost
        else:
            p['score'] = boost  # keyword-only ranking when embeddings are unavailable
    log('ranked with ' + ('embeddings + keywords' if pvecs else 'keywords only (embedding model not available)'))
    return papers


SYSTEM = ('You explain brand-new astronomy research to a curious 12th-grade student who loves space. '
          'Use plain words. Only use facts that are in the abstract. Never invent numbers.')


def explain(p, llm):
    prompt = (f"Title: {p['title']}\nAbstract: {p['abstract'][:2500]}\n\n"
              'Return JSON with exactly these keys:\n'
              '"summary": 2-3 short plain-English sentences on what they did and found,\n'
              '"why": one sentence on why it matters,\n'
              '"project": which of these hobby projects it relates to: "exoplanet-watch" (timing known planets\' transits), '
              '"tess" (finding new planets in TESS data), "asteroids" (near-Earth asteroids, impact risk, precovery), or "none".')
    r = llm.chat(prompt, system=SYSTEM, json_mode=True, max_tokens=400)
    if isinstance(r, dict) and r.get('summary'):
        proj = str(r.get('project', 'none')).lower()
        if proj not in ('exoplanet-watch', 'tess', 'asteroids'):
            proj = 'none'
        return str(r['summary']).strip(), str(r.get('why', '')).strip(), proj
    return first_sentences(p['abstract']), '', 'none'


def run(ctx):
    cfg, con, log = ctx.cfg, ctx.con, ctx.log
    llm = LLM(cfg, log)
    try:
        from . import news
        n, ok = news.refresh(con, log)
        log(f'{n} news items from {len(ok)} other sources')
    except Exception as e:
        log(f'news feeds: {e}')
    try:
        from . import news
        news.papers_in_news(con, log)
    except Exception as e:
        log(f'research in the news: {e}')
    papers = fetch_new(cfg, log)
    known = {r['id'] for r in db.rows(con, 'SELECT id FROM arxiv')}
    new = [p for p in papers if p['id'] not in known]
    log(f'{len(new)} not seen before')
    if new:
        score_papers(new, cfg, llm, con, log)
        now = time.time()
        for p in new:
            con.execute('INSERT OR IGNORE INTO arxiv(id,title,authors,abstract,cats,published,url,score,added) '
                        'VALUES(?,?,?,?,?,?,?,?,?)',
                        (p['id'], p['title'], ', '.join(p['authors'][:8]) + (' et al.' if len(p['authors']) > 8 else ''),
                         p['abstract'], ', '.join(p['cats']), p['published'], p['url'], p['score'], now))
            if p.get('vec'):
                import numpy as np
                con.execute('INSERT OR REPLACE INTO arxiv_emb(id, v) VALUES(?,?)', (p['id'], np.asarray(p['vec'], dtype=np.float32).tobytes()))
    today = time.strftime('%Y-%m-%d')
    if db.one(con, 'SELECT 1 AS x FROM arxiv WHERE digest=?', (today,)):
        log('today already has a digest; adding nothing new')
        return f'{len(new)} new papers; digest for {today} already built'
    pool = db.rows(con, 'SELECT * FROM arxiv WHERE digest IS NULL AND added > ? ORDER BY score DESC LIMIT ?',
                   (time.time() - 4 * 86400, int(cfg['arxiv']['top_n'])))
    if not pool:
        return f'{len(new)} new papers; nothing new to digest (arXiv posts no new papers on weekends)'
    for rank, p in enumerate(pool, 1):
        summary, why, proj = explain(p, llm)
        con.execute('UPDATE arxiv SET digest=?, rank=?, summary=?, why=?, project=? WHERE id=?',
                    (today, rank, summary, why, proj, p['id']))
        log(f'  #{rank} {p["title"][:80]}')
    db.close_tasks_like(con, 'arxiv:read:%', status='dismissed', except_key=f'arxiv:read:{today}')
    linked = sum(1 for p in db.rows(con, "SELECT project FROM arxiv WHERE digest=? AND project!='none'", (today,)))
    db.add_task(con, f'arxiv:read:{today}', 'arxiv', 'read',
                f"Read today's space brief ({len(pool)} papers)",
                f'{linked} relate to your projects. About 5 minutes.', {'tab': 'arxiv', 'date': today}, priority=3)
    model = llm.used[-1] if llm.used else 'no AI (abstract excerpts)'
    return f'{len(new)} new papers, top {len(pool)} explained by {model}'
