"""Fast transit search (box least squares) on the Mac's GPU with Apple MLX, or with NumPy when MLX is missing.

Same statistic as astropy's BoxLeastSquares(objective='likelihood'), computed on phase-folded bins, so a whole
period grid takes seconds. The best peaks are then refined with astropy for exact numbers and the standard checks.
"""
import math

import numpy as np

_MX = None


def backend():
    """'mlx-gpu', 'mlx-cpu' or 'numpy'."""
    global _MX
    if _MX is None:
        try:
            import mlx.core as mx
            _MX = mx
        except Exception:
            _MX = False
    if not _MX:
        return 'numpy'
    try:
        return 'mlx-gpu' if 'gpu' in str(_MX.default_device()).lower() else 'mlx-cpu'
    except Exception:
        return 'mlx-cpu'


def _prep(t, y, dy):
    t = np.asarray(t, dtype=float)
    y = np.asarray(y, dtype=float)
    dy = np.asarray(dy, dtype=float)
    w = 1.0 / np.where(np.isfinite(dy) & (dy > 0), dy, np.nanmedian(dy[dy > 0]) if np.any(dy > 0) else 1.0) ** 2
    w = w / w.sum()
    yc = y - np.sum(w * y)
    tref = t.min()
    return (t - tref), w, yc, tref


def _chunk_numpy(tt, w, wy, P, Nb, ks):
    C = len(P)
    ph = np.mod(tt[None, :] / P[:, None], 1.0)
    b = np.minimum((ph * Nb).astype(np.int64), Nb - 1)
    flat = (b + (np.arange(C) * Nb)[:, None]).ravel()
    R = np.bincount(flat, weights=np.tile(w, C), minlength=C * Nb).reshape(C, Nb)
    S = np.bincount(flat, weights=np.tile(wy, C), minlength=C * Nb).reshape(C, Nb)
    kmax = int(ks.max())
    zc = np.zeros((C, 1))
    cR = np.concatenate([zc, np.cumsum(np.concatenate([R, R[:, :kmax]], axis=1), axis=1)], axis=1)
    cS = np.concatenate([zc, np.cumsum(np.concatenate([S, S[:, :kmax]], axis=1), axis=1)], axis=1)
    best = np.zeros(C)
    bk = np.zeros(C, dtype=np.int64)
    bj = np.zeros(C, dtype=np.int64)
    for ki, k in enumerate(ks):
        r = cR[:, k:k + Nb] - cR[:, :Nb]
        s = cS[:, k:k + Nb] - cS[:, :Nb]
        ok = (r > 1e-12) & (r < 1 - 1e-12) & (s < 0)
        pw = np.where(ok, 0.5 * s * s / np.where(ok, r * (1 - r), 1.0), 0.0)
        j = np.argmax(pw, axis=1)
        v = pw[np.arange(C), j]
        better = v > best
        best = np.where(better, v, best)
        bk = np.where(better, ki, bk)
        bj = np.where(better, j, bj)
    return best, bk, bj


