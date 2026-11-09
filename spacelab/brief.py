"""Morning brief: one short paragraph from your local model about what happened overnight."""
import time

from . import db
from .llm import LLM


def facts(con):
    out = []
    tasks = db.rows(con, "SELECT project, COUNT(*) AS n FROM tasks WHERE status='open' GROUP BY project")
    if tasks:
        out.append('Open tasks: ' + ', '.join(f"{t['n']} {t['project']}" for t in tasks))
    since = time.time() - 26 * 3600
    for r in db.rows(con, 'SELECT job, status, summary FROM runs WHERE started > ? ORDER BY started', (since,)):
        out.append(f"{r['job']}: {r['status']} — {r['summary']}")
    today = time.strftime('%Y-%m-%d')
    top = db.rows(con, 'SELECT title FROM arxiv WHERE digest=? ORDER BY rank LIMIT 3', (today,))
    if top:
        out.append('Top papers today: ' + ' | '.join(t['title'] for t in top))
    w = db.kv_get(con, 'neo_watch_text')
    if w and w.get('facts'):
        out.append('Asteroid watch: ' + ' | '.join(w['facts'][:3]))
    return out


def run(ctx):
    con = ctx.con
    f = facts(con)
    llm = LLM(ctx.cfg, ctx.log)
    text = llm.chat('Write a friendly morning brief (4 short bullet points, plain English, start each with an emoji) for a '
                    'space hobbyist about what his computer did overnight and what is waiting for him. Only use these facts:\n\n- '
                    + '\n- '.join(f), system='You are a concise assistant. Never invent results.', max_tokens=260)
    if not text:
        text = '\n'.join('• ' + x for x in f) or 'Nothing ran overnight yet.'
    db.kv_set(con, 'brief', {'t': time.time(), 'text': text, 'model': llm.used[-1] if llm.used else None})
    return 'brief written' + (f' by {llm.used[-1]}' if llm.used else ' (no AI available, plain list)')
