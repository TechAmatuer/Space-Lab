"""Variable-star observatory, part 5: what is new out there, and what changed in the observatory's own stars.

- AAVSO observing campaigns and alert notices. The AAVSO lists its active campaigns at apps.aavso.org/v2/campaigns; each
  campaign page gives the targets, dates, filters and what the astronomers ask for. The page's fixed fields are read
  directly; the free text ("nightly, and several times a night in outburst; DSLR and visual not needed") is read by the
  local AI, whose answer is checked (a target must appear on the page, filters must be real filters, dates real dates). Without
  the local AI, simple rules read it. Campaign stars go into tonight's plan with the campaign's filters and cadence, until
  its end date, with or without a Target Tool key.
- Outbursts and fades. Each new measurement is compared with the star's recent AAVSO data (the last 30 days from the AAVSO
  International Database, through the VSX API) or, failing that, with this observatory's own earlier measurements, using
  what the star is: a dwarf nova that brightens by a magnitude is in outburst, an R CrB star that fades by one is in a
  dip, a VY Scl star that fades is in a low state. The observer is told at once (Mac notification, phone push), the star
  gets a higher priority for the next nights, and nothing is posted anywhere from here.
- Coverage (optional, off by default). Which stars this observatory measured lately, and tonight's plan, can go on the
  public page (star names, filters and dates only), and other Space Lab observatories listed as peers are read, so two
  observatories do not both spend the night on the same star while another one waits.
"""
import json
import math
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import numpy as np

from . import config, db, net

CAMPAIGNS = 'https://apps.aavso.org/v2/campaigns/'
VSX_API = 'https://www.aavso.org/vsx/index.php'
AID_DIR = config.DATA / 'vstars' / 'aid'
LABELS = ('Abstract', 'Justification', 'Requested Data Types', 'Targets', 'Photometry Filters', 'Comparison Stars', 'Co-Authorship',
          'Additional Observer Input', 'Additional Submission Location', 'Notes', 'Spectroscopy Lines', 'Active Dates')


