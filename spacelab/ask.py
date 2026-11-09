"""Ask Space Lab: answer questions about your own results with the local model (Gemma 4) and bge-m3.

Builds a compact, factual briefing from the database (TESS signals, asteroid watch, Exoplanet Watch results,
timing targets, recent runs) plus the papers closest in meaning to the question, then asks the model to answer
only from that briefing, citing items. Nothing leaves your Mac."""
import json
import re
import time

import numpy as np

from . import classifier, config, db
from .llm import LLM

SYSTEM = ('You are Space Lab, a careful astronomy assistant on the user\'s own computer. Answer ONLY from the records '
          'given. If the records do not contain the answer, say so plainly and suggest what to run or check. Cite items '
          'inline like [TIC 12345], [paper 2411.01234], [2024 YR4], [WASP-12 b]. Use plain English, short paragraphs or '
          'bullets, no invented numbers.')


def _j(x):
    try:
        return json.loads(x) if isinstance(x, str) else x
    except Exception:
        return None


def _ago(t):
    if not t:
        return 'never'
    d = (time.time() - t) / 86400
    return f'{d * 24:.0f} h ago' if d < 2 else f'{d:.0f} days ago'


def paper_hits(con, llm, question, k=5):
    rows = db.rows(con, 'SELECT a.id, a.title, a.summary, a.abstract, a.published, e.v FROM arxiv a '
                        'LEFT JOIN arxiv_emb e ON e.id = a.id ORDER BY a.added DESC LIMIT 1500')
    if not rows:
        return []
    missing = [r for r in rows if r['v'] is None][:120]
    if missing:
        vecs = llm.embed([(r['title'] + '. ' + (r['abstract'] or ''))[:2000] for r in missing]) or []
        for r, v in zip(missing, vecs):
            r['v'] = np.asarray(v, dtype=np.float32).tobytes()
            con.execute('INSERT OR REPLACE INTO arxiv_emb(id, v) VALUES(?,?)', (r['id'], r['v']))
    qv = llm.embed([question])
    if not qv:
        words = set(re.findall(r'[a-z0-9]{4,}', question.lower()))
        scored = [(len(words & set(re.findall(r'[a-z0-9]{4,}', (r['title'] + ' ' + (r['abstract'] or '')).lower()))), r) for r in rows]
    else:
        q = np.asarray(qv[0], dtype=np.float32)
        q /= np.linalg.norm(q) or 1
        scored = []
        for r in rows:
            if r['v'] is None:
                continue
            v = np.frombuffer(r['v'], dtype=np.float32)
            scored.append((float(v @ q / (np.linalg.norm(v) or 1)), r))
    scored.sort(key=lambda x: -x[0])
    return [r for s, r in scored[:k] if s > 0]


