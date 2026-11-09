"""Centroid (difference-image) test: does the dip happen on the target star or on a neighbor?

Averages TESS pixel images taken during the dips and just before/after them. The "difference image" (out minus in)
lights up wherever brightness dropped. If that spot sits on the target, good. If it sits a pixel or more away,
the dip probably comes from a nearby star (a "blend"), the most common planet impostor in TESS data.
"""
import math
import warnings

import numpy as np

TESS_PX_ARCSEC = 21.0


def _wcentroid(img, x0, y0, rad=2.5):
    ny, nx = img.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    m = ((xx - x0) ** 2 + (yy - y0) ** 2 <= rad ** 2) & np.isfinite(img)
    v = np.where(m, np.clip(img, 0, None), 0.0)
    tot = v.sum()
    if tot <= 0:
        return None
    return float((v * xx).sum() / tot), float((v * yy).sum() / tot)


def analyze_cube(t, cube, x_t, y_t, period, t0, duration, subtract_bkg=False):
    """Core test on a pixel cube (time, rows, cols). (x_t, y_t) = target pixel position. Pure NumPy, testable."""
    t = np.asarray(t, dtype=float)
    cube = np.asarray(cube, dtype=float)
    good = np.isfinite(t) & (np.isfinite(cube.reshape(len(t), -1)).sum(axis=1) > 0)
    t, cube = t[good], cube[good]
    if subtract_bkg:
        flat = cube.reshape(len(t), -1)
        bkg = np.nanpercentile(flat, 30, axis=1)
        cube = cube - bkg[:, None, None]
    ph = ((t - t0 + 0.5 * period) % period) - 0.5 * period
    epoch = np.round((t - t0) / period).astype(int)
    a = np.abs(ph)
    inside = a < 0.35 * duration
    outside = (a > 0.75 * duration) & (a < 0.75 * duration + max(1.5 * duration, 0.08))
    diffs, outs = [], []
    for n in np.unique(epoch[inside]):
        mi, mo = inside & (epoch == n), outside & (epoch == n)
        if mi.sum() < 2 or mo.sum() < 4:
            continue
        o = np.nanmean(cube[mo], axis=0)
        diffs.append(o - np.nanmean(cube[mi], axis=0))
        outs.append(o)
    if not diffs:
        return None
    diff = np.nanmean(diffs, axis=0)
    out = np.nanmean(outs, axis=0)
    # noise of the difference image from the scatter between transits (or a robust pixel estimate)
    if len(diffs) >= 3:
        pix_err = np.nanstd(diffs, axis=0) / math.sqrt(len(diffs))
    else:
        resid = cube[outside] - np.nanmean(cube[outside], axis=0)
        pix_err = 1.4826 * np.nanmedian(np.abs(resid), axis=0) / math.sqrt(max(1, inside.sum()))
    pix_err = np.where(np.isfinite(pix_err) & (pix_err > 0), pix_err, np.nanmedian(pix_err[pix_err > 0]) if np.any(pix_err > 0) else 1.0)
    c_out = _wcentroid(out, x_t, y_t)
    c_dif = _wcentroid(diff, x_t, y_t)
    if c_out is None or c_dif is None:
        return None
    # significance of the dip in the difference image, inside a 2-pixel aperture around the target
    ny, nx = diff.shape
    yy, xx = np.mgrid[0:ny, 0:nx]
    ap = (xx - x_t) ** 2 + (yy - y_t) ** 2 <= 2.0 ** 2
    snr = float(np.nansum(diff[ap]) / math.sqrt(np.nansum(pix_err[ap] ** 2))) if ap.any() else 0.0
    # centroid uncertainty: per-transit scatter, else from pixel noise
    per = [_wcentroid(d, x_t, y_t) for d in diffs]
    per = np.array([p for p in per if p is not None])
    if len(per) >= 3:
        sig = float(np.hypot(*np.nanstd(per, axis=0)) / math.sqrt(len(per)))
    else:
        sig = 1.0 / max(snr, 1.0)
    sig = math.hypot(max(sig, 0.05), 0.1)   # + ~2 arcsec for the star-position (WCS) uncertainty
    dx, dy = c_dif[0] - x_t, c_dif[1] - y_t   # where the light dropped vs. where the target star is
    off = math.hypot(dx, dy)
    peak = np.unravel_index(np.nanargmax(np.where(np.isfinite(diff), diff, -np.inf)), diff.shape)
    peak_dist = math.hypot(peak[1] - x_t, peak[0] - y_t)
    if snr < 3:
        verdict = 'inconclusive'
    elif off / sig > 3 and off * TESS_PX_ARCSEC > 7:
        verdict = 'off target'
    elif peak_dist > 1.5 and snr > 6:
        verdict = 'off target'
    else:
        verdict = 'on target'
    return {'offset_px': round(off, 3), 'offset_arcsec': round(off * TESS_PX_ARCSEC, 1), 'sigma_px': round(sig, 3),
            'signif': round(off / sig, 1), 'snr': round(snr, 1), 'peak_dist_px': round(peak_dist, 2),
            'n_transits': len(diffs), 'verdict': verdict, 'diff': diff, 'out': out,
            'c_out': c_out, 'c_dif': c_dif, 'target_xy': (float(x_t), float(y_t))}


