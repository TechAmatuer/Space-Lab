"""Data export utilities. Download results as JSON or CSV for sharing, analysis, or backup."""
import csv
import json
import io
from datetime import datetime

from . import db


def _to_dict(row, keys):
    """Convert a database row to a dict."""
    return {k: v for k, v in zip(keys, row)}


def _clean_row(row, json_keys=('flags', 'metrics', 'ai', 'centroid')):
    """Clean a row dict for export: convert JSON fields, add human-readable helpers."""
    out = {}
    for k, v in row.items():
        if k in json_keys and isinstance(v, str):
            try:
                out[k] = json.loads(v)
            except (json.JSONDecodeError, ValueError):
                out[k] = None
        else:
            out[k] = v
    
    # Human-readable fields
    out['kind_readable'] = 'Periodic' if out.get('kind') == 'periodic' else 'Single dip'
    status_map = {'new': 'Under review', 'real': 'Looks like a planet', 
                  'fake': 'Not a planet', 'unsure': 'Unsure', 'auto-rejected': 'Auto-rejected'}
    out['status_readable'] = status_map.get(out.get('status'), str(out.get('status', '')))
    
    if out.get('rp') and out['rp'] > 0:
        out['planet_size_Earth'] = f"~{out['rp']:.1f}x Earth"
    if out.get('depth') is not None:
        out['depth_ppm'] = round(out['depth'] * 1e6)
    
    # Convert dicts back to strings for CSV compatibility
    for k in json_keys:
        if isinstance(out.get(k), dict):
            out[k] = json.dumps(out[k])
    return out


def eb_csv(con):
    """Rejected TESS signals that look like eclipsing binaries: useful for the AAVSO Variable Star Index (VSX)."""
    rows = con.execute("SELECT c.tic, c.period, c.t0, c.depth, c.duration, c.flags, c.ai, t.ra, t.dec, t.tmag "
                       "FROM tess_cands c LEFT JOIN tess_targets t ON t.tic=c.tic WHERE c.kind='periodic'").fetchall()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(['tic', 'ra_deg', 'dec_deg', 'tmag', 'period_days', 'epoch_bjd', 'depth_pct', 'duration_h', 'why'])
    n = 0
    for tic, period, t0, depth, dur, flags, ai, ra, dec, tmag in rows:
        fl = (flags or '').lower()
        aiv = (json.loads(ai).get('verdict') if ai else '') or ''
        why = [x for x in ('eclipsing binary' if 'eclipsing binary' in fl else '', 'odd/even depths differ' if 'odd and even' in fl else '',
                           'secondary eclipse' if 'secondary' in fl else '', 'AI: binary' if 'binary' in aiv.lower() else '') if x]
        if not why:
            continue
        n += 1
        w.writerow([tic, ra, dec, tmag, f'{period:.6f}' if period else '', f'{(t0 or 0) + 2457000:.5f}', f'{(depth or 0) * 100:.3f}',
                    f'{(dur or 0) * 24:.2f}', '; '.join(why)])
    return out.getvalue(), n


def tess_csv(con, fmt='csv'):
    """Export TESS candidates as CSV or JSON."""
    cands = con.execute('SELECT * FROM tess_cands ORDER BY created DESC').fetchall()
    if not cands:
        return 'No TESS candidates yet.', 'empty'
    
    keys = [k[0] for k in con.execute('SELECT * FROM tess_cands LIMIT 1').description]
    rows = [_clean_row(dict(zip(keys, c))) for c in cands]
    
    if fmt == 'json':
        return json.dumps(rows, indent=2), f'{len(cands)} TESS candidates'
    
    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=['id', 'tic', 'kind', 'status', 'depth_ppm', 
                                              'period_days', 'duration_h', 'rp', 'planet_size_Earth',
                                              'flags', 'metrics', 'ai', 'centroid'],
                              extrasaction='ignore')
    writer.writeheader()
    for row in rows:
        clean = {k: (str(v) if v is not None else '') for k, v in row.items()}
        # Format period for periodic candidates
        if clean.get('kind') == 'periodic' and row.get('period'):
            clean['period_days'] = f"{row['period']:.4f}"
        writer.writerow(clean)
    return out.getvalue(), f'{len(cands)} TESS candidates'


