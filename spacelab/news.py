"""Space news from beyond arXiv, for the Space brief: journal and agency feeds (RSS/Atom), last 7 days.
No AI needed; titles link to the source. Feeds that fail are skipped."""
import html
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

from . import db, net

FEEDS = [
    ('AAS Nova (research highlights)', 'https://aasnova.org/feed/'),
    ('Nature Astronomy', 'https://www.nature.com/natastron.rss'),
    ('NASA Science', 'https://science.nasa.gov/feed/'),
    ('ESA', 'https://www.esa.int/rssfeed/TopNews'),
    ('NOIRLab news', 'https://noirlab.edu/public/news/feed/'),
]


def _when(s):
    if not s:
        return None
    try:
        return parsedate_to_datetime(s).timestamp()
    except Exception:
        pass
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()
    except Exception:
        return None


def _fix_entities(xml_text):
    """Some feeds use HTML entities (&nbsp; &mdash;) that XML does not know: turn them into numeric references."""
    from html.entities import name2codepoint
    known = {'amp', 'lt', 'gt', 'quot', 'apos'}
    xml_text = re.sub(r'&(?!#\d+;|#x[0-9a-fA-F]+;|[A-Za-z][A-Za-z0-9]*;)', '&amp;', xml_text)    # a bare "&" (e.g. "Q&A")
    return re.sub(r'&([A-Za-z][A-Za-z0-9]*);', lambda m: m.group(0) if m.group(1) in known or m.group(1) not in name2codepoint
                  else f'&#{name2codepoint[m.group(1)]};', xml_text)


def parse(xml_text, source):
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        root = ET.fromstring(_fix_entities(xml_text))
    items = []
    for it in root.iter():
        tag = it.tag.split('}')[-1]
        if tag not in ('item', 'entry'):
            continue
        get = lambda name: next((c for c in it if c.tag.split('}')[-1] == name), None)
        title = (get('title').text if get('title') is not None else '') or ''
        link_el = get('link')
        link = (link_el.get('href') or link_el.text) if link_el is not None else ''
        date = None
        for name in ('pubDate', 'published', 'updated', 'date'):
            el = get(name)
            if el is not None and el.text:
                date = _when(el.text.strip())
                break
        desc_el = get('description') if get('description') is not None else get('summary')
        desc = re.sub(r'<[^>]+>', ' ', html.unescape(desc_el.text or '')) if desc_el is not None else ''
        items.append({'source': source, 'title': re.sub(r'\s+', ' ', html.unescape(title)).strip(), 'url': (link or '').strip(),
                      'date': date, 'summary': re.sub(r'\s+', ' ', desc).strip()[:300]})
    return items


def refresh(con, log, days=7):
    out, ok = [], []
    for name, url in FEEDS:
        try:
            items = parse(net.fetch_text(url, timeout=45), name)
            ok.append(name)
            out += [i for i in items if i['title'] and (i['date'] is None or i['date'] > time.time() - days * 86400)][:12]
        except Exception as e:
            log(f'  news feed {name} skipped: {e}')
    out.sort(key=lambda i: -(i['date'] or 0))
    db.kv_set(con, 'news', {'t': time.time(), 'items': out[:60], 'sources': ok})
    return len(out), ok


# ------------------------------------------------------------------ research in the news
# Popular, consumer-friendly outlets. Each morning their newest space stories are opened and checked for a link
# to the research paper behind the story (arXiv, a DOI, or a journal page). Only stories that cite a paper are kept.
POP_FEEDS = [
    ('Space.com', 'https://www.space.com/feeds/all'),
    ('Universe Today', 'https://www.universetoday.com/feed/'),
    ('Astronomy Magazine', 'https://www.astronomy.com/feed/'),
    ('Phys.org', 'https://phys.org/rss-feed/space-news/'),
    ('ScienceAlert', 'https://www.sciencealert.com/space/feed'),
    ('New Scientist', 'https://www.newscientist.com/subject/space/feed/'),
    ('EarthSky', 'https://earthsky.org/feed/'),
    ('Quanta Magazine', 'https://api.quantamagazine.org/feed/'),
    ('The Conversation', 'https://theconversation.com/us/technology/articles.atom'),
]
ARXIV_RE = re.compile(r'arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})', re.I)
DOI_RE = re.compile(r'(10\.\d{4,9}/[A-Za-z0-9._;()/:+-]+)')
NATURE_RE = re.compile(r'nature\.com/articles/(s\d{5}-\d{3}-\d{4,5}-[0-9a-z])', re.I)
HREF_RE = re.compile(r'href=["\']([^"\']+)["\']', re.I)
SKIP_DOI = ('10.1002/9781', '10.5281/zenodo')     # books / data deposits rather than the paper itself


