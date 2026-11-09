"""Local AI catch-up: runs when nobody has used the Mac for a few minutes (see Setup -> "Run the local AI when idle").

1. Gives every TESS candidate still "awaiting AI analysis" its first look from the local vision model.
2. Refreshes the space-science radar if it is more than 90 minutes old (so the local AI reads the news more often).
Stops early as soon as someone uses the Mac again, so it never slows you down.
"""
import json
import time

from . import db, idle


def busy_again(cfg):
    s = idle.user_idle_seconds()
    return s is not None and s < 60


def run(ctx):
    con, log, cfg = ctx.con, ctx.log, ctx.cfg
    from .llm import LLM
    from .tess_hunter import vision_check
    from . import config
    llm = LLM(cfg, log)
    if not llm.models():
        return 'no local AI model is running (is Ollama open?)'
    looked = 0
    rows = db.rows(con, "SELECT * FROM tess_cands WHERE status IN ('new','unsure') AND ai IS NULL AND plot IS NOT NULL ORDER BY snr DESC LIMIT 40")
    for c in rows:
        if busy_again(cfg):
            log('someone is using the Mac again: stopping here')
            return f'gave {looked} TESS candidates their AI first look, then stopped (the Mac is in use)'
        if ctx.time_left() < 60:
            break
        png = config.DATA / c['plot']
        if not png.is_file():
            continue
        m = json.loads(c['metrics'] or '{}')
        if c['kind'] == 'periodic':
            facts = (f"period {c['period']:.4f} d, depth {c['depth'] * 1e6:.0f} ppm, duration {c['duration'] * 24:.1f} h, SNR {c['snr']:.1f}, "
                     f"~{c['rp'] or 0:.1f} Earth radii, odd/even difference {m.get('odd_even_sigma', '?')} sigma, half-orbit dip {m.get('secondary_sigma', '?')} sigma.")
            ai = vision_check(llm, str(png), facts)
        else:
            ai = vision_check(llm, str(png), f"single dip: depth {c['depth'] * 1e6:.0f} ppm, duration {c['duration'] * 24:.1f} h, SNR {c['snr']:.1f}", kind='single')
        if ai:
            con.execute('UPDATE tess_cands SET ai=? WHERE id=?', (json.dumps(ai), c['id']))
            looked += 1
            log(f"  TIC {c['tic']}: {ai['verdict']} ({ai['confidence']})")
    note = f'gave {looked} TESS candidates their AI first look' if rows else 'every TESS candidate already has an AI first look'
    last = con.execute("SELECT MAX(started) FROM runs WHERE job='radar'").fetchone()[0] or 0
    if time.time() - last > 90 * 60 and not busy_again(cfg) and ctx.time_left() > 300:
        from . import radar
        log('refreshing the space-science radar')
        note += '; radar: ' + radar.run(ctx)
    return note