def neo_csv(con):
    """Export asteroid targets + precovery candidates as CSV."""
    targets = list(con.execute('SELECT * FROM pc_targets ORDER BY ps DESC LIMIT 100').fetchall())
    cands = list(con.execute('SELECT * FROM pc_cands ORDER BY created DESC LIMIT 100').fetchall())
    
    lines = ['# === Asteroid Targets (precovery watch) ===']
    for t in targets:
        d = dict(zip([k[0] for k in con.execute('SELECT * FROM pc_targets LIMIT 1').description], t))
        lines.append(f"{d.get('des', '')} | H={d.get('h')} | arc={d.get('arc')}d | Palermo={d.get('ps')}")
    
    lines.extend(['\n# === Precovery Candidates ==='])
    for c in cands:
        d = dict(zip([k[0] for k in con.execute('SELECT * FROM pc_cands LIMIT 1').description], c))
        lines.append(f"{d.get('des', '')} | Night={d.get('night')} | Offset={d.get('offset')}\"")
    
    return '\n'.join(lines), f'{len(targets)} targets + {len(cands)} candidates'


def exo_csv(con):
    """Export Exoplanet Watch results as CSV."""
    exo = list(con.execute('SELECT * FROM exo ORDER BY created DESC').fetchall())
    if not exo:
        return ('No Exoplanet Watch datasets yet.\n\n'
                'Check out a NASA Exoplanet Watch dataset from:\n'
                'https://science.nasa.gov/citizen-science/exoplanet-watch/how-to-contribute/data-checkout/'
                '\n\nOr upload your own telescope FITS files to SpaceLab/inbox/'), 'empty'
    
    lines = ['# === Exoplanet Watch Results ===']
    for d in exo:
        dd = dict(zip([k[0] for k in con.execute('SELECT * FROM exo LIMIT 1').description], d))
        lines.append(f"{dd.get('name', '')} | Planet={dd.get('planet')} | Status={dd.get('status')}"
                     f" | Images={dd.get('nimg')}")
    
    return '\n'.join(lines), f'{len(exo)} datasets exported'


def export_all(con, fmt='json'):
    """Export everything as a comprehensive JSON or CSV summary."""
    def count(table):
        return con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]
    
    data = {
        'export_date': datetime.now().isoformat(),
        'version': '1.0',
        'counts': {
            'tess_cands': count('tess_cands'),
            'tess_targets': count('tess_targets'),
            'neo_targets': count('pc_targets'),
            'neo_cands': count('pc_cands'),
            'exowatch': count('exo'),
        },
        'summary': {
            'new_tess': count_constrained(con, 'tess_cands', "status='new'"),
            'real_tess': count_constrained(con, 'tess_cands', "status='real'"),
            'auto_rejected': count_constrained(con, 'tess_cands', "status='auto-rejected'"),
        }
    }
    
    if fmt == 'json':
        return json.dumps(data, indent=2), f'Full export ({json.dumps(data["counts"], indent=2)})'
    
    lines = [f'# Space Lab Export - {data["export_date"]}', '']
    for k, v in data['counts'].items():
        lines.append(f'{k}: {v}')
    return '\n'.join(lines), 'Full export summary'


def run(ctx):
    """Export job: generate JSON + CSV of all pipeline data."""
    con, log = ctx.con, ctx.log
    try:
        from . import config
        cfg = config.load()
        
        # Export to JSON
        json_str, msg_json = export_all(con, fmt='json')
        
        # Export TESS to CSV
        csv_str, msg_csv = tess_csv(con)
        
        log(f'Export: {msg_json}')
        log(f'TESS CSV: {msg_csv}')
        
        # Write to data/exports/
        from pathlib import Path
        export_dir = config.DATA / 'exports'
        export_dir.mkdir(parents=True, exist_ok=True)
        
        ts = datetime.now().strftime('%Y%m%d_%H%M')
        (export_dir / f'space-lab-{ts}.json').write_text(json_str)
        (export_dir / f'tess-cands-{ts}.csv').write_text(csv_str)
        eb, n_eb = eb_csv(con)
        (export_dir / 'eclipsing-binary-candidates.csv').write_text(eb)
        log(f'eclipsing-binary list: {n_eb} stars (for the AAVSO Variable Star Index)')
        
        return f'Exported JSON + CSV to data/exports/ ({msg_json})'
    except Exception as e:
        log(f'export error: {e}')
        return f'error: {type(e).__name__}: {e}'


def count_constrained(con, table, where):
    """Helper: SELECT COUNT(*) FROM table WHERE ..."""
    return con.execute(f'SELECT COUNT(*) FROM {table} WHERE {where}').fetchone()[0]
