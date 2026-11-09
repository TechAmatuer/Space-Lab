"""Statistical false-positive probability with TRICERATOPS (Giacalone et al. 2021).

It asks: given the stars around the target (Gaia), the shape of the dip and its depth, how likely is it that
the signal is a planet on the target star versus an eclipsing binary, a background binary, or a nearby star?
  FPP  = probability it's NOT a planet on the target;  NFPP = probability it comes from a nearby star.
Published thresholds: FPP < 0.015 and NFPP < 0.001 → "statistically validated"; FPP < 0.5 and NFPP < 0.001
→ "likely planet"; NFPP > 0.1 → "likely a nearby false positive". (A validation still needs follow-up.)
"""
import json
import subprocess
import sys
import warnings
from pathlib import Path

import numpy as np

# TRICERATOPS pins old numpy/scipy, so it lives in its own environment (.venv-fpp) and runs as a separate process.
FPP_PY = Path(__file__).resolve().parent.parent / '.venv-fpp' / 'bin' / 'python'


def verdict(fpp, nfpp):
    if fpp is None or nfpp is None:
        return 'not computed'
    if nfpp > 0.1:
        return 'likely a nearby-star false positive'
    if fpp < 0.015 and nfpp < 0.001:
        return 'passes the statistical validation thresholds'
    if fpp < 0.5 and nfpp < 0.001:
        return 'likely planet'
    return 'inconclusive'


def available():
    return FPP_PY.exists()


def run(tic, sectors, t, y, period, t0, duration, depth, workdir, n_samples=200_000, timeout=1800):
    """Runs TRICERATOPS in .venv-fpp. Returns a JSON-safe dict, or raises RuntimeError."""
    if not available():
        raise RuntimeError('TRICERATOPS is not installed (restart Meridian to install it)')
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    inp, out = workdir / 'fpp_in.npz', workdir / 'fpp_out.json'
    np.savez(inp, t=np.asarray(t, float), y=np.asarray(y, float), sectors=np.asarray(sorted(set(int(s) for s in sectors))),
             p=np.array([int(tic), period, t0, duration, depth, n_samples], float))
    out.unlink(missing_ok=True)
    r = subprocess.run([str(FPP_PY), '-I', __file__, str(inp), str(out)], capture_output=True, text=True, timeout=timeout)
    if r.returncode or not out.exists():
        raise RuntimeError('TRICERATOPS failed: ' + (r.stderr or r.stdout)[-300:].replace('\n', ' '))
    return json.loads(out.read_text())


def _compute(tic, sectors, t, y, period, t0, duration, depth, workdir, n_samples):
    import triceratops.triceratops as tr
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        target = tr.target(ID=int(tic), sectors=np.array(sorted(set(int(s) for s in sectors))), lightkurve_cache_dir=str(workdir))
        target.calc_depths(tdepth=float(depth))
        ph = ((np.asarray(t) - t0 + 0.5 * period) % period) - 0.5 * period
        w = np.abs(ph) < max(2.5 * duration, 0.25)
        tt, ff = ph[w], np.asarray(y)[w]
        out_tr = np.abs(tt) > 0.75 * duration
        err = float(np.std(ff[out_tr])) if out_tr.sum() > 10 else float(np.std(ff))
        order = np.argsort(tt)
        target.calc_probs(time=tt[order], flux_0=ff[order], flux_err_0=err, P_orb=float(period), N=int(n_samples),
                          parallel=False, verbose=0)
    fpp, nfpp = float(target.FPP), float(target.NFPP)
    return {'fpp': round(fpp, 4), 'nfpp': round(nfpp, 5), 'verdict': verdict(fpp, nfpp),
            'n_stars': int(len(target.stars)), 'method': 'TRICERATOPS (Giacalone+ 2021)'}


if __name__ == '__main__':
    d = np.load(sys.argv[1])
    tic, period, t0, duration, depth, n = d['p']
    res = _compute(int(tic), d['sectors'], d['t'], d['y'], period, t0, duration, depth, Path(sys.argv[1]).parent / 'lk', int(n))
    Path(sys.argv[2]).write_text(json.dumps(res))
