"""NEO confirmation watch (every 2 hours).

1. Reads JPL Scout's assessment of every object on the Minor Planet Center's NEO Confirmation Page (NEOCP):
   how likely each is to be near-Earth or hazardous. New possibly hazardous ones trigger a private Discord alert.
2. Finds near-Earth asteroids announced in the last days (JPL Small-Body Database) and runs a fast precovery
   check on them in old Pan-STARRS images, before most people get to it. Honest limit: a brand-new object's orbit
   is usually too uncertain to point at 2010-2014 images, so most checks end with "orbit too uncertain yet";
   Space Lab re-checks them on later runs as their orbits improve.
"""
import json
import time
from datetime import datetime, timedelta, timezone

from . import config, db, net, notify, precovery

SCOUT = 'https://ssd-api.jpl.nasa.gov/scout.api'
SBDB_QUERY = 'https://ssd-api.jpl.nasa.gov/sbdb_query.api'
SBDB = 'https://ssd-api.jpl.nasa.gov/sbdb.api'


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def watch_neocp(ctx):
    con, log, nc = ctx.con, ctx.log, ctx.cfg['neocp']
    js = net.fetch_json(SCOUT, timeout=60)
    data = js.get('data') or []
    now = time.time()
    seen = set()
    alerts = []
    first_run = con.execute('SELECT COUNT(*) FROM neocp').fetchone()[0] == 0   # don't alert on what was already there
    for r in data:
        name = r.get('objectName')
        if not name:
            continue
        seen.add(name)
        old = db.one(con, 'SELECT * FROM neocp WHERE name=?', (name,))
        con.execute('INSERT INTO neocp(name, first, last, data) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET '
                    'last=excluded.last, data=excluded.data, gone=NULL', (name, now, now, json.dumps(r)))
        notable = (_f(r.get('phaScore')) or 0) >= nc['alert_pha_score'] or (_f(r.get('rating')) or 0) >= 1
        if notable and not (old and old['alerted']):
            if not first_run:
                alerts.append(r)
            con.execute('UPDATE neocp SET alerted=1 WHERE name=?', (name,))
    con.execute('UPDATE neocp SET gone=? WHERE gone IS NULL AND name NOT IN (%s)' % ','.join('?' * len(seen)) if seen else
                'UPDATE neocp SET gone=? WHERE gone IS NULL', (now, *seen))
    con.execute('DELETE FROM neocp WHERE gone IS NOT NULL AND gone < ?', (now - 30 * 86400,))
    db.kv_set(con, 'neocp_snapshot', {'t': now, 'n': len(data)})
    log(f'NEO confirmation page: {len(data)} objects, {len(alerts)} new possibly hazardous ones')
    cfg = ctx.cfg
    if alerts and notify.valid(cfg.get('discord_webhook')) and (cfg.get('discord_alerts') or {}).get('tasks', True):
        lines = ['🛰️ **New possibly hazardous objects on the MPC confirmation page** (JPL Scout)']
        for r in alerts[:8]:
            lines.append(f"• {r.get('objectName')}: NEO score {r.get('neoScore')}, hazardous score {r.get('phaScore')}, "
                         f"H {r.get('H')}, {r.get('nObs')} observations over {r.get('arc')} h")
        lines.append('Space Lab will check old images once the object is confirmed and announced.')
        notify.send('\n'.join(lines), cfg['discord_webhook'])
    return len(data), len(alerts)


def new_neos(ctx):
    """Near-Earth asteroids first observed in the last N days, from JPL's Small-Body Database."""
    con, log, nc = ctx.con, ctx.log, ctx.cfg['neocp']
    since = (datetime.now(timezone.utc) - timedelta(days=int(nc['new_days']))).strftime('%Y-%m-%d')
    params = {'fields': 'pdes,full_name,H,data_arc,condition_code,first_obs,last_obs,pha,n_obs_used',
              'sb-group': 'neo', 'sb-kind': 'a', 'sb-cdata': json.dumps({'AND': [f'first_obs|GE|{since}']})}
    js = net.fetch_json(SBDB_QUERY, params=params, timeout=90)
    fields = js.get('fields') or []
    rows = [dict(zip(fields, r)) for r in js.get('data') or []]
    added = 0
    for r in rows:
        des = (r.get('pdes') or '').strip()
        if not des:
            continue
        arc = _f(r.get('data_arc'))
        cur = con.execute("""INSERT INTO pc_targets(des,fullname,h,arc,cc,last_obs,status,origin) VALUES(?,?,?,?,?,?,?,?)
                          ON CONFLICT(des) DO UPDATE SET h=excluded.h, arc=excluded.arc, cc=excluded.cc, last_obs=excluded.last_obs,
                          origin=COALESCE(pc_targets.origin, excluded.origin)""",
                          (des, (r.get('full_name') or des).strip(), _f(r.get('H')), arc, str(r.get('condition_code') or ''),
                           r.get('last_obs'), 'ready', 'new-neo'))
        added += cur.rowcount
    log(f'{len(rows)} near-Earth asteroids announced since {since}')
    return rows


def run(ctx):
    con, log, nc = ctx.con, ctx.log, ctx.cfg['neocp']
    msg = []
    try:
        n, a = watch_neocp(ctx)
        msg.append(f'{n} objects on the confirmation page ({a} new possibly hazardous)')
    except Exception as e:
        log(f'Scout unavailable: {e}')
        msg.append('confirmation page unavailable')
    try:
        rows = new_neos(ctx)
    except Exception as e:
        log(f'SBDB query failed: {e}')
        rows = []
    # fast precovery check: newest first, re-check objects whose arc grew since the last check
    cands = db.rows(con, "SELECT * FROM pc_targets WHERE origin='new-neo' AND (searched IS NULL OR searched < ?) "
                         "ORDER BY h ASC LIMIT ?", (time.time() - 2 * 86400, int(nc['max_searches'])))
    found = searched = 0
    for t in cands:
        if ctx.time_left() < 240:
            break
        try:
            nimg, ncand = precovery.search_target(ctx, t)
            searched += 1
            found += ncand
        except Exception as e:
            log(f'{t["des"]}: {type(e).__name__}: {e}')
            con.execute('UPDATE pc_targets SET note=?, searched=? WHERE des=?', (f'error: {e}'[:200], time.time(), t['des']))
    msg.append(f'{len(rows)} new NEOs in the last {nc["new_days"]} days; {searched} checked in old images; {found} possible sightings')
    return '; '.join(msg)
