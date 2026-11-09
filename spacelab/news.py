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
    ('ESA Space Science', 'https://www.esa.int/rssfeed/Our_Activities/Space_Science'),
    ('NOIRLab news', 'https://noirlab.edu/public/news/feed/'),
    ('Minor Planet Center (MPECs)', 'https://www.minorplanetcenter.net/mpec/RecentMPECs.xml'),
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


def parse(xml_text, source):
    root = ET.fromstring(xml_text)
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