def cited_papers(html_text):
    """arXiv ids and DOIs linked from an article page, in order of appearance."""
    out = []
    for href in HREF_RE.findall(html_text or ''):
        h = html.unescape(href)
        m = ARXIV_RE.search(h)
        if m:
            out.append(('arxiv', m.group(1)))
            continue
        m = NATURE_RE.search(h)
        if m:
            out.append(('doi', '10.1038/' + m.group(1)))
            continue
        if 'doi' in h.lower() or any(d in h for d in ('iopscience.iop.org', 'science.org', 'pnas.org', 'wiley.com', 'aanda.org', 'cell.com')):
            m = DOI_RE.search(h)
            if m:
                d = m.group(1).rstrip('.,;)').split('?')[0].split('#')[0]
                for suffix in ('/full', '/abstract', '/pdf', '/meta'):
                    if d.lower().endswith(suffix):
                        d = d[:-len(suffix)]
                if not d.startswith(SKIP_DOI):
                    out.append(('doi', d))
    seen, uniq = set(), []
    for k in out:
        if k[1].lower() not in seen:
            seen.add(k[1].lower())
            uniq.append(k)
    return uniq


def paper_info(kind, pid):
    """Title/year/link for a cited paper: the local library first, then arXiv or OpenAlex."""
    try:
        from . import library
        con = library.connect()
        r = con.execute('SELECT id, title, year, doi FROM papers WHERE id=? OR lower(doi)=lower(?)', (pid, pid)).fetchone()
        if r:
            return {'id': pid, 'title': r['title'], 'year': r['year'], 'url': library.paper_url(dict(r))}
    except Exception:
        pass
    if kind == 'arxiv':
        url = f'https://arxiv.org/abs/{pid}'
        try:
            x = net.fetch_text('https://export.arxiv.org/api/query', params={'id_list': pid}, timeout=40, min_gap=3)
            items = parse(x, 'arXiv')
            if items:
                return {'id': pid, 'title': items[0]['title'], 'year': None, 'url': url}
        except Exception:
            pass
        return {'id': pid, 'title': None, 'year': None, 'url': url}
    url = f'https://doi.org/{pid}'
    try:
        w = net.fetch_json(f'https://api.openalex.org/works/doi:{pid}', params={'select': 'display_name,publication_year,primary_location'},
                           timeout=40, min_gap=1)
        venue = (((w.get('primary_location') or {}).get('source')) or {}).get('display_name')
        return {'id': pid, 'title': w.get('display_name'), 'year': w.get('publication_year'), 'url': url, 'venue': venue}
    except Exception:
        return {'id': pid, 'title': None, 'year': None, 'url': url}


def papers_in_news(con, log, days=3, max_articles=60):
    seen = db.kv_get(con, 'pin_seen') or {}          # article url -> {'t', 'papers'} so each article is opened once
    stories = []
    for name, url in POP_FEEDS:
        try:
            items = parse(net.fetch_text(url, timeout=45), name)
            stories += [i for i in items if i['url'].startswith('http') and (i['date'] is None or i['date'] > time.time() - days * 86400)][:15]
        except Exception as e:
            log(f'  {name} feed skipped: {e}')
    opened = 0
    for s in sorted(stories, key=lambda i: -(i['date'] or 0)):
        if s['url'] in seen or opened >= max_articles:
            continue
        opened += 1
        try:
            page = net.fetch_text(s['url'], timeout=40, min_gap=1.5)
            refs = cited_papers(page)[:3]
        except Exception:
            refs = []
        seen[s['url']] = {'t': time.time(), 'story': s, 'papers': [paper_info(k, p) for k, p in refs]}
    cutoff = time.time() - 14 * 86400
    seen = {u: v for u, v in seen.items() if v['t'] > cutoff}
    db.kv_set(con, 'pin_seen', seen)
    items = [{**v['story'], 'papers': v['papers']} for v in seen.values()
             if v['papers'] and (v['story'].get('date') or v['t']) > time.time() - 7 * 86400]
    items.sort(key=lambda i: -(i['date'] or 0))
    db.kv_set(con, 'paper_news', {'t': time.time(), 'items': items[:40]})
    log(f'research in the news: opened {opened} new stories, {len(items)} cite a paper this week')
    return items