def _chunk_mlx(tt, w, wy, P, Nb, ks):
    mx = _MX
    C = len(P)
    tt_ = mx.array(tt.astype(np.float32))
    Pm = mx.array(P.astype(np.float32))
    ph = mx.remainder(tt_[None, :] / Pm[:, None], 1.0)
    b = mx.minimum(mx.floor(ph * Nb).astype(mx.int32), Nb - 1)
    flat = (b + (mx.arange(C, dtype=mx.int32) * Nb)[:, None]).reshape(-1)
    wt = mx.tile(mx.array(w.astype(np.float32)), (C,))
    wyt = mx.tile(mx.array(wy.astype(np.float32)), (C,))
    R = mx.zeros((C * Nb,), dtype=mx.float32).at[flat].add(wt).reshape(C, Nb)
    S = mx.zeros((C * Nb,), dtype=mx.float32).at[flat].add(wyt).reshape(C, Nb)
    kmax = int(ks.max())
    zc = mx.zeros((C, 1), dtype=mx.float32)
    cR = mx.concatenate([zc, mx.cumsum(mx.concatenate([R, R[:, :kmax]], axis=1), axis=1)], axis=1)
    cS = mx.concatenate([zc, mx.cumsum(mx.concatenate([S, S[:, :kmax]], axis=1), axis=1)], axis=1)
    pws = []
    for k in ks:
        k = int(k)
        r = cR[:, k:k + Nb] - cR[:, :Nb]
        s = cS[:, k:k + Nb] - cS[:, :Nb]
        ok = (r > 1e-9) & (r < 1 - 1e-9) & (s < 0)
        den = mx.where(ok, r * (1 - r), mx.ones_like(r))
        pws.append(mx.where(ok, 0.5 * s * s / den, mx.zeros_like(r)))
    allp = mx.stack(pws, axis=1)                     # C, K, Nb
    flatp = allp.reshape(C, -1)
    idx = mx.argmax(flatp, axis=1)
    best = mx.take_along_axis(flatp, idx[:, None], axis=1)[:, 0]
    mx.eval(best, idx)
    idx = np.array(idx).astype(np.int64)
    return np.array(best, dtype=float), idx // Nb, idx % Nb


def search(t, y, dy, periods, durations, oversample=3, use=None, max_cells=6_000_000):
    """Return dict with power (per period), and the best period's t0, duration and depth."""
    tt, w, yc, tref = _prep(t, y, dy)
    wy = w * yc
    periods = np.asarray(periods, dtype=float)
    durations = np.sort(np.asarray(durations, dtype=float))
    binw = durations.min() / oversample
    order = np.argsort(periods)
    P_sorted = periods[order]
    eng = backend() if use is None else (backend() and use)
    if eng.startswith('mlx') and not _MX:
        eng = 'numpy'
    fn = _chunk_mlx if eng.startswith('mlx') else _chunk_numpy
    n = len(tt)
    power = np.zeros(len(periods))
    kbest = np.zeros(len(periods), dtype=np.int64)
    jbest = np.zeros(len(periods), dtype=np.int64)
    nbins = np.zeros(len(periods), dtype=np.int64)
    i = 0
    while i < len(P_sorted):
        Nb = int(math.ceil(P_sorted[i] * 1.15 / binw))    # bins for the longest period in this chunk
        C = max(1, min(4096, max_cells // max(n, Nb * len(durations))))
        j = i
        while j < len(P_sorted) and j - i < C and P_sorted[j] <= P_sorted[i] * 1.15:
            j += 1
        P = P_sorted[i:j]
        Nb_c = int(math.ceil(P.max() / binw))
        ks = np.maximum(1, np.round(durations / (P.mean() / Nb_c)).astype(np.int64))
        best, bk, bj = fn(tt, w, wy, P, Nb_c, ks)
        sl = order[i:j]
        power[sl], kbest[sl], jbest[sl], nbins[sl] = best, bk, bj, Nb_c
        i = j
    ib = int(np.argmax(power))
    P = periods[ib]
    Nb = nbins[ib]
    ks = np.maximum(1, np.round(durations / (P / Nb)).astype(np.int64))
    k = ks[kbest[ib]]
    dur = k * P / Nb
    t0 = tref + (jbest[ib] + k / 2.0) * P / Nb
    # depth from the box at the best phase
    ph = np.mod((tt + tref - t0) / P + 0.5, 1.0) - 0.5
    inside = np.abs(ph) * P < dur / 2
    depth = float(np.sum(w[~inside] * yc[~inside]) / max(np.sum(w[~inside]), 1e-12) -
                  np.sum(w[inside] * yc[inside]) / max(np.sum(w[inside]), 1e-12)) if inside.any() else 0.0
    return {'power': power, 'period': float(P), 't0': float(t0), 'duration': float(dur), 'depth': depth,
            'engine': eng, 'index': ib}
