"""Re-analysis and maintenance utilities. Fixes common issues that accumulate over time."""
import json
from datetime import datetime

from . import config, db


def report_tess_coverage(con):
    """Report TESS candidate AI/centroid coverage gaps."""
    total = con.execute('SELECT COUNT(*) FROM tess_cands').fetchone()[0]
    with_ai = con.execute("SELECT COUNT(*) FROM tess_cands WHERE ai IS NOT NULL AND ai != ''").fetchone()[0]
    
    # Breakdown by status and kind
    rows = []
    for row in con.execute('''
        SELECT status, kind, 
               COUNT(*) as total,
               SUM(CASE WHEN ai IS NOT NULL AND ai != '' THEN 1 ELSE 0 END) as with_ai
        FROM tess_cands GROUP BY status, kind
    ''').fetchall():
        rows.append({'status': row[0], 'kind': row[1], 'total': row[2], 'with_ai': row[3]})
    
    missing = total - with_ai
    report = f"TESS coverage: {with_ai}/{total} candidates have AI ({missing} missing)\n\n"
    
    for r in rows:
        status = r['status']
        kind = r['kind']
        total_c = r['total']
        ai_c = r['with_ai']
        report += f"  {status}/{kind}: {ai_c}/{total_c} with AI\n"
    
    if missing > 0:
        report += f"\nAction: Run 'tess' job again to fill in missing AI analysis on all candidates."
    else:
        report += "\nAll candidates have AI coverage! Ready for publication."
    
    return report, total


def build_schedule_report(con):
    """Analyze actual job runtimes and suggest schedule improvements."""
    runs = list(con.execute('''
        SELECT job, 
               AVG(CASE WHEN finished > started AND status = 'ok' THEN (finished - started) END) as avg_runtime_s,
               COUNT(*) as total_runs,
               SUM(CASE WHEN status = 'ok' THEN 1 ELSE 0 END) as successes
        FROM runs 
        GROUP BY job
    ''').fetchall())
    
    if not runs:
        return "No run data available yet. Run a few jobs to build up schedule history.", 0
    
    lines = ["Schedule analysis based on actual runtimes:\n"]
    for row in runs:
        job, avg_rt, total, success = row
        hours = (avg_rt or 0) / 3600
        lines.append(f"  {job:15s}: avg {hours*60:.1f} min/run, {success}/{total} successful")
    
    # Suggest schedule based on runtimes
    suggestions = []
    for row in runs:
        job, avg_rt, total, success = row
        if avg_rt and total > 2:
            mins = avg_rt / 60
            if mins > 30:
                suggestions.append(f"  {job}: consider schedule 'every 1h' (takes ~{mins:.0f} min)")
    
    lines.append("\nSuggestions:")
    for s in suggestions:
        lines.append(s)
    if not suggestions:
        lines.append("  All jobs run under 30 min — current schedule looks optimal.")
    
    return '\n'.join(lines), len(runs)


def run(ctx):
    """Report job: analyze coverage, build data.json, optimize schedule."""
    con, log = ctx.con, ctx.log
    from . import config
    
    try:
        cfg = config.load()
        
        # 1. TESS coverage report
        log('--- TESS Coverage ---')
        report, _ = report_tess_coverage(con)
        for line in report.split('\n'):
            log(f'  {line}')
        
        # 3. Schedule analysis  
        log('--- Schedule Analysis ---')
        sched_report, _ = build_schedule_report(con)
        for line in sched_report.split('\n'):
            log(f'  {line}')
        
        return 'coverage report + schedule analysis written to the log'
    except Exception as e:
        log(f'report error: {e}')
        return f'error: {type(e).__name__}: {e}'
