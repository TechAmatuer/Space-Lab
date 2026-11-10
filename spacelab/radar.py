"""Space science radar (every 6 hours): the local AI reads everything new in space science and ranks it.

Sources: space agencies and observatories, research highlight sites, The Astronomer's Telegram, popular science
outlets, spaceflight news, and the best new arXiv papers from the morning digest. New items are de-duplicated (the same
story from several outlets is merged), then the local writer model scores each one for importance (0-10), sorts it
into a topic, and explains it in plain English. Items that mention your own targets become tasks.
Also keeps the "candidate of the day" fresh (spacelab/daily.py). Works without the AI (simpler keyword scoring).
"""
import hashlib
import re
import time

from . import db, net, news

# (name, feed URL). Feeds that fail are skipped and listed in the local Space brief so they can be fixed.
# Sites that refuse automated readers (JPL, Astrobites, SpaceNews, Sky & Telescope) are left out on purpose.
SOURCES = [
    ('NASA', 'https://www.nasa.gov/news-release/feed/'),
    ('NASA Science', 'https://science.nasa.gov/feed/'),
    ('ESA', 'https://www.esa.int/rssfeed/TopNews'),
    ('ESO', 'https://www.eso.org/public/news/feed/'),
    ('NOIRLab', 'https://noirlab.edu/public/news/feed/'),
    ('AAS Nova', 'https://aasnova.org/feed/'),
    ('Nature Astronomy', 'https://www.nature.com/natastron.rss'),
    ("The Astronomer's Telegram", 'https://www.astronomerstelegram.org/?rss'),
    ('Planetary Society', 'https://www.planetary.org/rss/articles'),
    ('Spaceflight Now', 'https://spaceflightnow.com/feed/'),
    ('NASASpaceflight', 'https://www.nasaspaceflight.com/feed/'),
] + [(n, u) for n, u in news.POP_FEEDS]

TOPICS = ['Exoplanets', 'Asteroids & comets', 'Sun & space weather', 'Planets & moons', 'Stars', 'Galaxies & cosmology',
          'Black holes & extreme events', 'Astrobiology', 'Missions & launches', 'Earth from space', 'Other']
KEYWORDS = {  # fallback scoring and topics when no AI is running
    'Exoplanets': ('exoplanet', 'tess', 'transit', 'jwst atmosphere', 'habitable zone', 'super-earth', 'hot jupiter'),
    'Asteroids & comets': ('asteroid', 'comet', 'near-earth', 'meteor', 'interstellar object', 'planetary defense'),
    'Sun & space weather': ('solar flare', 'sunspot', 'coronal mass', 'aurora', 'geomagnetic', 'space weather'),
    'Planets & moons': ('mars', 'jupiter', 'saturn', 'venus', 'europa', 'enceladus', 'titan', 'moon', 'mercury', 'pluto'),
    'Stars': ('star', 'supernova', 'nova', 'white dwarf', 'neutron star', 'stellar'),
    'Galaxies & cosmology': ('galaxy', 'galaxies', 'cosmolog', 'dark matter', 'dark energy', 'big bang', 'hubble constant'),
    'Black holes & extreme events': ('black hole', 'gravitational wave', 'gamma-ray burst', 'fast radio burst', 'quasar'),
    'Astrobiology': ('life', 'biosignature', 'organic', 'amino acid', 'habitab'),
    'Missions & launches': ('launch', 'rocket', 'spacecraft', 'mission', 'starship', 'artemis', 'iss', 'satellite'),
    'Earth from space': ('earth observation', 'climate', 'wildfire', 'hurricane'),
}
MAX_NEW = 120          # items the AI reads per run (about 15 short AI calls)
# Two texts (title + summary, bge-m3) this alike are the same story. Set from the real list of 2026-11-10: the eight true
# repeats in it scored 0.75 to 0.92 (also Spanish/English pairs), the most alike different stories 0.73 and below.
SAME = 0.78
KEEP_DAYS = 7