def briefing(con, cfg, question, llm):
    out = []
    from .jobs import JOBS
    runs = []
    for job, (_, name) in JOBS.items():
        r = db.one(con, 'SELECT started, status, summary FROM runs WHERE job=? ORDER BY started DESC LIMIT 1', (job,))
        if r:
            runs.append(f'- {name}: {r["status"]}, {_ago(r["started"])}: {(r["summary"] or "")[:200]}')
    out.append('LATEST RUNS\n' + '\n'.join(runs))
    st = {r['status']: r['n'] for r in db.rows(con, 'SELECT status, COUNT(*) AS n FROM tess_targets GROUP BY status')}
    model = db.kv_get(con, 'clf')
    lines = [f'TESS: {st.get("done", 0)} stars checked, {st.get("queued", 0)} waiting, {st.get("nodata", 0)} without data.']
    for c in db.rows(con, 'SELECT * FROM tess_cands ORDER BY created DESC LIMIT 25'):
        ai = _j(c.get('ai')) or {}
        cen = _j(c.get('centroid')) or {}
        sc = classifier.score(model, c)
        per = f'every {c["period"]:.4f} d' if c['period'] else 'one lone dip'
        lines.append(f'- [TIC {c["tic"]}] {per}, depth {c["depth"] * 1e6:.0f} ppm, ~{(c["rp"] or 0):.1f} Earth radii, SNR {c["snr"] or 0:.1f}, '
                     f'status {c["status"]}, found {_ago(c["created"])}; flags: {"; ".join(_j(c["flags"]) or []) or "none"}'
                     + (f'; known as {c["known"]}' if c.get('known') else '')
                     + (f'; AI first look: {ai.get("verdict")} ({ai.get("confidence")})' if ai else '')
                     + (f'; centroid: {cen.get("verdict")} ({cen.get("offset_arcsec")}″)' if cen else '')
                     + (f'; TRICERATOPS FPP {(_j(c.get("fpp")) or {}).get("fpp")}' if c.get('fpp') else '')
                     + (f'; your model: {sc:.0%} planet-like' if sc is not None else ''))
    out.append('TESS PLANET HUNTER\n' + '\n'.join(lines))
    w = db.kv_get(con, 'neo_watch') or {}
    txt = (db.kv_get(con, 'neo_watch_text') or {}).get('text') or ''
    lines = [f'Summary: {txt}'] if txt else []
    for r in (w.get('cad') or [])[:6]:
        lines.append(f'- close approach: [{r.get("des")}] {r.get("cd")}, {float(r.get("dist") or 0) / 0.00256955529:.1f} lunar distances, H {r.get("h")}')
    for r in (w.get('sentry') or [])[:6]:
        lines.append(f'- Sentry risk list: [{r.get("fullname") or r.get("des")}] impact chance {r.get("ip")}, Palermo {r.get("ps")}, Torino {r.get("ts_max")}')
    for r in db.rows(con, 'SELECT name, first, data FROM neocp WHERE gone IS NULL ORDER BY first DESC LIMIT 8'):
        d = _j(r['data']) or {}
        lines.append(f'- NEO confirmation page: [{r["name"]}] NEO score {d.get("neoScore")}, PHA score {d.get("phaScore")}, H {d.get("H")}, seen {_ago(r["first"])}')
    for r in db.rows(con, 'SELECT des, night, status, offset FROM pc_cands ORDER BY created DESC LIMIT 10'):
        lines.append(f'- precovery sighting: [{r["des"]}] night {r["night"]}, {r["offset"]:.0f}″ from prediction, status {r["status"]}')
    for r in db.rows(con, 'SELECT des, arc, searched, nimg, note FROM pc_targets WHERE searched IS NOT NULL ORDER BY searched DESC LIMIT 8'):
        lines.append(f'- precovery search: [{r["des"]}] arc {r["arc"]} d, {r["nimg"]} images, {_ago(r["searched"])}: {(r["note"] or "")[:120]}')
    out.append('ASTEROIDS\n' + '\n'.join(lines))
    lines = []
    for d in db.rows(con, 'SELECT name, planet, obs_date, status, result FROM exo ORDER BY created DESC LIMIT 10'):
        p = _j(d['planet']) or {}
        r = _j(d['result']) or {}
        oc = r.get('oc') or {}
        lines.append(f'- [{p.get("pl_name") if isinstance(p, dict) else d["planet"]}] observed {d["obs_date"]}, status {d["status"]}'
                     + (f', mid-transit ±{(r.get("tmid_err") or 0) * 1440:.1f} min, O-C {oc.get("oc_min")} min' if r else ''))
    eph = db.kv_get(con, 'ephem') or {}
    for t in (eph.get('stale') or [])[:6]:
        lines.append(f'- timing target: [{t["name"]}] predicted transit time uncertain by ±{t["sigma_min"]:.0f} min, V {t.get("vmag")}, depth {t.get("depth_ppt")} ppt')
    out.append('EXOPLANET WATCH + TIMING TARGETS\n' + ('\n'.join(lines) or 'none yet'))
    hits = paper_hits(con, llm, question)
    if hits:
        out.append('PAPERS CLOSEST TO THE QUESTION\n' + '\n'.join(
            f'- [paper {p["id"]}] {p["title"]} ({p["published"]}): {(p.get("summary") or p.get("abstract") or "")[:400]}' for p in hits))
    text = '\n\n'.join(out)
    return text[:26000]


def answer(question, cfg=None):
    cfg = cfg or config.load()
    question = (question or '').strip()[:500]
    if not question:
        return {'error': 'empty question'}
    con = db.connect()
    llm = LLM(cfg, lambda *a: None)
    t0 = time.time()
    ctx = briefing(con, cfg, question, llm)
    reply = llm.chat(f'RECORDS FROM SPACE LAB (as of {time.strftime("%Y-%m-%d %H:%M")}):\n\n{ctx}\n\nQUESTION: {question}',
                     system=SYSTEM, max_tokens=700, temperature=0.2, num_ctx=16384)
    if not reply:
        return {'error': 'No local model answered. Is Ollama running?'}
    return {'answer': reply, 'model': (llm.used or ['?'])[-1], 'seconds': round(time.time() - t0, 1)}
