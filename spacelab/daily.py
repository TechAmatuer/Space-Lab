"""Candidate of the day: everyone who visits gets the same TESS candidate to judge (like a daily puzzle).

* A new candidate is picked each day (UTC) from signals still waiting for review, never one picked in the last 60 days.
* The local AI writes two or three plain-English sentences about what to look for in it.
* Quick votes come from the public page's visitor counter (GoatCounter "events", no cookies, no personal data), if you
  set it up in Setup -> Sharing. Reviews posted on GitHub are counted too (spacelab/beyond.py). Yesterday's tally is
  shown the next day next to the AI's opinion and yours.
Runs from the radar job (every 6 hours) and the publish job.
"""
import random
import re
import time
from datetime import datetime, timedelta, timezone

from . import db, net

ANSWERS = {'planet-like': 'Planet-like', 'eclipsing-binary': 'Eclipsing binary (two stars)', 'nearby-star': 'Comes from a nearby star',
           'noise': 'Noise / instrument glitch', 'not-sure': 'Not sure'}
CODE_RE = re.compile(r'^[a-z0-9][a-z0-9-]{1,49}$')


def today():
    return datetime.now(timezone.utc).date().isoformat()


def summary(con, tic):
    """Everything the page needs about one candidate, so it still shows after its status changes."""
    c = db.one(con, "SELECT c.*, t.teff, t.rad AS srad, t.dist, t.tmag FROM tess_cands c LEFT JOIN tess_targets t ON t.tic=c.tic "
                    "WHERE c.tic=? ORDER BY c.created DESC LIMIT 1", (tic,))
    if not c:
        return None
    import json
    ai = json.loads(c['ai']) if c.get('ai') else {}
    fpp = json.loads(c['fpp']) if c.get('fpp') else {}
    return {'tic': c['tic'], 'id': c['id'], 'kind': c['kind'], 'period': c['period'], 't0': c['t0'], 'depth': c['depth'], 'duration': c['duration'],
            'rp': c['rp'], 'snr': c['snr'], 'plot': c['plot'], 'status': c['status'], 'flags': json.loads(c['flags'] or '[]'),
            'ai': ai.get('verdict'), 'fpp': fpp.get('verdict'),
            'star': {'teff': c.get('teff'), 'radius_sun': c.get('srad'), 'dist_pc': c.get('dist'), 'tmag': c.get('tmag')}}


def pick(con, date, recent_tics):
    rows = db.rows(con, "SELECT tic, snr FROM tess_cands WHERE kind='periodic' AND status IN ('new','unsure') AND plot IS NOT NULL "
                        "AND period IS NOT NULL ORDER BY snr DESC LIMIT 400")
    rows = [r for r in rows if r['tic'] not in recent_tics]
    if not rows:
        return None
    rng = random.Random(date)                      # same pick for the whole day, different every day
    return rng.choice(rows[:30])['tic']


def blurb(llm, c):
    star = c.get('star') or {}
    facts = (f"TESS star TIC {c['tic']}: brightness dips by {round((c['depth'] or 0) * 1e6)} parts per million every "
             f"{c['period']:.3f} days, each dip lasting {(c['duration'] or 0) * 24:.1f} hours. If it is a planet it is about "
             f"{c['rp'] or 0:.1f} times Earth's width. Star: {round(star['teff']) if star.get('teff') else '?'} K, "
             f"{round(star['dist_pc'] * 3.26) if star.get('dist_pc') else '?'} light-years away. Automatic warning flags: "
             f"{', '.join(c['flags']) or 'none'}. The local AI's first look: {c.get('ai') or 'not yet'}.")
    text = None
    if llm is not None:
        text = llm.chat('Write 2 or 3 short sentences for the public "candidate of the day" on a citizen-science website. Explain '
                        'in plain English what makes this signal interesting and the one thing a visitor should look at in the plots '
                        'to decide whether it is a planet. Do not call it a discovery or a planet; it is a candidate. Facts: ' + facts,
                        kind='writer', max_tokens=220, temperature=0.4)
    if not text:
        text = (f"This star dims a little every {c['period']:.2f} days. If something is passing in front of it, that something is "
                f"about {c['rp'] or 0:.1f} times Earth's width. Look at the bottom-left plot: a flat-bottomed U suits a planet, "
                f"a sharp V often means two stars.")
    return text.strip()