def _id(url):
    return hashlib.sha1((url or '').encode()).hexdigest()[:12]


def _words(t):
    return {w for w in re.findall(r'[a-z0-9]{4,}', (t or '').lower())}


def same_story(a, b):
    wa, wb = _words(a), _words(b)
    return bool(wa and wb) and len(wa & wb) / max(1, min(len(wa), len(wb))) >= 0.6


def collect(log, days=3):
    items, ok, bad = [], [], []
    seen_urls = set()
    for name, url in SOURCES:
        try:
            got = news.parse(net.fetch_text(url, timeout=45), name)
            ok.append(name)
        except Exception as e:
            bad.append(f'{name}: {str(e)[:80]}')
            continue
        for it in got[:40]:
            if not it['title'] or not it['url'] or it['url'] in seen_urls:
                continue
            if it['date'] and it['date'] < time.time() - days * 86400:
                continue
            seen_urls.add(it['url'])
            items.append(it)
    return items, sorted(set(ok)), bad


def merge_duplicates(items):
    """The same story from several outlets becomes one item that lists every source."""
    out = []
    for it in sorted(items, key=lambda i: -(i['date'] or 0)):
        twin = next((o for o in out if same_story(o['title'], it['title'])), None)
        if twin:
            if it['source'] not in twin['sources']:
                twin['sources'].append(it['source'])
                twin['links'].append(it['url'])
            continue
        out.append(dict(it, sources=[it['source']], links=[it['url']], arxiv=list(it.get('arxiv') or [])))
    return out


def _cos(a, b):
    import math
    na, nb = math.sqrt(sum(x * x for x in a)), math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb) if na and nb else 0.0


_ES = {'la', 'el', 'los', 'las', 'de', 'del', 'un', 'una', 'y', 'en', 'para', 'con', 'que', 'al', 'se', 'su', 'sus', 'por', 'más', 'es'}


def _spanish(title):
    """True for a title that reads as Spanish (NASA publishes many stories in both languages)."""
    t = (title or '').lower()
    n = sum(1 for w in re.findall(r'[a-záéíóúñü]+', t) if w in _ES)
    return n >= 3 or (n >= 2 and bool(re.search(r'[áéíóúñ¿¡]', t)))


def _lead_in_english(into, it):
    """When the same story is listed in Spanish and English, the English one leads on this English page."""
    if _spanish(into.get('title')) and not _spanish(it.get('title')):
        for k in ('title', 'url', 'summary', 'why', 'topic'):
            if it.get(k):
                into[k] = it[k]


def _absorb(into, it):
    for src, url in zip(it.get('sources') or [it['source']], it.get('links') or [it['url']]):
        if src not in into['sources']:
            into['sources'].append(src)
            into.setdefault('links', []).append(url)
    into['arxiv'] = sorted(set(into.get('arxiv') or []) | set(it.get('arxiv') or []))


