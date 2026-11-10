"""Run one Space Lab job:  python -m spacelab.jobs <arxiv|tess|precovery|exowatch|brief|selftest>"""
import importlib
import os
import sys
import time
import traceback

from . import config, db

JOBS = {
    'arxiv': ('spacelab.arxiv_digest', 'arXiv digest'),
    'exowatch': ('spacelab.exowatch', 'Exoplanet Watch'),
    'tess': ('spacelab.tess_hunter', 'TESS planet hunter'),
    'precovery': ('spacelab.precovery', 'Asteroid watch + precovery'),
    'brief': ('spacelab.brief', 'Morning brief'),
    'neocp': ('spacelab.neocp', 'NEO confirmation watch'),
    'targets': ('spacelab.ephem', 'Transit timing targets'),
    'publish': ('spacelab.publish', 'Shared page update'),
    'library': ('spacelab.library', 'Paper library (50 years)'),
    'beyond': ('spacelab.beyond', 'Sky watch & Beyond astronomy'),
    'radar': ('spacelab.radar', 'Space science radar (local AI)'),
    'cheops': ('spacelab.cheops', 'CHEOPS answer-key test'),
    'plates': ('spacelab.plates', 'Old-plate precovery (distant objects)'),
    'universe': ('spacelab.universe', '3D universe (galaxy map, monthly)'),
    'variables': ('spacelab.variables', 'Variable stars in TESS light curves'),
    'ai': ('spacelab.aicatchup', 'Local AI catch-up (while the Mac is idle)'),
    'selftest': ('spacelab.selftest', 'Self-test'),
    'report': ('spacelab.reanalyze', 'Maintenance & coverage report'),
    'export': ('spacelab.export', 'Data export (JSON/CSV)'),
}


class Ctx:
    def __init__(self, job, run_id, budget_min=None):
        self.job = job
        self.run_id = run_id
        self.cfg = config.load()
        self.con = db.connect()
        self.t0 = time.time()
        self.deadline = self.t0 + budget_min * 60 if budget_min else None

    def log(self, *a):
        print(time.strftime('%H:%M:%S'), *a, flush=True)

    def time_left(self):
        return float('inf') if self.deadline is None else self.deadline - time.time()

    def progress(self, text):
        self.con.execute('UPDATE runs SET summary=? WHERE id=?', (text[:500], self.run_id))


def restart_if_requested():
    """Updates drop data/.restart-request; the next job then restarts the Space Lab server (installing any new
    packages first), exactly like Start Meridian.command would. Used once after an update, so you don't have to."""
    flag = config.DATA / '.restart-request'
    if not flag.exists() or not os.environ.get('SPACELAB_RUN_ID'):
        return
    flag.unlink()
    import signal
    import subprocess
    root = str(config.ROOT)
    script = ('sleep 3; cd "$ROOT" || exit 1; '
              'want="$(cat requirements.txt requirements-exotic.txt requirements-fpp.txt | shasum | cut -c1-40)"; '
              'if [ "$want" != "$(cat .venv/.req-stamp 2>/dev/null)" ]; then bash setup.sh; fi; '
              'PYTHONPYCACHEPREFIX="$ROOT/data/cache/pycache" exec .venv/bin/python -m spacelab.server --no-browser')
    env = {k: v for k, v in os.environ.items() if k != 'SPACELAB_RUN_ID'}
    env['ROOT'] = root
    with open(config.LOGS / 'server.log', 'a') as log:
        subprocess.Popen(['/bin/bash', '-c', script], cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                         stdin=subprocess.DEVNULL, start_new_session=True)
    print('restarting the Space Lab server to load an update', flush=True)
    os.kill(os.getppid(), signal.SIGTERM)
    time.sleep(10)


def main(argv):
    restart_if_requested()
    if len(argv) < 2 or argv[1] not in JOBS:
        print('usage: python -m spacelab.jobs ' + '|'.join(JOBS))
        return 2
    job = argv[1]
    con = db.connect()
    run_id = os.environ.get('SPACELAB_RUN_ID')
    if run_id:
        run_id = int(run_id)
    else:
        run_id = con.execute("INSERT INTO runs(job, started, status, trigger) VALUES(?,?,?,?)",
                             (job, time.time(), 'running', 'terminal')).lastrowid
    cfg = config.load()
    budget = (cfg.get(job) or {}).get('time_budget_min') if isinstance(cfg.get(job), dict) else None
    if budget and os.environ.get('SPACELAB_TRIGGER') == 'full-time mode':
        budget = min(float(budget), 50.0)     # background runs stay short so scheduled and requested jobs never wait long
    ctx = Ctx(job, run_id, budget)
    ctx.log(f'=== {JOBS[job][1]} (run {run_id}) ===')
    try:
        mod = importlib.import_module(JOBS[job][0])
        summary = mod.run(ctx) or 'done'
        status, code = 'ok', 0
    except Exception as e:
        traceback.print_exc()
        summary = f'error: {type(e).__name__}: {e}'
        status, code = 'error', 1
    ctx.log('SUMMARY:', summary)
    if not os.environ.get('SPACELAB_RUN_ID'):
        con.execute('UPDATE runs SET finished=?, status=?, summary=? WHERE id=?', (time.time(), status, summary[:500], run_id))
    else:
        con.execute('UPDATE runs SET summary=? WHERE id=?', (summary[:500], run_id))
    return code


if __name__ == '__main__':
    sys.exit(main(sys.argv))
