"""Extras for the public website, made at every publish: link previews (Open Graph), a share page per candidate,
the preview image, an Atom feed, a sitemap, an app icon + manifest, and the optional GoatCounter visitor counter.

GoatCounter (goatcounter.com) counts visits without cookies or personal data; it is only added when you enter its
code in Setup -> Sharing. Your GoatCounter API key is never published (publish.forbidden checks for it).
"""
import html
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path

e = lambda s: html.escape(str(s or ''), quote=True)


def describe(data):
    d = data.get('daily') or {}
    c = d.get('cand') or {}
    if c.get('tic'):
        return (f"Today's planet candidate: TIC {c['tic']}, a star that dims every {c['period']:.2f} days. Is it a planet? Vote, check "
                "tonight's sky and follow a home computer searching open NASA data for new worlds and asteroids.")
    return 'A home computer searching open NASA data for new planets and asteroid sightings, with games, sky alerts and the latest space science.'


def head_tags(data, cfg, title):
    url = (data.get('site') or {}).get('url') or ''
    tags = [f'<meta name="description" content="{e(describe(data))}">',
            '<meta name="theme-color" content="#0c1218">',
            '<link rel="manifest" href="manifest.webmanifest">',
            '<link rel="icon" type="image/png" href="icon-192.png">',
            '<link rel="apple-touch-icon" href="icon-192.png">',
            f'<link rel="alternate" type="application/atom+xml" title="Space Lab updates" href="{e(url)}feed.xml">',
            f'<link rel="alternate" type="text/calendar" title="Space Lab sky events" href="{e(url)}events.ics">',
            '<meta property="og:type" content="website">', f'<meta property="og:title" content="{e(title)}">',
            f'<meta property="og:description" content="{e(describe(data))}">', '<meta property="og:site_name" content="Space Lab">',
            '<meta name="twitter:card" content="summary_large_image">']
    if url:
        tags += [f'<link rel="canonical" href="{e(url)}">', f'<meta property="og:url" content="{e(url)}">',
                 f'<meta property="og:image" content="{e(url)}og.png">', '<meta property="og:image:width" content="1200">',
                 '<meta property="og:image:height" content="630">', f'<meta name="twitter:image" content="{e(url)}og.png">']
    code = str((cfg.get('share') or {}).get('goatcounter') or '').strip().lower()
    if re.match(r'^[a-z0-9][a-z0-9-]{1,49}$', code):
        tags.append(f'<script data-goatcounter="https://{code}.goatcounter.com/count" data-goatcounter-settings=\'{{"no_onload": true}}\' '
                    'async src="https://gc.zgo.at/count.js"></script>')
    return '\n'.join(tags)


def inject_head(index_html, tags):
    return index_html.replace('</head>', tags + '\n</head>', 1)