def quick_votes(cfg, date, log):
    """Count today's votes from GoatCounter events (path vote-<date>-<answer>). None when not set up."""
    sh = cfg.get('share') or {}
    code, token = str(sh.get('goatcounter') or '').strip().lower(), str(sh.get('goatcounter_token') or '').strip()
    if not code or not token or not CODE_RE.match(code):
        return None
    end = (datetime.fromisoformat(date) + timedelta(days=2)).date().isoformat()
    try:
        js = net.fetch_json(f'https://{code}.goatcounter.com/api/v0/stats/hits', params={'start': date, 'end': end, 'limit': 100},
                            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, timeout=40)
    except Exception as e:
        log(f'  visitor votes not read: {e}')
        return None
    out = {}
    for h in js.get('hits') or []:
        p = str(h.get('path') or '').lstrip('/')
        if p.startswith(f'vote-{date}-'):
            k = p[len(f'vote-{date}-'):]
            if k in ANSWERS:
                out[ANSWERS[k]] = out.get(ANSWERS[k], 0) + int(h.get('count') or 0)
    return out


def visitors(cfg, log, days=7):
    """Total visits in the last week from GoatCounter, for your own Today tab (never published)."""
    sh = cfg.get('share') or {}
    code, token = str(sh.get('goatcounter') or '').strip().lower(), str(sh.get('goatcounter_token') or '').strip()
    if not code or not token or not CODE_RE.match(code):
        return None
    start = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    try:
        js = net.fetch_json(f'https://{code}.goatcounter.com/api/v0/stats/total', params={'start': start},
                            headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'}, timeout=40)
        return {'days': days, 'visitors': js.get('total', js.get('total_utc')), 'events': js.get('total_events'), 't': time.time(),
                'url': f'https://{code}.goatcounter.com'}
    except Exception as e:
        log(f'  visitor count not read: {e}')
        return None


def ensure(con, cfg, log, llm=None):
    d = db.kv_get(con, 'daily') or {}
    hist = db.kv_get(con, 'daily_history') or []
    date = today()
    if d.get('date') != date:
        if d.get('tic'):
            hist = (hist + [{'date': d['date'], 'tic': d['tic']}])[-120:]
            prev = {'date': d['date'], 'tic': d['tic']}
        else:
            prev = d.get('prev')
        recent = {h['tic'] for h in hist[-60:]}
        tic = pick(con, date, recent)
        d = {'date': date, 'tic': tic, 'prev': prev, 'made': time.time()}
        if tic:
            c = summary(con, tic)
            d['blurb'] = blurb(llm, c) if c else None
            log(f'  candidate of the day: TIC {tic}')
        db.kv_set(con, 'daily_history', hist)
    votes = db.kv_get(con, 'daily_votes') or {}
    for day_ in {date, (d.get('prev') or {}).get('date')} - {None}:
        v = quick_votes(cfg, day_, log)
        if v is not None:
            votes[day_] = v
    db.kv_set(con, 'daily_votes', {k: v for k, v in votes.items() if k >= (datetime.now(timezone.utc) - timedelta(days=30)).date().isoformat()})
    vis = visitors(cfg, log)
    if vis:
        db.kv_set(con, 'visitors', vis)
    db.kv_set(con, 'daily', d)
    return d


VERDICT = {'real': 'planet-like', 'fake': 'not a planet', 'unsure': 'unsure', 'auto-rejected': 'rejected by the automatic checks'}


def public(con):
    """The candidate of the day as published in data.json (plot paths are mapped by publish)."""
    d = db.kv_get(con, 'daily') or {}
    if not d.get('tic'):
        return {}
    votes = db.kv_get(con, 'daily_votes') or {}
    comm = (db.kv_get(con, 'community') or {}).get('tess') or {}
    out = {'date': d['date'], 'cand': summary(con, d['tic']), 'blurb': d.get('blurb'), 'votes': votes.get(d['date']) or {}}
    p = d.get('prev')
    if p and p.get('tic'):
        c = summary(con, p['tic'])
        if c:
            out['prev'] = {'date': p['date'], 'cand': c, 'votes': votes.get(p['date']) or {}, 'github': comm.get(str(p['tic'])) or {},
                           'volunteer': VERDICT.get(c['status'], 'not reviewed yet')}
    return out
