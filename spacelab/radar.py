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

# (name, feed URL). Feeds that fail are skipped and listed in the Meridian Space brief so they can be fixed.
SOURCES = [
    ('NASA', 'https://www.nasa.gov/news-release/feed/'),
    ('NASA Science', 'https://science.nasa.gov/feed/'),
    ('NASA JPL', 'https://www.jpl.nasa.gov/feeds/news/'),
    ('ESA', 'https://www.esa.int/rssfeed/Our_Activities/Space_Science'),
    ('ESA', 'https://www.esa.int/rssfeed/TopNews'),
    ('ESO', 'https://www.eso.org/public/news/feed/'),
    ('NOIRLab', 'https://noirlab.edu/public/news/feed/'),
    ('AAS Nova', 'https://aasnova.org/feed/'),
    ('Astrobites', 'https://astrobites.org/feed/'),
    ('Nature Astronomy', 'https://www.nature.com/natastron.rss'),
    ("The Astronomer's Telegram", 'https://www.astronomerstelegram.org/?rss'),
    ('Planetary Society', 'https://www.planetary.org/rss/articles'),
    ('SpaceNews', 'https://spacenews.com/feed/'),
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
        out.append(dict(it, sources=[it['source']], links=[it['url']]))
    return out


def keyword_score(it):
    text = (it['title'] + ' ' + it.get('summary', '')).lower()
    topic = next((t for t, kws in KEYWORDS.items() if any(k in text for k in kws)), 'Other')
    score = 4 + min(3, len(it.get('sources', [])) - 1) + (1 if topic != 'Other' else 0)
    return {'score': score, 'topic': topic, 'summary': it.get('summary', '')[:240], 'why': ''}


PROMPT = """You are the science editor of a public astronomy website. Rate each news item below.
For each item return: "id"; "score" 0-10 (10 = major discovery or event that space fans everywhere should hear about,
7 = interesting new result, 4 = routine news, 1 = not space science, advertising or opinion); "topic" (one of: {topics});
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
    for p in db.rows(con, "SELECT id, title, url, summary, published FROM arxiv WHERE rank IS NOT NULL AND rank <= 5 "
                          "ORDER BY published DESC, rank LIMIT 5"):
        raw.append({'source': 'arXiv (new research)', 'title': p['title'], 'url': p['url'], 'date': time.time(), 'summary': p.get('summary') or ''})
    fresh = [it for it in merge_duplicates(raw) if _id(it['url']) not in seen][:MAX_NEW]
    for it in fresh:
        it['id'] = _id(it['url'])
    log(f'{len(raw)} items from {len(ok)} sources; {len(fresh)} new after removing repeats' + (f'; {len(bad)} sources failed' if bad else ''))
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
        text = it['title'] + ' ' + it.get('summary', '')
        lab = sorted({lab for rx, lab in pats if rx.search(text)})
        kept.append({'id': it['id'], 'title': it['title'], 'url': it['url'], 'sources': it['sources'], 'links': it['links'][:5],
                     'date': it['date'] or now, 'found': now, 'score': r['score'], 'topic': r['topic'],
                     'summary': r['summary'] or it.get('summary', '')[:240], 'why': r['why'], 'ai': it['id'] in rated, 'lab': lab})
    items = [x for x in (kept + (old.get('items') or [])) if x['found'] > now - KEEP_DAYS * 86400]
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