# ------------------------------------------------------------------ reading web pages without a browser
class _Text(HTMLParser):
    BLOCK = {'p', 'div', 'br', 'li', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article', 'dt', 'dd', 'ul', 'ol', 'header',
             'footer', 'title', 'main', 'table', 'thead', 'tbody', 'nav', 'blockquote', 'pre', 'hr'}
    SKIP = {'script', 'style', 'noscript', 'svg', 'template'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.links, self.tables = [], [], []
        self.skip, self.href, self.atext = 0, None, []
        self.row, self.cell = None, None

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == 'tr':
            self.row = []
        elif tag in ('td', 'th'):
            self.cell = []
        elif tag in self.BLOCK:
            (self.cell if self.cell is not None else self.out).append(' ' if self.cell is not None else '\n')
        if tag == 'a':
            self.href, self.atext = dict(attrs).get('href'), []

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag in ('td', 'th') and self.cell is not None:
            if self.row is None:
                self.row = []
            self.row.append(' '.join(''.join(self.cell).split()))
            self.cell = None
        elif tag == 'tr' and self.row is not None:
            if any(self.row):
                self.tables.append(self.row)
                self.out.append('\n' + ' | '.join(self.row) + '\n')
            self.row = None
        elif tag in self.BLOCK and self.cell is None:
            self.out.append('\n')
        if tag == 'a' and self.href is not None:
            self.links.append((self.href, ' '.join(''.join(self.atext).split())))
            self.href = None

    def handle_data(self, data):
        if self.skip:
            return
        (self.cell if self.cell is not None else self.out).append(data)
        if self.href is not None:
            self.atext.append(data)


def html_text(html):
    """(text with one line per paragraph or table row, [(href, link text)], [table rows as lists of cells])."""
    p = _Text()
    p.feed(html or '')
    p.close()
    lines = [' '.join(x.split()) for x in ''.join(p.out).split('\n')]
    return '\n'.join(x for x in lines if x), p.links, p.tables


def _date(x):
    """'2026-03-13 (UTC)', 'Jan. 20, 2025', 'January 31, 2026', '31 Dec 2026' -> 'YYYY-MM-DD', else None ('Ongoing')."""
    s = str(x or '').strip()
    m = re.search(r'(\d{4})-(\d{2})-(\d{2})', s)
    if m:
        return '-'.join(m.groups())
    s = re.sub(r'\(.*?\)', '', s).replace('.', '').replace(',', ' ').strip()
    s = re.sub(r'\bSept\b', 'Sep', s)
    for fmt in ('%b %d %Y', '%B %d %Y', '%d %b %Y', '%d %B %Y'):
        try:
            return datetime.strptime(' '.join(s.split()[:3]), fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def filter_codes(text):
    """Filter names as written on AAVSO pages -> AAVSO codes: 'R (Cousins), r (Sloan)' -> ['R', 'SR']."""
    out = []
    for tok in re.split(r'[,;/]|\band\b|\bor\b', str(text or '')):
        t = tok.strip()
        low = t.lower()
        if not t:
            continue
        code = None
        if 'sloan' in low or "'" in t or re.fullmatch(r"[ugriz]['’]?", t):
            m = re.search(r'\b([ugrizUGRIZ])\b|^([ugriz])', t)
            if m:
                code = 'S' + (m.group(1) or m.group(2)).upper()
        elif 'clear' in low or 'unfiltered' in low:
            code = 'CV'
        else:
            m = (re.match(r'^\(?([UBVRI])[cj]?\b', t) or re.search(r'\b([UBVRI])[cj]?\s*\((?:johnson|cousins|bessell)', t, re.I)
                 or re.search(r'\b(?:johnson|cousins|bessell)\s+([UBVRI])\b', t, re.I))
            if m:
                code = m.group(1).upper()
        if code and code not in out:
            out.append(code)
    return out


def cadence_days(text):
    """How often (days) from words such as 'nightly', 'every 3 days', 'several times a night', 'weekly'; None if not said."""
    t = str(text or '').lower()
    if not t.strip() or re.search(r'not specified|unspecified', t) and not re.search(r'night|daily|week|every \d', t):
        return None
    m = re.search(r'every\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|h|days?|d|nights?|weeks?)\b', t)
    if m:
        n, u = float(m.group(1)), m.group(2)
        return n / 24 if u.startswith('h') else n * 7 if u.startswith('w') else n
    if re.search(r'nightly|every (clear )?night|each night|once (a|per) night|daily|every day', t):
        return 1.0                     # "nightly, more often in outburst": the outburst check raises the priority when that happens
    if re.search(r'(several|multiple|many|a few|high)[ -](times|observations|points|cadence|images)|times (a|per) night|per night|high[- ]cadence', t):
        return 0.25
    if re.search(r'weekly|once (a|per) week|every week', t):
        return 7.0
    m = re.search(r'(\d+(?:\.\d+)?)\s*(?:days?|d)\b', t)
    if m and 0 < float(m.group(1)) <= 60:
        return float(m.group(1))
    return None


def _section(text, label):
    """The text under one label of a campaign page, up to the next known label."""
    m = re.search(r'(?im)^\s*' + re.escape(label) + r'\s*:?\s*(.*)$', text)
    if not m:
        return ''
    rest = text[m.start(1):]
    nxt = [mm.start() for lab in LABELS if lab != label for mm in [re.search(r'(?im)^\s*' + re.escape(lab) + r'\s*:?', rest[1:])] if mm]
    return rest[:min(nxt) + 1 if nxt else 1500].strip()


# ------------------------------------------------------------------ AAVSO campaigns
def campaign_list(html):
    """Active campaigns from the AAVSO campaigns page: [{'id', 'title', 'url', 'start', 'end', 'types'}]."""
    text, links, rows = html_text(html)
    by_id = {}
    for r in rows:
        if r and re.fullmatch(r'#?\s*\d+', r[0].strip()):
            by_id[int(re.sub(r'\D', '', r[0]))] = r
    out, seen = [], set()
    for href, label in links:
        m = re.search(r'/campaigns/(\d+)/?(?:[?#].*)?$', href or '')
        if not m:
            continue
        cid = int(m.group(1))
        if cid in seen:
            continue
        seen.add(cid)
        r = by_id.get(cid) or []
        dates = [c for c in r if re.match(r'\d{4}-\d{2}-\d{2}|ongoing', c.strip(), re.I)]
        title = label if label and not label.isdigit() else (r[1] if len(r) > 1 else '')
        out.append({'id': cid, 'title': title, 'url': urljoin(CAMPAIGNS, href), 'start': _date(dates[0]) if dates else None,
                    'end': _date(dates[1]) if len(dates) > 1 else None, 'ongoing': bool(dates[1:] and 'ongoing' in dates[1].lower()),
                    'types': r[-1] if len(r) >= 5 else ''})
    return out


def parse_campaign(text, tables=()):
    """The fixed fields of one campaign page (no AI): dates, data types, targets table, filters, cadence, observer notes."""
    c = {'targets': [], 'filters': [], 'cadence_days': None, 'time_series': False}
    val = _section(text, 'Active Dates').split('\n')[0]     # "Jan. 5, 2027 – Dec. 31, 2027", on the label's line or the next
    if val:
        parts = re.split(r'\s+[–—-]\s+|\s+to\s+', val.strip(), maxsplit=1)
        c['start'] = _date(parts[0])
        c['end'] = _date(parts[1]) if len(parts) > 1 else None
        c['ongoing'] = 'ongoing' in val.lower()
    types = _section(text, 'Requested Data Types')
    c['types'] = types.split('\n')[0][:120]
    c['photometry'] = 'photometry' in types.lower() if types else None
    for i, row in enumerate(tables):
        low = [x.lower() for x in row]
        if any(x.startswith('name') for x in low) and any('variab' in x or 'magnitude' in x for x in low):
            ix = {k: next((j for j, x in enumerate(low) if x.startswith(k)), None) for k in ('name', 'magnitude', 'variab', 'photometry')}
            for r in tables[i + 1:]:
                if len(r) < 2 or r[0].lower().startswith('name'):
                    break
                g = lambda k: r[ix[k]] if ix.get(k) is not None and ix[k] < len(r) else ''
                notes = g('photometry')
                mag = re.search(r'-?\d+(?:\.\d+)?', g('magnitude'))
                prec = re.search(r'Precision\s*:?\s*(\d+(?:\.\d+)?)', notes, re.I)
                cad = re.search(r'Cadence\s*:?\s*(.+?)(?:Precision|$)', notes, re.I)
                c['targets'].append({'name': g('name').strip(), 'mag': float(mag.group(0)) if mag else None, 'type': g('variab').strip(),
                                     'cadence_days': cadence_days(cad.group(1)) if cad else None,
                                     'precision': float(prec.group(1)) if prec else None})
            break
    c['filters'] = filter_codes(_section(text, 'Photometry Filters').split('\n')[0])
    c['input'] = _section(text, 'Additional Observer Input')[:1200]
    c['abstract'] = _section(text, 'Abstract')[:1500]
    words = ' '.join([c['input'], c['abstract']])
    cads = [t['cadence_days'] for t in c['targets'] if t.get('cadence_days')]
    c['cadence_days'] = min(cads) if cads else cadence_days(c['input']) or cadence_days(c['abstract'])
    c['time_series'] = bool(re.search(r'time[- ]series|continuous(ly)? (photometry|monitoring|coverage)', words, re.I))
    c['urgent'] = bool(re.search(r'\burgent', words, re.I))
    return c


CAMPAIGN_PROMPT = """Here is the text of one AAVSO observing-campaign page:
<<<
{text}
>>>
Return JSON with exactly these keys:
"targets": the star names observers are asked to observe, written exactly as on the page (a list),
"photometry": true if photometry is requested (not only spectroscopy),
"filters": the photometric filters requested, as AAVSO codes (U, B, V, R, I, CV, SU, SG, SR, SI, SZ), or [] if any filter is fine,
"cadence_days": how often each star should be observed, in days (nightly = 1, several times a night = 0.25, weekly = 7), or null if not said,
"time_series": true if continuous time-series photometry is asked for,
"series_minutes": the length of a requested time series in minutes, or null,
"until": the last date observations are wanted, as YYYY-MM-DD, or null,
"urgent": true if the page says observations are needed urgently now,
"note": one short plain-English sentence telling the observer what to do."""


def ask_llm(llm, text):
    """The local AI's reading of a campaign page, checked: names must be on the page, filters real, numbers sensible."""
    if not llm:
        return None
    try:
        r = llm.chat(CAMPAIGN_PROMPT.format(text=text[:6000]), system='You read AAVSO observing-campaign pages and reply with JSON only.',
                     kind='json', json_mode=True, max_tokens=500, temperature=0.0)
    except Exception:
        return None
    if not isinstance(r, dict):
        return None
    page = ' '.join(text.lower().split())
    out = {'targets': [str(t).strip() for t in (r.get('targets') or []) if isinstance(t, str) and len(t.strip()) >= 3
                       and ' '.join(t.lower().split()) in page][:20]}
    out['filters'] = filter_codes(', '.join(str(f) for f in (r.get('filters') or []) if isinstance(f, str)))
    num = lambda v, lo, hi: float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and lo <= float(v) <= hi else None
    out['cadence_days'] = num(r.get('cadence_days'), 0.01, 60)
    out['series_minutes'] = num(r.get('series_minutes'), 10, 720)
    out['until'] = _date(r.get('until')) if isinstance(r.get('until'), str) else None
    for k in ('photometry', 'time_series', 'urgent'):
        out[k] = r.get(k) if isinstance(r.get(k), bool) else None
    out['note'] = str(r.get('note') or '')[:240] if isinstance(r.get('note'), str) else ''
    return out


def merge(page, ai):
    """The page's fixed fields win; the AI fills what the page leaves to free text."""
    c = dict(page)
    c['by'] = 'page'
    if not ai:
        if not c['targets']:
            c['by'] = 'page (no targets table found)'
        return c
    c['by'] = 'page + local AI'
    if not c['targets'] and ai['targets']:
        c['targets'] = [{'name': n, 'mag': None, 'type': '', 'cadence_days': None, 'precision': None} for n in ai['targets']]
    if not c['filters']:
        c['filters'] = ai['filters']
    if not c.get('cadence_days'):
        c['cadence_days'] = ai['cadence_days']
    c['time_series'] = bool(c.get('time_series') or ai.get('time_series'))
    c['series_minutes'] = c.get('series_minutes') or ai.get('series_minutes')
    if not c.get('end') and ai.get('until'):
        c['end'] = ai['until']
    if c.get('photometry') is None:
        c['photometry'] = ai.get('photometry')
    c['urgent'] = bool(c.get('urgent') or ai.get('urgent'))
    c['note'] = ai.get('note') or ''
    return c


def campaigns(con, s, fetch=None, llm=None, log=print, now=None, max_pages=25, refresh_days=7):
    """The active photometry campaigns, refreshed once a day (each campaign page at most once a week)."""
    now = now or time.time()
    cache = db.kv_get(con, 'vs_campaigns') or {}
    items = cache.get('items') or {}
    if now - cache.get('t', 0) > 20 * 3600 or not items:
        get = fetch or (lambda url: net.fetch_text(url, timeout=60))
        try:
            lst = campaign_list(get(CAMPAIGNS))
        except Exception as e:
            log(f'  AAVSO campaigns page did not answer ({type(e).__name__}); using the last copy')
            lst = None
        if lst is not None:
            pages = 0
            for c in lst:
                old = items.get(str(c['id']))
                if old and now - old.get('at', 0) < refresh_days * 86400:
                    old.update({k: c[k] for k in ('title', 'url') if c.get(k)})
                    continue
                if pages >= max_pages:
                    break
                try:
                    text, _links, tables = html_text(get(c['url']))
                except Exception as e:
                    log(f"  campaign #{c['id']}: page did not answer ({type(e).__name__})")
                    continue
                pages += 1
                rec = merge(parse_campaign(text, tables), ask_llm(llm, text))
                rec.update(id=c['id'], title=c['title'] or rec.get('title') or f"campaign #{c['id']}", url=c['url'], at=now,
                           start=rec.get('start') or c.get('start'), end=rec.get('end') or c.get('end'))
                if rec.get('photometry') is None and c.get('types'):
                    rec['photometry'] = 'photometry' in c['types'].lower()
                items[str(c['id'])] = rec
            listed = {str(c['id']) for c in lst}
            items = {k: v for k, v in items.items() if k in listed}
            cache = {'t': now, 'items': items}
            db.kv_set(con, 'vs_campaigns', cache)
            log(f'  AAVSO campaigns: {len(lst)} active, {pages} page(s) read')
    today = datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d')
    return [c for c in items.values() if c.get('photometry') is not False and c.get('targets')
            and (not c.get('end') or c['end'] >= today) and (not c.get('start') or c['start'] <= today)]


def jname_position(name):
    """Position from a coordinate name such as 'PSN J12355230+2755559' or 'TCP J17395720-2627410' (RA hhmmss.ss, Dec ddmmss.s)."""
    m = re.search(r'J(\d{2})(\d{2})(\d{2})\.?(\d*)([+-])(\d{2})(\d{2})(\d{2})\.?(\d*)', str(name or ''))
    if not m:
        return None
    h, mi, se, sf, sg, d, dm, ds, df = m.groups()
    ra = (int(h) + int(mi) / 60 + float(f'{se}.{sf or 0}') / 3600) * 15
    dec = int(d) + int(dm) / 60 + float(f'{ds}.{df or 0}') / 3600
    if ra >= 360 or dec > 90:
        return None
    return {'ra': ra, 'dec': -dec if sg == '-' else dec}


def campaign_targets(con, s, camps, vsx=None, log=print):
    """Planner targets from the campaigns (positions from VSX, or from a coordinate name)."""
    from .vsobs import vsx_object
    cache = db.kv_get(con, 'vs_vsx') or {}
    out = []
    for c in camps:
        for t in (c.get('targets') or [])[:20]:
            nm = t['name']
            o = cache.get(nm)
            if not o:
                try:
                    o = vsx_object(nm, (lambda n=nm: vsx(n)) if vsx else None)
                except Exception:
                    o = None
                if o:
                    cache[nm] = o
            if not o:
                pos = jname_position(nm)
                if not pos:
                    log(f"  campaign #{c['id']}: {nm} not found in VSX; left out")
                    continue
                o = dict(pos, name=nm, type=t.get('type') or '', period=None, max_mag=t.get('mag'), min_mag=None)
            series = bool(c.get('time_series'))
            out.append({'name': nm, 'ra': o['ra'], 'dec': o['dec'], 'type': t.get('type') or o.get('type') or '', 'period': o.get('period'),
                        'min_mag': o.get('min_mag'), 'max_mag': o.get('max_mag') if o.get('max_mag') is not None else t.get('mag'),
                        'cadence': t.get('cadence_days') or c.get('cadence_days') or float(s.get('campaign_cadence_days', 1.0)),
                        'last': None, 'priority': True, 'sections': ['Alerts / Campaigns'], 'filters': [],
                        'want_filters': list(c.get('filters') or []),
                        'info': f"AAVSO campaign #{c['id']}: {c.get('title') or ''}"[:300], 'source': f"AAVSO campaign #{c['id']}",
                        'photometry': True, 'conj': False, 'series': series, 'series_min': c.get('series_minutes') if series else None,
                        'campaign': c['id'], 'campaign_url': c.get('url'), 'until': c.get('end'), 'urgent': bool(c.get('urgent')),
                        'note': c.get('note') or ''})
    db.kv_set(con, 'vs_vsx', cache)
    return out


def merge_targets(tt, camp):
    """Target Tool targets enriched with what the campaigns say; campaign stars the Target Tool did not list are added."""
    by = {t['name'].upper(): t for t in tt}
    out = list(tt)
    for c in camp:
        t = by.get(c['name'].upper())
        if not t:
            out.append(c)
            by[c['name'].upper()] = c
            continue
        t['priority'] = True
        if 'Alerts / Campaigns' not in (t.get('sections') or []):
            t['sections'] = list(t.get('sections') or []) + ['Alerts / Campaigns']
        for k in ('campaign', 'campaign_url', 'until', 'note', 'urgent'):
            if c.get(k):
                t[k] = c[k]
        if c.get('series'):
            t['series'], t['series_min'] = True, t.get('series_min') or c.get('series_min')
        if c.get('want_filters') and not t.get('want_filters'):
            t['want_filters'] = c['want_filters']
        if c.get('cadence') and (not t.get('cadence') or c['cadence'] < t['cadence']):
            t['cadence'] = c['cadence']
        t['info'] = (str(t.get('info') or '') + ' · ' + c['info'])[:300] if c['info'] not in str(t.get('info') or '') else t.get('info')
    return out


# ------------------------------------------------------------------ the AAVSO International Database (recent data)
def _f(x):
    try:
        v = float(str(x).strip())
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def aid_obs(name, days, now=None, fetch=None, max_age_h=6):
    """The star's AAVSO observations of the last `days` days (fainter-than and discrepant ones left out), kept 6 hours."""
    from .vsphot import slug
    now = now or time.time()
    AID_DIR.mkdir(parents=True, exist_ok=True)
    p = AID_DIR / f'{slug(name)}.json'
    if p.exists():
        try:
            c = json.loads(p.read_text())
            if now - c.get('t', 0) < max_age_h * 3600 and c.get('days', 0) >= days:
                return c['obs']
        except ValueError:
            pass
    jd1 = now / 86400 + 2440587.5
    params = {'view': 'api.delim', 'ident': name, 'fromjd': f'{jd1 - days:.4f}', 'tojd': f'{jd1:.4f}', 'delimiter': '@@@'}
    txt = (fetch or (lambda q: net.fetch_text(VSX_API, params=q, timeout=90)))(params)
    lines = [x for x in (txt or '').splitlines() if x.strip()]
    obs = []
    if lines and '@@@' in lines[0]:
        head = [h.strip().lower() for h in lines[0].split('@@@')]
        col = lambda *ns: next((head.index(n) for n in ns if n in head), None)
        ij, im, ie, ib, ifa, iv = col('jd'), col('mag'), col('uncert', 'uncertainty', 'err'), col('band'), col('fainterthan', 'fainter'), col('valflag', 'val')
        for ln in lines[1:] if ij is not None and im is not None else []:
            q = ln.split('@@@')
            jd, mag = _f(q[ij]) if ij < len(q) else None, _f(q[im]) if im < len(q) else None
            if jd is None or mag is None:
                continue
            if ifa is not None and ifa < len(q) and q[ifa].strip().lower() in ('1', 'true', 'yes'):
                continue
            if iv is not None and iv < len(q) and q[iv].strip().upper() == 'T':
                continue
            obs.append({'jd': jd, 'mag': mag, 'err': _f(q[ie]) if ie is not None and ie < len(q) else None,
                        'band': q[ib].strip() if ib is not None and ib < len(q) else ''})
    p.write_text(json.dumps({'t': now, 'days': days, 'obs': obs}))
    return obs


# ------------------------------------------------------------------ outbursts and fades
RULES = [   # (VSX type, direction, magnitudes, what it is called)
    (r'^(NL/VY|VY)', 'fainter', 1.0, 'low state'),
    (r'^(UG|DN)', 'brighter', 1.0, 'outburst'),
    (r'^(NR|NA|NB|NC|N)$', 'brighter', 0.5, 'brightening'),
    (r'^(ZAND|SDOR|FUOR|EXOR|BLLAC|AGN|QSO|GCAS|BE)', 'brighter', 0.5, 'brightening'),
    (r'^(RCB|DYPER)', 'fainter', 1.0, 'fade'),
    (r'^(INS|UXOR|ISA|ISB|IN|YSO|CTTS|WTTS|IT)', 'fainter', 0.75, 'fade'),
]
PERIODIC = r'^(EA|EB|EW|ELL|EP|R$|RR|RRAB|RRC|DCEP|CEP|CW|DSCT|HADS|BCEP|SXPHE|ROAP|ZZ|M$|SR|LB|LC|L$|RV|ACV|BY|ROT|GDOR|SPB)'
V_LIKE = {'V', 'VIS', 'VIS.', 'CV', 'TG', 'SG'}


def rule_for(vtype):
    t = re.split(r'[+|]', str(vtype or '').upper().replace(':', ''))[0].strip()
    for pat, direction, mag, word in RULES:
        if re.match(pat, t):
            return direction, mag, word
    return None


def _same_band(band, mine):
    b = str(band or '').upper()
    return b == mine or (mine == 'V' and b in V_LIKE)


def check_star(con, s, star, band, new, info=None, fetch=None, now=None):
    """Compare tonight's measurements of one star in one filter with its recent behaviour: an event dict, or None."""
    now = now or time.time()
    good = [r for r in new if r.get('mag') is not None and (r.get('kdev') is None or abs(r['kdev']) <= float(s.get('check_tol', 0.1)))
            and not any('saturat' in f for f in (r.get('flags') or []))]
    if len(good) < int(s.get('outburst_min_points', 2)):
        return None
    mags = np.array([r['mag'] for r in good])
    if len(mags) >= 3 and 1.4826 * float(np.median(np.abs(mags - np.median(mags)))) > 0.3:
        return None                                       # tonight's points disagree with each other: not trusted
    m_now = float(np.median(mags))
    night = good[-1].get('night')
    days = float(s.get('outburst_days', 30))
    jd_now = now / 86400 + 2440587.5
    base_src, ref = None, []
    if fetch is not False:                                # False: no AAVSO data (offline tests); this observatory's own only
        try:
            obs = aid_obs(star, days, now, fetch)
            ref = [o['mag'] for o in obs if _same_band(o['band'], band) and o['jd'] < jd_now - 1.0]
            base_src = f'AAVSO data of the last {days:.0f} days ({len(ref)} observations)'
        except Exception:
            ref = []
    if len(ref) < 3:
        ref = [r['mag'] for r in db.rows(con, "SELECT mag FROM vs_meas WHERE target=? AND filter=? AND jd < ? AND jd > ? AND status != 'rejected'",
                                         (star, band, jd_now - 1.0, jd_now - 60))]
        base_src = f"this observatory's own measurements ({len(ref)})"
    if len(ref) < 3:
        return None
    ref = np.array(ref)
    vtype = (info or {}).get('type') or ''
    rule = rule_for(vtype)
    pct = {'brighter': 70, 'fainter': 30}.get(rule[0] if rule else '', 50)
    base = float(np.percentile(ref, pct))
    spread = 1.4826 * float(np.median(np.abs(ref - np.median(ref))))
    d = m_now - base
    kind = None
    if rule:
        direction, lim, word = rule
        if (direction == 'brighter' and d <= -lim) or (direction == 'fainter' and d >= lim):
            kind = word
    elif not re.match(PERIODIC, re.split(r'[+|]', vtype.upper().replace(':', ''))[0].strip() or '-') and abs(d) >= max(1.0, 5 * spread):
        kind = 'unusual brightening' if d < 0 else 'unusual fade'
    mx = (info or {}).get('max_mag')
    if not kind and band == 'V' and mx is not None and m_now < mx - 1.0:
        kind = 'brighter than its catalogued maximum'
    if not kind:
        return None
    words = (f"{kind}: {band} = {m_now:.2f} tonight, {abs(d):.2f} mag {'brighter' if d < 0 else 'fainter'} than its usual {base:.2f} "
             f"(from {base_src})")
    return {'star': star, 'band': band, 'kind': kind, 'night': night, 'mag': round(m_now, 3), 'base': round(base, 3), 'delta': round(d, 3),
            'n': len(good), 'type': vtype, 'words': words, 'at': now}


def outbursts(con, s, cfg, new_rows, known, fetch=None, now=None, sink=None, log=print):
    """Check tonight's new measurements; tell the observer about each new event once, and raise the star's priority."""
    now = now or time.time()
    events = db.kv_get(con, 'vs_events') or []
    seen = {(e['star'], e['kind'], e['night']) for e in events}
    groups = {}
    for r in new_rows:
        groups.setdefault((r['target'], r['filter'], r['night']), []).append(r)
    fresh = []
    for (star, band, night), rs in groups.items():
        alln = [dict(x, flags=json.loads(x['flags'] or '[]')) for x in db.rows(
            con, "SELECT mag, kdev, flags, night FROM vs_meas WHERE target=? AND filter=? AND night=? AND status != 'rejected'", (star, band, night))]
        try:
            ev = check_star(con, s, star, band, alln or rs, known.get(star.upper()), fetch, now)
        except Exception as e:
            log(f'  outburst check of {star} failed: {type(e).__name__}')
            ev = None
        if not ev or (ev['star'], ev['kind'], ev['night']) in seen:
            continue
        seen.add((ev['star'], ev['kind'], ev['night']))
        fresh.append(ev)
        events.insert(0, ev)
        boost = db.kv_get(con, 'vs_boost') or {}
        series = ev['kind'] in ('outburst', 'brightening') and str(ev.get('type') or '').upper().startswith(('UGSU', 'UGWZ', 'UG'))
        boost[star] = {'until': now + float(s.get('boost_days', 7)) * 86400, 'why': ev['words'], 'series_min': 120 if series else None, 'at': now}
        db.kv_set(con, 'vs_boost', boost)
        from . import health
        health.send(cfg, f"Space Lab: {star} {ev['kind']}", ev['words'] + '. It gets priority in the next nights\' plans. Nothing has been '
                    'reported for you: consider a time series and a post in the AAVSO forum.', sink)
        db.add_task(con, f"vs:event:{star}:{ev['night']}:{ev['kind']}", 'vstars', 'event', f"{star}: {ev['kind']} ({band} {ev['mag']:.2f})",
                    ev['words'] + '. Look at the light curve on the Variable star observatory tab; the AAVSO forum\'s time-sensitive section is '
                    'the place to report it if it holds.', {'tab': 'vstars'}, 1)
        log(f"  {star}: {ev['words']}")
    if fresh:
        db.kv_set(con, 'vs_events', events[:200])
    return fresh


def boosts(con, now=None):
    """Stars with a raised priority after an event: {NAME: info} (expired ones dropped)."""
    now = now or time.time()
    b = db.kv_get(con, 'vs_boost') or {}
    live = {k: v for k, v in b.items() if v.get('until', 0) > now}
    if len(live) != len(b):
        db.kv_set(con, 'vs_boost', live)
    return {k.upper(): v for k, v in live.items()}


# ------------------------------------------------------------------ coverage, shared with other observatories (optional)
def coverage(con, s, now=None, days=21):
    """What goes on the public page when vstars.share_coverage is on: star names, filters, nights. Nothing else."""
    if not s.get('share_coverage'):
        return {}
    now = now or time.time()
    jd0 = now / 86400 + 2440587.5 - days
    stars = {}
    try:
        rows = db.rows(con, "SELECT target, filter, night FROM vs_meas WHERE jd > ? AND status IN ('ok','review','filed','reported')", (jd0,))
    except Exception:
        rows = []
    for r in rows:
        st = stars.setdefault(r['target'], {'filters': set(), 'nights': set()})
        st['filters'].add(r['filter'])
        st['nights'].add(r['night'])
    plan = db.kv_get(con, 'vs_plan') or {}
    dark = plan.get('dark') or [0, 0]
    current = bool(dark[1]) and dark[0] - 30 * 3600 <= now <= dark[1] + 6 * 3600     # the plan is for tonight (or the night under way)
    tonight = [t['name'] for t in plan.get('targets') or []] if current else []
    out = [{'name': k, 'filters': sorted(v['filters']), 'nights': sorted(v['nights'])[-10:]} for k, v in stars.items()]
    out.sort(key=lambda x: x['nights'][-1] if x['nights'] else '', reverse=True)
    return {'stars': out[:150], 'tonight': tonight[:80], 'night': plan.get('day') if tonight else None, 'updated': now}


def peers(s, fetch=None, log=print):
    """Other Space Lab observatories' coverage (vstars.peers: their public page addresses): {NAME: {'tonight', 'recent', 'last'}}."""
    out = {}
    for url in (s.get('peers') or [])[:20]:
        u = str(url or '').strip()
        if not u.startswith('https://'):
            continue
        if not u.endswith('.json'):
            u = u.rstrip('/') + '/data.json'
        try:
            js = (fetch or (lambda x: net.fetch_json(x, timeout=20, retries=1)))(u)
        except Exception as e:
            log(f'  peer {u}: no answer ({type(e).__name__})')
            continue
        v = js.get('vstars') if isinstance(js, dict) else None
        if not isinstance(v, dict):
            continue
        for nm in v.get('tonight') or []:
            out.setdefault(str(nm).upper(), {'tonight': 0, 'recent': 0, 'last': None})['tonight'] += 1
        for st in v.get('stars') or []:
            if not isinstance(st, dict) or not st.get('name'):
                continue
            e = out.setdefault(str(st['name']).upper(), {'tonight': 0, 'recent': 0, 'last': None})
            e['recent'] += 1
            last = max(st.get('nights') or [''])
            if last and (not e['last'] or last > e['last']):
                e['last'] = last
    return out