def dedupe(fresh, recent, embed=None, same=SAME):
    """Remove repeats the word check misses, so the local AI never reads or lists the same story twice:
    1. a news article that links the arXiv paper it reports on becomes one item with that paper;
    2. stories with the same meaning (bge-m3 embeddings, cosine >= `same`) become one item, within this run;
    3. a story already listed in the last days (same paper, same words or same meaning) is added to that item's sources
       instead of being listed again.
    Returns (new items to rate, {recent item id: [extra sources]}, links of stories folded into older ones)."""
    out = []
    for it in fresh:                                                         # 1. article <-> paper by arXiv id
        ids = set(it.get('arxiv') or [])
        twin = next((o for o in out if ids & set(o.get('arxiv') or [])), None) if ids else None
        if twin:
            news_first = 'arXiv' in twin['sources'][0] and 'arXiv' not in it['source']
            _absorb(twin, it)
            if news_first:                                                   # the plain-English article leads, the paper is a source
                twin.update(title=it['title'], url=it['url'], summary=it.get('summary') or twin.get('summary'))
            continue
        out.append(it)
    grew, folded = {}, []
    old_by_arxiv = {a: r for r in recent for a in r.get('arxiv') or []}
    keep = []
    for it in out:                                                           # 3a. already listed: same paper, or same words
        r = next((old_by_arxiv[a] for a in it.get('arxiv') or [] if a in old_by_arxiv), None)
        r = r or next((x for x in recent if same_story(x['title'], it['title'])), None)
        if r:
            grew.setdefault(r['id'], []).extend(s for s in it['sources'] if s not in r['sources'])
            folded += it.get('links') or [it['url']]
            continue
        keep.append(it)
    if embed and keep:
        text = lambda x: (x['title'] + '. ' + (x.get('summary') or ''))[:600]
        vec = embed([text(x) for x in keep] + [text(x) for x in recent])
        if vec and len(vec) == len(keep) + len(recent):
            kv, rv = vec[:len(keep)], vec[len(keep):]
            final, fv = [], []
            for it, v in zip(keep, kv):
                j = max(range(len(recent)), key=lambda k: _cos(v, rv[k])) if recent else None
                if j is not None and _cos(v, rv[j]) >= same:                 # 3b. already listed, same meaning
                    grew.setdefault(recent[j]['id'], []).extend(s for s in it['sources'] if s not in recent[j]['sources'])
                    folded += it.get('links') or [it['url']]
                    continue
                k = max(range(len(final)), key=lambda q: _cos(v, fv[q])) if final else None
                if k is not None and _cos(v, fv[k]) >= same:                 # 2. same meaning in this run
                    _absorb(final[k], it)
                    _lead_in_english(final[k], it)
                    continue
                final.append(it); fv.append(v)
            keep = final
    return keep, grew, folded


def tidy(items, embed=None, same=SAME):
    """Merge repeats inside the stored list (stories listed before a check existed, or that a check missed): same paper,
    same words or same meaning. The better-rated one stays and takes the other's sources. Returns (items, number merged)."""
    vec = None
    if embed and len(items) > 1:
        try:
            import numpy as np
            v = embed([(x['title'] + '. ' + (x.get('summary') or ''))[:600] for x in items])
            if v and len(v) == len(items):
                v = np.asarray(v, float)
                v /= np.maximum(np.linalg.norm(v, axis=1), 1e-12)[:, None]
                vec = v @ v.T
        except Exception:
            vec = None
    order = sorted(range(len(items)), key=lambda k: (-items[k].get('score', 0), items[k].get('found', 0)))
    keep, gone = [], 0
    for k in order:
        it = items[k]
        ids = set(it.get('arxiv') or [])
        twin = next((j for j in keep if (ids & set(items[j].get('arxiv') or [])) or same_story(items[j]['title'], it['title'])
                     or (vec is not None and vec[k, j] >= same)), None)
        if twin is None:
            keep.append(k)
            continue
        into = items[twin]
        for src, url in zip(it.get('sources') or [], (it.get('links') or [it['url']]) + [it['url']] * 9):
            if src not in into['sources']:
                into['sources'].append(src)
                into.setdefault('links', []).append(url)
        into['arxiv'] = sorted(set(into.get('arxiv') or []) | ids)
        into['lab'] = sorted(set(into.get('lab') or []) | set(it.get('lab') or []))
        _lead_in_english(into, it)
        gone += 1
    return [items[k] for k in sorted(keep)], gone


def english_titles(items, chat, limit=12):
    """Headlines that arrive in Spanish are put into English by the local AI (this page is in English); the original is kept
    in title_es. chat(prompt) -> text. Returns how many were translated."""
    n = 0
    for x in items:
        if n >= limit or not _spanish(x.get('title')):
            continue
        try:
            en = chat('Translate this Spanish headline into plain English. Reply with the English headline only, nothing else.\n\n' + x['title'])
        except Exception:
            en = None
        en = ((en or '').strip().splitlines() or [''])[0].strip().strip('"“”').strip()
        if 8 <= len(en) <= 220 and not _spanish(en):
            x['title_es'], x['title'] = x['title'], en
            n += 1
    return n


