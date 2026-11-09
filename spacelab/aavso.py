"""AAVSO Exoplanet Database upload helper: checks the report EXOTIC wrote, fixes what it safely can
(your observer code), and lists the exact upload steps."""
import re
from pathlib import Path

SUBMIT_URL = 'https://aavso.org/apps/exosite/submit'
SITE_EQUIP_URL = 'https://aavso.org/apps/exosite/'
REQUIRED = ['TYPE', 'OBSCODE', 'SOFTWARE', 'DELIM', 'DATE_TYPE', 'OBSTYPE', 'STAR_NAME', 'EXOPLANET_NAME', 'BINNING',
            'EXPOSURE_TIME', 'FILTER', 'DETREND_PARAMETERS', 'MEASUREMENT_TYPE']


def parse(text):
    head, data = {}, []
    for line in text.splitlines():
        if line.startswith('#'):
            m = re.match(r'#([A-Z0-9_\-]+)=(.*)', line.strip())
            if m:
                head.setdefault(m.group(1), m.group(2).strip())
        elif line.strip():
            data.append(line.strip())
    return head, data


def check(path, cfg, source='mobs'):
    p = Path(path or '')
    if not p.is_file():
        return [{'level': 'error', 'msg': 'AAVSO file not found (re-run the dataset)'}]
    head, data = parse(p.read_text(errors='ignore'))
    out = []
    add = lambda level, msg: out.append({'level': level, 'msg': msg})
    for k in REQUIRED:
        if k not in head:
            add('error', f'#{k} line is missing')
    if head.get('TYPE', '').upper() != 'EXOPLANET':
        add('error', '#TYPE must be EXOPLANET')
    code = head.get('OBSCODE', '')
    if not code:
        add('error' if not cfg.get('aavso_code') else 'warn',
            '#OBSCODE is empty' + (': click "Add my observer code" to fix it' if cfg.get('aavso_code') else ': add your AAVSO observer code in Setup first'))
    elif cfg.get('aavso_code') and code != cfg['aavso_code']:
        add('warn', f'#OBSCODE is {code}, but Setup says {cfg["aavso_code"]}')
    else:
        add('ok', f'observer code {code}')
    if head.get('DATE_TYPE', '').upper() not in ('BJD_TDB', 'JD_UTC', 'HJD_UTC'):
        add('warn', f'unusual #DATE_TYPE {head.get("DATE_TYPE")}')
    delim = head.get('DELIM', ',')
    delim = {'comma': ',', 'tab': '\t'}.get(delim.lower(), delim)
    rows = []
    for ln in data:
        parts = [x.strip() for x in ln.split(delim)]
        try:
            rows.append([float(x) for x in parts[:3]])
        except ValueError:
            continue   # the column-name line
    if len(rows) < 20:
        add('error', f'only {len(rows)} data rows')
    else:
        ts = [r[0] for r in rows]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            add('warn', 'times are not strictly increasing')
        if any(r[2] <= 0 for r in rows if len(r) > 2):
            add('error', 'some error values are zero or negative')
        else:
            add('ok', f'{len(rows)} measurements over {(ts[-1] - ts[0]) * 24:.1f} hours')
    if 'RESULTS' not in head:
        add('warn', 'no #RESULTS line (fit results); the upload still works')
    if source == 'scope':
        add('ok', 'your own telescope: make sure your Site & Equipment profile on AAVSO matches it (one-time)')
    else:
        add('ok', 'MicroObservatory data: add the MicroObservatory telescope to your AAVSO Site & Equipment profile (one-time)')
    return out


def fix(path, cfg):
    """Fill an empty #OBSCODE with the code from Setup. Returns True if the file changed."""
    p = Path(path)
    code = (cfg.get('aavso_code') or '').strip()
    if not code or not p.is_file():
        return False
    text = p.read_text(errors='ignore')
    new = re.sub(r'^#OBSCODE=\s*$', f'#OBSCODE={code}', text, flags=re.M)
    if '#OBSCODE=' not in new:
        new = new.replace('#TYPE=EXOPLANET', f'#TYPE=EXOPLANET\n#OBSCODE={code}', 1)
    if new != text:
        p.write_text(new)
        return True
    return False


def steps(ds):
    return [
        f'Log in at aavso.org, then open the Exoplanet Database submit page: {SUBMIT_URL}',
        'Pick your site and equipment profile (one-time setup: Site & Equipment link on that page).',
        'Upload the AAVSO file (button above) and, as the supporting image, the star-field picture with the marked stars.',
        'Accept the terms and submit. It appears on the Exoplanet Watch results page within a few days.',
    ]
