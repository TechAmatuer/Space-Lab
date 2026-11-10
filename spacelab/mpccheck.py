"""Checks on a draft precovery report (ADES PSV) before you send it to the Minor Planet Center.

Format: required header blocks and columns, sane times/positions, a real name instead of the placeholder.
Science: at least two positions per night, positions close to JPL's prediction, plausible motion between them.
Duplicates: asks the MPC whether Pan-STARRS already reported this object at those times (then it is not new).
"""
import json
import math
import re
import urllib.request
from datetime import datetime, timedelta

from . import net

MPC_OBS_API = 'https://data.minorplanetcenter.net/api/get-obs'
CATALOGS = {'Gaia1', 'Gaia2', 'Gaia3', 'Gaia3E', 'UCAC4', 'PPMXL', 'ATLAS2', 'PS1_DR1', 'PS1_DR2', 'URAT1', '2MASS'}


def parse_psv(text):
    head, rows, cols = [], [], None
    for line in (text or '').splitlines():
        if not line.strip():
            continue
        if line.startswith(('#', '!')):
            head.append(line.strip())
            continue
        parts = [x.strip() for x in line.split('|')]
        if cols is None:
            cols = parts
        else:
            rows.append(dict(zip(cols, parts)))
    return head, cols or [], rows


def _t(s):
    try:
        return datetime.strptime(s.replace('Z', ''), '%Y-%m-%dT%H:%M:%S.%f')
    except ValueError:
        try:
            return datetime.strptime(s.replace('Z', ''), '%Y-%m-%dT%H:%M:%S')
        except ValueError:
            return None