def keyword_score(it):
    text = (it['title'] + ' ' + it.get('summary', '')).lower()
    topic = next((t for t, kws in KEYWORDS.items() if any(k in text for k in kws)), 'Other')
    score = 4 + min(3, len(it.get('sources', [])) - 1) + (1 if topic != 'Other' else 0)
    return {'score': score, 'topic': topic, 'summary': it.get('summary', '')[:240], 'why': ''}


PROMPT = """You are the science editor of a public astronomy website. Rate each news item below.
For each item return: "id"; "score" 0-10 (10 = once-a-year discovery or event, e.g. the first image of a black hole;
8 = important new result that will be widely reported; 6 = solid new result; 4 = routine news, mission updates, previews;
2 = not really space science; 0 = advertising, deals or opinion). Be strict: most items are 3-6, and at most one item in a
batch should get 9 or 10; "topic" (one of: {topics});
"summary" (two plain-English sentences a 12-year-old could follow, no hype, keep numbers accurate);
"why" (one sentence on why it matters). Use only the information given; do not invent details.
Reply as JSON: {{"items": [{{"id": "...", "score": 0, "topic": "...", "summary": "...", "why": "..."}}]}}

Items:
{items}"""


def ai_rate(llm, batch, log):
    text = '\n'.join(f'- id {it["id"]} | {", ".join(it["sources"])} | {it["title"]} | {it.get("summary", "")[:260]}' for it in batch)
    r = llm.chat(PROMPT.format(topics=', '.join(TOPICS), items=text), kind='writer', json_mode=True, max_tokens=1600, temperature=0.2)
    out = {}
    for x in (r or {}).get('items') or []:
        try:
            out[str(x['id'])] = {'score': max(0, min(10, int(round(float(x.get('score', 0)))))),
                                 'topic': x.get('topic') if x.get('topic') in TOPICS else 'Other',
                                 'summary': str(x.get('summary') or '')[:400], 'why': str(x.get('why') or '')[:240]}
        except (KeyError, TypeError, ValueError):
            continue
    return out


DIGEST = """Write a short "space science radar" update for a public website: 3 to 5 sentences in plain English covering the
most important items below, most important first. No hype, no greetings, no headings. Items:
{items}"""


