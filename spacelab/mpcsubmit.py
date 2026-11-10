"""Sending a checked ADES report to the Minor Planet Center, only when you press the button.

Two steps, both started by you on the local Space Lab page:
  1. Test: the MPC's test address checks the file with its own validator and stores nothing.
  2. Send: allowed only after a passing test of exactly the same file in the last 7 days, after you marked the sighting
     real, with your email in Setup (the MPC sends its acknowledgement there) and, for archival data, after the MPC's
     SARC committee has verified you.
Nothing here runs on a schedule.
"""
import hashlib
import json
import re
import time
import urllib.request
import uuid

from . import mpccheck, net

URL = {'test': 'https://minorplanetcenter.net/submit_psv_test', 'real': 'https://minorplanetcenter.net/submit_psv'}
OBJ_TYPES = {'neo', 'tno', 'comet', 'unclassified'}
EMAIL = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def digest(text):
    return hashlib.sha256((text or '').encode()).hexdigest()[:16]


def multipart(fields):
    b = uuid.uuid4().hex
    parts = []
    for k, v in fields.items():
        parts.append(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n')
    return (''.join(parts) + f'--{b}--\r\n').encode('utf-8'), f'multipart/form-data; boundary={b}'


def post(url, fields, timeout=120):
    body, ctype = multipart(fields)
    req = urllib.request.Request(url, data=body, method='POST', headers={'Content-Type': ctype, 'User-Agent': net.UA})
    with urllib.request.urlopen(req, timeout=timeout, context=net.ssl_ctx()) as r:
        return r.read().decode('utf-8', 'replace')


def test_passed(text):
    t = (text or '').lower()
    return 'valid' in t and not re.search(r'\b(error|invalid|fail)', t)


def submission_id(text):
    m = re.search(r'submission id\s*(?:is|:)?\s*([A-Za-z0-9._:\-]+)', text or '', re.I)
    return m.group(1) if m else None


def blockers(c, cfg, checks, mode, state):
    """Reasons this report may not be sent now (empty list = allowed)."""
    out = []
    if c.get('status') != 'real':
        out.append('mark the sighting as real first (after looking at it yourself)')
    errs = [x['msg'] for x in checks or [] if x.get('level') == 'error'
            and not (mode == 'test' and 'SARC' in x['msg'])]          # the format test is fine before SARC verification
    out += [f'report check: {e}' for e in errs]
    if not EMAIL.match((cfg.get('contact_email') or '').strip()):
        out.append('add your email in Setup: the MPC sends its acknowledgement there')
    if mode == 'real':
        if not cfg.get('sarc_verified'):
            out.append('the MPC must verify you for archival reports first (SARC); tick it in Setup once they have')
        t = (state or {}).get('test') or {}
        if not (t.get('ok') and t.get('hash') == digest(c.get('ades')) and time.time() - (t.get('t') or 0) < 7 * 86400):
            out.append('run the MPC test on this exact file first (it must pass, within the last 7 days)')
        if (state or {}).get('sent'):
            out.append(f"already sent ({(state['sent'].get('id') or 'see the acknowledgement email')})")
    return out


def submit(c, cfg, mode, online=True):
    """mode: 'test' or 'real'. Returns (http_code, result dict, new state for the pc_cands.mpc column)."""
    state = json.loads(c.get('mpc') or '{}') if isinstance(c.get('mpc'), str) else (c.get('mpc') or {})
    checks = mpccheck.check(c.get('ades') or '', c, cfg, online=online)
    why = blockers(c, cfg, checks, mode, state)
    if why:
        return 409, {'ok': False, 'blocked': why, 'checks': checks}, state
    obs = c.get('obs') if isinstance(c.get('obs'), list) else json.loads(c.get('obs') or '[]')
    otype = ((obs[0] if obs else {}).get('objtype') or 'unclassified').lower()
    fields = {'ack': f"Space Lab {c['des']} archival {c.get('archive') or ''}"[:80], 'ac2': cfg['contact_email'].strip(),
              'obj_type': otype if otype in OBJ_TYPES else 'unclassified', 'source': c['ades']}
    text = post(URL[mode], fields)
    now = time.time()
    if mode == 'test':
        state['test'] = {'t': now, 'ok': test_passed(text), 'hash': digest(c['ades']), 'text': text[:2000]}
        return 200, {'ok': state['test']['ok'], 'mode': 'test', 'text': text[:2000]}, state
    sid = submission_id(text)
    state['sent'] = {'t': now, 'id': sid, 'text': text[:2000], 'hash': digest(c['ades'])}
    return 200, {'ok': bool(sid), 'mode': 'real', 'id': sid, 'text': text[:2000]}, state