def check(text, cand=None, cfg=None, online=True):
    """Return a list of {'level': 'ok'|'warn'|'error', 'msg': ...}."""
    out = []
    add = lambda level, msg: out.append({'level': level, 'msg': msg})
    cfg = cfg or {}
    head, cols, rows = parse_psv(text)
    h = '\n'.join(head)
    for block in ('# version=2017', '# observatory', '# submitter', '# observers', '# measurers', '# telescope'):
        if block not in h:
            add('error', f'header is missing "{block}"')
    if 'YOUR NAME' in h or re.search(r'! name\s*$', h, re.M):
        add('error', 'your name is missing in the submitter/measurer lines (add it in Setup, then re-download)')
    m = re.search(r'! mpcCode (\S+)', h)
    code = m.group(1) if m else None
    for c in ('mode', 'stn', 'obsTime', 'ra', 'dec', 'astCat'):
        if c not in cols:
            add('error', f'column "{c}" is missing')
    if not ({'permID', 'provID', 'trkSub'} & set(cols)):
        add('error', 'needs a permID, provID or trkSub column')
    if not rows:
        add('error', 'no observations in the file')
        return out
    times = []
    for i, r in enumerate(rows, 1):
        t = _t(r.get('obsTime', ''))
        if not t or not r.get('obsTime', '').endswith('Z'):
            add('error', f'row {i}: obsTime must look like 2012-03-04T05:06:07.890Z')
        times.append(t)
        try:
            ra, dec = float(r.get('ra')), float(r.get('dec'))
            if not (0 <= ra < 360 and -90 <= dec <= 90):
                add('error', f'row {i}: position out of range')
        except (TypeError, ValueError):
            add('error', f'row {i}: ra/dec are not numbers')
        if code and r.get('stn') != code:
            add('error', f'row {i}: station {r.get("stn")} differs from the header observatory {code}')
        if r.get('astCat') not in CATALOGS:
            add('warn', f'row {i}: astrometric catalog "{r.get("astCat")}" is unusual; Pan-STARRS DR2 images are tied to Gaia (use the code the MPC/SARC tells you)')
        if not r.get('provID') and not r.get('permID'):
            add('error', f'row {i}: no designation')
    if not out or all(o['level'] != 'error' for o in out):
        add('ok', 'format: header and columns look complete')
    good = [t for t in times if t]
    plates = bool(rows) and all(r.get('mode') == 'PHO' for r in rows)
    if plates and len(good) == 1:
        add('ok', 'one position from one photographic plate: normal for archival plates of a known object (the MPC\'s SARC reviews these)')
    elif len(good) < 2:
        add('error', 'only one position: the MPC needs at least two positions from the same night (a tracklet)')
    else:
        span_h = (max(good) - min(good)).total_seconds() / 3600
        if span_h > 12:
            add('warn', f'positions span {span_h:.1f} h: make sure they are from the same night')
        else:
            add('ok', f'{len(good)} positions spanning {span_h * 60:.0f} minutes on one night')
    if cand:
        off = cand.get('offset')
        if off is not None:
            if off > 60:
                add('warn', f'the detection is {off:.0f}″ from JPL\'s prediction: plausible for an uncertain orbit, but double-check the blink')
            else:
                add('ok', f'{off:.0f}″ from JPL\'s predicted position')
        obs = cand.get('obs') if isinstance(cand.get('obs'), list) else []
        if len(obs) >= 2:
            a, b = obs[0], obs[-1]
            ta, tb = _t(a.get('obsTime', '')), _t(b.get('obsTime', ''))
            if ta and tb and tb != ta:
                dra = (b['ra'] - a['ra']) * math.cos(math.radians(a['dec'])) * 3600
                ddec = (b['dec'] - a['dec']) * 3600
                rate = math.hypot(dra, ddec) / abs((tb - ta).total_seconds() / 3600)
                if rate < 0.5:
                    add('warn', f'it barely moved ({rate:.2f}″/hour): could be a stationary star or galaxy')
                elif rate > 3600:
                    add('warn', f'very fast motion ({rate:.0f}″/hour): check for an artifact')
                else:
                    add('ok', f'moves {rate:.1f}″ per hour between the images')
    if not cfg.get('sarc_verified'):
        add('error', 'not yet SARC-verified: the MPC will not accept archival (precovery) data until its committee verifies you (see the task on Today)')
    if online:
        try:
            tol = 1.6 * 86400 if plates else 60
            dup = duplicates(rows, tol)
            if dup is None:
                add('warn', 'could not ask the MPC about duplicates right now')
            elif dup:
                add('error', f'the MPC already has {dup} observation(s) from {code or "this station"} ' +
                    ('within a day and a half of this plate: someone has probably measured the same plate already' if plates
                     else 'within a minute of yours: likely already reported'))
            else:
                add('ok', 'the MPC has no observations from this station at these times: looks new')
        except Exception as e:
            add('warn', f'duplicate check failed: {e}')
    return out


def duplicates(rows, tol_s=60):
    des = rows[0].get('permID') or rows[0].get('provID')
    stn = rows[0].get('stn')
    if not des:
        return None
    body = json.dumps({'desigs': [des], 'output_format': ['OBS80']}).encode()
    req = urllib.request.Request(MPC_OBS_API, data=body, method='GET',
                                 headers={'Content-Type': 'application/json', 'User-Agent': net.UA})
    with urllib.request.urlopen(req, timeout=60, context=net.ssl_ctx()) as r:
        js = json.loads(r.read())
    lines = []
    for item in js if isinstance(js, list) else [js]:
        v = item.get('OBS80') if isinstance(item, dict) else None
        if isinstance(v, str):
            lines += v.splitlines()
        elif isinstance(v, list):
            lines += v
    mine = [t for t in (_t(r.get('obsTime', '')) for r in rows) if t]
    n = 0
    for ln in lines:
        from .plates import same_site
        if len(ln) < 80 or not same_site(ln[77:80], stn):
            continue
        try:
            y, mo, d = int(ln[15:19]), int(ln[20:22]), float(ln[23:32])
            day = int(d)
            t = datetime(y, mo, day) + timedelta(days=d - day)
        except ValueError:
            continue
        if any(abs((t - m).total_seconds()) < tol_s for m in mine):
            n += 1
    return n