def run(ctx):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    from .llm import LLM
    from .arxiv_digest import tracked_names
    llm = LLM(cfg, log)
    old = db.kv_get(con, 'radar') or {}
    seen = db.kv_get(con, 'radar_seen') or {}
    raw, ok, bad = collect(log)
    # the best of this morning's arXiv digest joins the radar
    # the best of this morning's arXiv astro-ph digest joins the radar
    for p in db.rows(con, "SELECT id, title, url, summary, published FROM arxiv WHERE rank IS NOT NULL AND rank <= 10 "
                          "ORDER BY published DESC, rank LIMIT 10"):
        raw.append({'source': 'arXiv (new research)', 'title': p['title'], 'url': p['url'], 'date': time.time(),
                    'summary': p.get('summary') or '', 'arxiv': [str(p['id']).split('v')[0]]})
    merged = merge_duplicates(raw)
    fresh = [it for it in merged if _id(it['url']) not in seen and not any(_id(u) in seen for u in it['links'])][:MAX_NEW]
    recent = [x for x in (old.get('items') or []) if x.get('found', 0) > time.time() - 4 * 86400]
    fresh, grew, folded = dedupe(fresh, recent, llm.embed if llm.models() else None)
    for it in fresh:
        it['id'] = _id(it['url'])
    for u in folded:                                      # proven repeats of stories already listed: never read again
        seen[_id(u)] = time.time()
    log(f'{len(raw)} items from {len(ok)} sources; {len(fresh)} new after removing repeats'
        + (f'; {len(folded)} were already listed under another title (their sources were added there)' if folded else '')
        + (f'; {len(bad)} sources failed' if bad else ''))
    rated = {}
    if llm.models():
        for i in range(0, len(fresh), 8):
            if ctx.time_left() < 120:
                log('  time is up; the rest waits for the next run')
                break
            rated.update(ai_rate(llm, fresh[i:i + 8], log))
    pats = [(re.compile(p, re.I), lab) for p, lab in tracked_names(con).items()]
    now, kept = time.time(), []
    for it in fresh:
        r = rated.get(it['id']) or keyword_score(it)
        if it['id'] not in rated and llm.models() and ctx.time_left() < 120:
            continue           # not read yet: leave it for the next run instead of guessing
        seen[it['id']] = now
        for u in it['links']:
            seen[_id(u)] = now
        text = it['title'] + ' ' + it.get('summary', '')
        lab = sorted({lab for rx, lab in pats if rx.search(text)})
        kept.append({'id': it['id'], 'title': it['title'], 'url': it['url'], 'sources': it['sources'], 'links': it['links'][:5], 'arxiv': it.get('arxiv') or [],
                     'date': it['date'] or now, 'found': now, 'score': r['score'], 'topic': r['topic'],
                     'summary': r['summary'] or it.get('summary', '')[:240], 'why': r['why'], 'ai': it['id'] in rated, 'lab': lab})
    for x in old.get('items') or []:                      # stories already listed that other outlets covered since
        for src in grew.get(x['id'], []):
            if src not in x['sources']:
                x['sources'].append(src)
    items = [x for x in (kept + (old.get('items') or [])) if x['found'] > now - KEEP_DAYS * 86400]
    items, n_tidy = tidy(items, llm.embed if llm.models() else None)
    if n_tidy:
        log(f'  {n_tidy} repeats inside the list merged into the story they repeat')
    if llm.models() and ctx.time_left() > 90:
        n_en = english_titles(items, lambda q: llm.chat(q, kind='writer', max_tokens=80, temperature=0.1))
        if n_en:
            log(f'  {n_en} Spanish headlines put into English by the local AI')
    items.sort(key=lambda x: (-x['score'], -x['date']))
    digest = old.get('digest')
    top = [x for x in kept if x['score'] >= 6][:6]
    if top and llm.models():
        d = llm.chat(DIGEST.format(items='\n'.join(f'- [{x["score"]}/10] {x["title"]}: {x["summary"]}' for x in top)),
                     kind='writer', max_tokens=350, temperature=0.3)
        if d:
            digest = {'t': now, 'text': d.strip()}
    db.kv_set(con, 'radar', {'t': now, 'items': items[:300], 'digest': digest, 'sources_ok': ok, 'sources_failed': bad,
                             'model': (llm.used or [None])[-1]})
    db.kv_set(con, 'radar_seen', {k: v for k, v in seen.items() if v > now - 30 * 86400})
    n_tasks = 0
    for x in sorted(kept, key=lambda x: -x['score']):
        if n_tasks >= 3:
            break
        if x['lab'] or x['score'] >= 9:
            db.add_task(con, f'radar:{x["id"]}', 'arxiv', 'read',
                        ('In the news: ' + x['lab'][0].split(' (')[0] if x['lab'] else f'Big space news ({x["score"]}/10)') + f': {x["title"][:90]}',
                        x['summary'], {'tab': 'arxiv', 'url': x['url']}, priority=2)
            n_tasks += 1
    try:
        from . import daily
        daily.ensure(con, cfg, log, llm)
    except Exception as e:
        log(f'  candidate of the day skipped: {e}')
    big = sum(1 for x in kept if x['score'] >= 7)
    return f'read {len(kept)} new items from {len(ok)} sources ({len(rated)} rated by the local AI); {big} important' + \
        (f'; {len(bad)} sources failed' if bad else '')