def plot(path, tic, res, neighbors):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(1, 2, figsize=(9, 4.2))
    for a, img, title in ((ax[0], res['out'], 'normal brightness (between dips)'), (ax[1], res['diff'], 'where the light dropped (difference)')):
        a.imshow(img, origin='lower', cmap='viridis')
        a.plot(*res['target_xy'], '+', color='#ff5a36', ms=14, mew=2, label='target star')
        for nb in neighbors:
            a.plot(nb['x'], nb['y'], 'o', mfc='none', mec='white', ms=7, alpha=0.8)
        a.set_title(title, fontsize=10)
        a.set_xticks([]); a.set_yticks([])
    ax[1].plot(*res['c_dif'], 'x', color='white', ms=10, mew=2, label='dip centroid')
    ax[1].legend(loc='lower right', fontsize=7)
    fig.suptitle(f'TIC {tic} centroid test: {res["verdict"]} (offset {res["offset_arcsec"]}″, {res["signif"]}σ; circles = nearby stars)', fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=80)
    plt.close(fig)


def run_test(tic, ra, dec, tmag, period, t0, duration, workdir, png_path, log=print):
    """Download a TESS full-frame cutout (TESScut), run the test, save a plot. Returns a JSON-safe dict or None."""
    import lightkurve as lk
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        sr = lk.search_tesscut(f'TIC {tic}')
        if len(sr) == 0:
            return None
        best, used = None, []
        for row in list(sr)[-3:][::-1]:
            try:
                tpf = row.download(cutout_size=11, download_dir=str(workdir))
            except Exception as e:
                log(f'  TESScut download failed: {e}')
                continue
            if tpf is None:
                continue
            tpf = tpf[tpf.quality == 0] if hasattr(tpf, 'quality') else tpf
            try:
                x, y = tpf.wcs.world_to_pixel(SkyCoord(ra, dec, unit='deg'))
            except Exception:
                x = y = (tpf.shape[1] - 1) / 2
            res = analyze_cube(tpf.time.value, tpf.flux.value, float(x), float(y), period, t0, duration, subtract_bkg=True)
            if res is None:
                continue
            res['sector'] = int(tpf.sector or 0)
            used.append(res['sector'])
            if best is None or res['snr'] > best['snr']:
                best, best_tpf = res, tpf
        if best is None:
            return None
        neighbors = []
        try:
            from astroquery.mast import Catalogs
            tab = Catalogs.query_region(SkyCoord(ra, dec, unit='deg'), radius=2.5 * u.arcmin, catalog='TIC')
            for r in tab:
                if int(r['ID']) == int(tic) or not np.isfinite(r['Tmag']) or r['Tmag'] > (tmag or 12) + 5:
                    continue
                nx_, ny_ = best_tpf.wcs.world_to_pixel(SkyCoord(r['ra'], r['dec'], unit='deg'))
                if -1 <= nx_ <= best_tpf.shape[2] and -1 <= ny_ <= best_tpf.shape[1]:
                    neighbors.append({'x': float(nx_), 'y': float(ny_), 'tmag': float(r['Tmag']),
                                      'sep_arcsec': round(float(r['dstArcSec']), 1) if 'dstArcSec' in tab.colnames else None})
        except Exception as e:
            log(f'  neighbor lookup failed: {e}')
    plot(png_path, tic, best, neighbors[:30])
    close = [n for n in neighbors if n.get('sep_arcsec') is not None and n['sep_arcsec'] < 42 and n['tmag'] < (tmag or 12) + 3]
    out = {k: v for k, v in best.items() if k not in ('diff', 'out', 'c_out', 'c_dif', 'target_xy')}
    out.update({'sectors_tried': used, 'neighbors_within_2px': len(close), 'source': 'TESScut full-frame cutout'})
    return out