def share_page(c, url):
    """A tiny page per candidate: its own link preview (title, text, plot), then it opens the candidate on the main page."""
    tic = int(c['tic'])
    title = f'Planet candidate TIC {tic} · Space Lab'
    what = f"dims every {c['period']:.3f} days" if c.get('period') else 'showed one lone dip'
    desc = (f"A star that {what}, by {round((c.get('depth') or 0) * 1e6)} parts per million. If it is a planet it is about "
            f"{(c.get('rp') or 0):.1f} times Earth's width. Still a candidate: help check it.")
    img = f'{url}{c["plot"]}' if url and c.get('plot') else ''
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{e(title)}</title>
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="{e(desc)}">
<meta property="og:type" content="article"><meta property="og:title" content="{e(title)}"><meta property="og:description" content="{e(desc)}">
<meta property="og:site_name" content="Space Lab">{f'<meta property="og:image" content="{e(img)}"><meta name="twitter:image" content="{e(img)}">' if img else ''}
<meta name="twitter:card" content="summary_large_image">{f'<link rel="canonical" href="{e(url)}c/{tic}.html">' if url else ''}
<meta http-equiv="refresh" content="0; url=../#tic-{tic}"></head>
<body style="font-family:system-ui;background:#0c1218;color:#e1e8ee;padding:24px"><p><a style="color:#4db3d3" href="../#tic-{tic}">Open TIC {tic} on Space Lab</a></p></body></html>"""


def _iso(t):
    return datetime.fromtimestamp(t or time.time(), timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def feed(data):
    """Atom feed: candidate of the day, new planet candidates, results and the biggest space-science news."""
    url = (data.get('site') or {}).get('url') or ''
    entries = []
    d = data.get('daily') or {}
    if (d.get('cand') or {}).get('tic'):
        c = d['cand']
        entries.append((time.mktime(time.strptime(d['date'], '%Y-%m-%d')), f"Candidate of the day: TIC {c['tic']}", f'{url}#today', d.get('blurb') or ''))
    for c in (data.get('tess') or {}).get('cands', [])[:15]:
        entries.append((c.get('created') or 0, f"New planet candidate: TIC {c['tic']}", f"{url}c/{c['tic']}.html", c.get('explain') or ''))
    for r in (data.get('results') or [])[:10]:
        entries.append((r.get('updated') or r.get('created') or 0, f"Result: {r.get('title')} ({r.get('status')})", f'{url}#results', r.get('note') or ''))
    for x in (data.get('radar') or {}).get('items', []):
        if (x.get('score') or 0) >= 7:
            entries.append((x.get('date') or 0, f"[{x.get('topic')}] {x.get('title')}", x.get('url'), x.get('summary') or ''))
    entries.sort(key=lambda x: -x[0])
    body = ''.join(f'<entry><title>{e(t)}</title><link href="{e(link)}"/><id>{e(link)}#{int(ts)}</id><updated>{_iso(ts)}</updated>'
                   f'<summary>{e(summ)}</summary></entry>' for ts, t, link, summ in entries[:60])
    return (f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom"><title>Space Lab</title>'
            f'<link href="{e(url)}"/><link rel="self" href="{e(url)}feed.xml"/><id>{e(url) or "urn:spacelab"}</id><updated>{_iso(None)}</updated>'
            f'<author><name>Space Lab</name></author>{body}</feed>')


def _ics_text(s):
    return str(s or '').replace('\\', '\\\\').replace(';', '\\;').replace(',', '\\,').replace('\n', '\\n')


def _fold(line):
    out, b = [], line.encode('utf-8')
    while len(b) > 73:
        cut = 73
        while (b[cut] & 0xC0) == 0x80:      # never split a multi-byte character
            cut -= 1
        out.append(b[:cut].decode('utf-8')); b = b[cut:]
    out.append(b.decode('utf-8'))
    return '\r\n '.join(out)


def ics(events, url=''):
    """Calendar of upcoming sky events. Subscribe once and new events appear by themselves."""
    from datetime import timedelta
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    lines = ['BEGIN:VCALENDAR', 'VERSION:2.0', 'PRODID:-//Space Lab//Sky events//EN', 'CALSCALE:GREGORIAN', 'METHOD:PUBLISH',
             'X-WR-CALNAME:Space Lab sky events', 'X-WR-CALDESC:Meteor showers\\, eclipses\\, Moon phases\\, launches and comets',
             'REFRESH-INTERVAL;VALUE=DURATION:PT12H', 'X-PUBLISHED-TTL:PT12H']
    for ev in events or []:
        uid = re.sub(r'[^A-Za-z0-9]+', '-', f"{ev.get('kind')}-{ev.get('date') or ev.get('t')}-{ev.get('title')}")[:120] + '@spacelab'
        lines += ['BEGIN:VEVENT', f'UID:{uid}', f'DTSTAMP:{stamp}']
        if ev.get('t'):
            try:
                t = datetime.fromisoformat(ev['t'].replace('Z', '+00:00')).astimezone(timezone.utc)
            except ValueError:
                continue
            lines += [f"DTSTART:{t.strftime('%Y%m%dT%H%M%SZ')}", f"DTEND:{(t + timedelta(hours=1)).strftime('%Y%m%dT%H%M%SZ')}"]
            trigger = '-PT2H'
        else:
            d = datetime.fromisoformat(ev['date'])
            lines += [f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(d + timedelta(days=1)).strftime('%Y%m%d')}"]
            trigger = '-PT6H'                    # 6 pm the evening before
        lines += [f"SUMMARY:{_ics_text(ev.get('title'))}", f"DESCRIPTION:{_ics_text((ev.get('detail') or '') + (' ' + url if url else ''))}",
                  'BEGIN:VALARM', 'ACTION:DISPLAY', f"DESCRIPTION:{_ics_text(ev.get('title'))}", f'TRIGGER:{trigger}', 'END:VALARM', 'END:VEVENT']
    lines.append('END:VCALENDAR')
    return '\r\n'.join(_fold(x) for x in lines) + '\r\n'


def sitemap(data):
    url = (data.get('site') or {}).get('url') or ''
    pages = [url] + [f"{url}c/{c['tic']}.html" for c in (data.get('tess') or {}).get('cands', [])]
    return ('<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' +
            ''.join(f'<url><loc>{e(p)}</loc></url>' for p in pages) + '</urlset>')


def manifest():
    return json.dumps({'name': 'Space Lab', 'short_name': 'Space Lab', 'start_url': './', 'display': 'standalone',
                       'background_color': '#0c1218', 'theme_color': '#0c1218',
                       'description': 'Citizen science with open NASA data: planet candidates, asteroids, sky watch.',
                       'icons': [{'src': 'icon-192.png', 'sizes': '192x192', 'type': 'image/png'},
                                 {'src': 'icon-512.png', 'sizes': '512x512', 'type': 'image/png'}]})


def icon(path, size):
    """A ringed planet on a night sky."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.patches import Arc, Circle
    fig = plt.figure(figsize=(1, 1), dpi=size)
    ax = fig.add_axes([0, 0, 1, 1]); ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis('off')
    fig.patch.set_facecolor('#0c1218')
    rng = np.random.default_rng(7)
    ax.scatter(rng.random(40), rng.random(40), s=rng.random(40) * 0.8 * size / 100, c='white', alpha=0.7, lw=0)
    ring = dict(xy=(0.5, 0.5), width=0.86, height=0.22, angle=-20, ec='#f2b632', lw=size / 60)
    ax.add_patch(Arc(theta1=0, theta2=180, zorder=1, **ring))          # back half of the ring
    ax.add_patch(Circle((0.5, 0.5), 0.25, color='#4db3d3', zorder=2))
    ax.add_patch(Arc(theta1=180, theta2=360, zorder=3, **ring))        # front half
    fig.savefig(path, dpi=size, facecolor=fig.get_facecolor())
    plt.close(fig)


