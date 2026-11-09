"""Your own planet-vs-not model. Learns from your "Looks like a planet" / "Not a planet" clicks (weighted 3x)
plus automatic labels: signals matching known planets/TOIs count as planet-like, signals the hard checks
rejected count as not. A small logistic regression in NumPy: transparent, retrains in milliseconds."""
import json
import math
import time

import numpy as np

from . import db

FEATURES = ['log depth', 'SNR', 'SDE', 'dips seen', 'log size', 'duration vs expected', 'odd/even σ',
            'half-orbit dip σ', 'centroid offset σ', 'single dip', 'AI says planet-like']
MIN_LABELS = 12


def _j(x):
    if isinstance(x, (dict, list)) or x is None:
        return x
    try:
        return json.loads(x)
    except Exception:
        return None


def features(c):
    m = _j(c.get('metrics')) or {}
    cen = _j(c.get('centroid')) or {}
    ai = _j(c.get('ai')) or {}
    dur_ratio = 1.0
    if m.get('expected_dur_h') and c.get('duration'):
        dur_ratio = min(5.0, c['duration'] * 24 / max(m['expected_dur_h'], 0.1))
    aiv = str(ai.get('verdict') or '').lower()
    ai_score = (ai.get('confidence') or 0.5) if aiv.startswith('planet') or aiv == 'real' else (1 - (ai.get('confidence') or 0.5)) if aiv else 0.5
    return [math.log10(max(c.get('depth') or 1e-5, 1e-5)), min(c.get('snr') or 0, 60), min(c.get('sde') or 0, 40),
            min(c.get('ntr') or 1, 20), math.log10(max(c.get('rp') or 1, 0.3)), dur_ratio,
            min(m.get('odd_even_sigma') or 0, 20), min(max(m.get('secondary_sigma') or 0, 0), 20),
            min(cen.get('signif') or 0, 20) if cen.get('snr', 0) >= 3 else 0.0, 1.0 if c.get('kind') == 'single' else 0.0,
            ai_score]


def labelled(con):
    X, y, w, n_user, n_auto = [], [], [], 0, 0
    for c in db.rows(con, 'SELECT * FROM tess_cands'):
        lab, wt = None, 1.0
        if c['status'] in ('real', 'fake'):
            lab, wt = (1 if c['status'] == 'real' else 0), 3.0
            n_user += 1
        elif c['status'] == 'auto-rejected':
            k = (c.get('known') or '').upper()
            if k and not any(x in k for x in (' FP', ' FA', 'APC')):
                lab = 1
            elif not k:
                lab = 0
            if lab is not None:
                n_auto += 1
        if lab is None:
            continue
        X.append(features(c)); y.append(lab); w.append(wt)
    return np.array(X, dtype=float), np.array(y, dtype=float), np.array(w, dtype=float), n_user, n_auto


def train(con, l2=0.3, iters=3000, lr=0.2):
    X, y, w, n_user, n_auto = labelled(con)
    if len(y) < MIN_LABELS or y.min() == y.max():
        db.kv_set(con, 'clf', {'ready': False, 'n_user': n_user, 'n_auto': n_auto, 'need': MIN_LABELS, 't': time.time()})
        return None
    mu, sd = X.mean(axis=0), X.std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    beta, b = np.zeros(Z.shape[1]), 0.0
    W = w / w.sum()
    for _ in range(iters):
        p = 1 / (1 + np.exp(-(Z @ beta + b)))
        g = Z.T @ (W * (p - y)) + l2 * beta / len(y)
        beta -= lr * g
        b -= lr * float(np.sum(W * (p - y)))
    p = 1 / (1 + np.exp(-(Z @ beta + b)))
    acc = float(np.sum(W * ((p > 0.5) == (y > 0.5))))
    top = sorted(zip(FEATURES, beta), key=lambda kv: -abs(kv[1]))[:4]
    model = {'ready': True, 'mu': mu.tolist(), 'sd': sd.tolist(), 'beta': beta.tolist(), 'b': b, 'n_user': n_user,
             'n_auto': n_auto, 'acc': round(acc, 3), 'top': [[k, round(v, 2)] for k, v in top], 't': time.time()}
    db.kv_set(con, 'clf', model)
    return model


def score(model, c):
    if not model or not model.get('ready'):
        return None
    z = (np.array(features(c)) - np.array(model['mu'])) / np.array(model['sd'])
    return round(float(1 / (1 + np.exp(-(z @ np.array(model['beta']) + model['b'])))), 3)