def og_image(path, data, files_root):
    """1200x630 link-preview picture: the candidate of the day's plot with a short caption."""
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig = plt.figure(figsize=(12, 6.3), dpi=100)
    fig.patch.set_facecolor('#0c1218')
    d = data.get('daily') or {}
    c = d.get('cand') or {}
    plot = Path(files_root) / c['plot'] if c.get('plot') else None
    if plot and plot.is_file():
        ax = fig.add_axes([0.45, 0.08, 0.53, 0.84]); ax.axis('off')
        ax.imshow(plt.imread(str(plot)))
    t = fig.text
    t(0.04, 0.84, 'SPACE LAB', color='#4db3d3', fontsize=30, weight='bold')
    if c.get('tic'):
        t(0.04, 0.68, 'Planet candidate', color='#e1e8ee', fontsize=26)
        t(0.04, 0.60, 'of the day', color='#e1e8ee', fontsize=26)
        t(0.04, 0.46, f"TIC {c['tic']}", color='#f2b632', fontsize=30, weight='bold')
        t(0.04, 0.38, f"dips every {c['period']:.2f} days", color='#b3bfcb', fontsize=18)
        t(0.04, 0.32, f"~{(c.get('rp') or 0):.1f}× Earth if it is a planet", color='#b3bfcb', fontsize=18)
        t(0.04, 0.16, 'Planet or not? Vote!', color='#2fbf71', fontsize=20, weight='bold')
    else:
        t(0.04, 0.6, 'Hunting planets and asteroids', color='#e1e8ee', fontsize=24)
        t(0.04, 0.52, 'with open NASA data', color='#e1e8ee', fontsize=24)
    t(0.04, 0.06, 'Citizen science from a home computer · candidates, not discoveries', color='#8a99a9', fontsize=12)
    fig.savefig(path, dpi=100, facecolor=fig.get_facecolor())
    plt.close(fig)


def build(site, data, cfg, files_root, title, log=print):
    """Write all the website extras into the folder that is about to be published."""
    site = Path(site)
    idx = site / 'index.html'
    if idx.is_file():
        idx.write_text(inject_head(idx.read_text(), head_tags(data, cfg, title)))
    url = (data.get('site') or {}).get('url') or ''
    (site / 'c').mkdir(exist_ok=True)
    for c in (data.get('tess') or {}).get('cands', []):
        (site / 'c' / f"{int(c['tic'])}.html").write_text(share_page(c, url))
    (site / 'feed.xml').write_text(feed(data))
    (site / 'events.ics').write_text(ics((data.get('beyond') or {}).get('events'), url), newline='')
    (site / 'sitemap.xml').write_text(sitemap(data))
    (site / 'robots.txt').write_text('User-agent: *\nAllow: /\n' + (f'Sitemap: {url}sitemap.xml\n' if url else ''))
    (site / 'manifest.webmanifest').write_text(manifest())
    for name, fn in (('icon-192.png', lambda p: icon(p, 192)), ('icon-512.png', lambda p: icon(p, 512)),
                     ('og.png', lambda p: og_image(p, data, site))):
        try:
            fn(site / name)
        except Exception as ex:
            log(f'  {name} not made: {ex}')
