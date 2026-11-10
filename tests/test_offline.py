"""Offline tests with synthetic data (no internet, no Ollama). Run:  python tests/test_offline.py"""
import json
import re
import math
import sys
import tempfile
import time
import traceback
from pathlib import Path

import os

import numpy as np

os.environ.setdefault('SPACELAB_DATA', tempfile.mkdtemp(prefix='spacelab-test-'))   # never touch the real data folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
RES = []


def test(fn):
    try:
        fn()
        RES.append((fn.__name__, True, ''))
    except Exception as e:
        RES.append((fn.__name__, False, f'{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}'))
    return fn


# ---------------------------------------------------------------- arXiv
@test
def arxiv_parse():
    from spacelab.arxiv_digest import parse_feed, first_sentences
    xml = b'''<?xml version="1.0" encoding="UTF-8"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
    <entry><id>http://arxiv.org/abs/2611.01234v2</id><published>2026-11-06T18:00:00Z</published>
    <title>A  hot Jupiter
     with TESS</title><summary>We find a planet. It is big. More text.</summary>
    <author><name>A. Author</name></author><author><name>B. Author</name></author>
    <arxiv:primary_category term="astro-ph.EP"/><category term="astro-ph.SR"/><category term="astro-ph.EP"/></entry></feed>'''
    p = parse_feed(xml)
    assert len(p) == 1 and p[0]['id'] == '2611.01234', p
    assert p[0]['title'] == 'A hot Jupiter with TESS'
    assert p[0]['cats'][0] == 'astro-ph.EP'
    assert first_sentences(p[0]['abstract']) == 'We find a planet. It is big.'


# ---------------------------------------------------------------- TESS
def synth_lc(P=3.7, depth=3e-3, dur=0.09, t0=1.3, days=54, noise=8e-4, eb=False, seed=1):
    rng = np.random.default_rng(seed)
    t = np.arange(0, days, 2 / 1440)
    t = t[(t % 27) > 1.0]  # sector gaps
    y = 1 + rng.normal(0, noise, t.size)
    ph = ((t - t0 + 0.5 * P) % P) - 0.5 * P
    ep = np.round((t - t0) / P).astype(int)
    intr = np.abs(ph) < dur / 2
    d = np.where((ep % 2 == 1) & eb, depth * 2.2, depth)
    y[intr] -= d[intr]
    return t, y, np.full_like(y, noise)


@test
def tess_finds_planet():
    from spacelab.tess_hunter import bls_search, vet, bin_lc, clean
    t, y, e = clean(*synth_lc())
    ts, ys, es = bin_lc(t, y, e, 10 / 1440)
    s = bls_search(ts, ys, es, 0.5, 20)
    assert abs(s['period'] - 3.7) / 3.7 < 0.003, s['period']
    assert s['sde'] > 9 and s['snr'] > 7, (s['sde'], s['snr'])
    v = vet(s, {'rad': 0.45, 'mass': 0.45}, ts, None)
    assert not v['severe'], v['flags']
    assert 2.2 < v['rp'] < 3.3, v['rp']  # sqrt(3e-3) * 0.45 Rsun = 2.7 Earth radii


@test
def tess_flags_eclipsing_binary():
    from spacelab.tess_hunter import bls_search, vet, bin_lc, clean
    t, y, e = clean(*synth_lc(eb=True, depth=6e-3, seed=3))
    ts, ys, es = bin_lc(t, y, e, 10 / 1440)
    s = bls_search(ts, ys, es, 0.5, 20)
    v = vet(s, {'rad': 0.45, 'mass': 0.45}, ts, None)
    # BLS may lock onto P or 2P; either way the odd/even or depth checks must catch it
    assert v['severe'] or abs(s['period'] - 7.4) < 0.05, (s['period'], v['flags'])


@test
def tess_known_match():
    from spacelab.tess_hunter import match_known
    assert match_known(3.700, [[1.85, 'TOI 123.01']]) == 'TOI 123.01'
    assert match_known(5.0, [[3.7, 'x']]) is None


@test
def tess_single_event_and_plots():
    from spacelab.tess_hunter import single_events, clean, bin_lc, plot_single, bls_search, vet, plot_periodic
    rng = np.random.default_rng(5)
    t = np.arange(0, 27, 2 / 1440)
    y = 1 + rng.normal(0, 8e-4, t.size)
    y[np.abs(t - 13.2) < 0.06] -= 4e-3
    t, y, e = clean(t, y, np.full_like(y, 8e-4))
    ts, ys, es = bin_lc(t, y, e, 10 / 1440)
    ev = single_events(ts, ys, es)
    assert ev and abs(ev[0]['t0'] - 13.2) < 0.05, ev
    out = Path(tempfile.mkdtemp())
    plot_single(out / 's.png', 1, ts, ys, ev[0], 'test')
    t2, y2, e2 = clean(*synth_lc())
    ts2, ys2, es2 = bin_lc(t2, y2, e2, 10 / 1440)
    s = bls_search(ts2, ys2, es2, 0.5, 20)
    plot_periodic(out / 'p.png', 1, ts2, ys2, s, vet(s, {}, ts2, None), 'test')
    assert (out / 's.png').stat().st_size > 5000 and (out / 'p.png').stat().st_size > 5000


# ---------------------------------------------------------------- precovery
def make_wcs_header(ra0, dec0, n, scale=0.25):
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.crpix = [n / 2 + 0.5, n / 2 + 0.5]
    w.wcs.cdelt = [-scale / 3600, scale / 3600]
    w.wcs.crval = [ra0, dec0]
    w.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    return w, w.to_header()


def star_image(n, stars, rng, fwhm=4.0, noise=5.0):
    yy, xx = np.mgrid[0:n, 0:n]
    img = rng.normal(100, noise, (n, n))
    sig = fwhm / 2.355
    for x, y, f in stars:
        img += f * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * sig ** 2))
    return img


@test
def precovery_finds_mover():
    from spacelab.precovery import detect_sources, moving_candidates, link_pairs, offsets, mahal
    rng = np.random.default_rng(7)
    n, ra0, dec0 = 400, 150.0, 20.0
    w, hdr = make_wcs_header(ra0, dec0, n)
    stars = [(rng.uniform(20, n - 20), rng.uniform(20, n - 20), rng.uniform(150, 3000)) for _ in range(60)]
    pred1 = {'ra': ra0, 'dec': dec0, 'smaa': 40.0, 'smia': 8.0, 'theta': 60.0}
    # true asteroid sits 18" east, 10" north of the prediction, moving 6"/15 min
    true_dx, true_dy = 18.0, 10.0
    x1 = n / 2 - true_dx / 0.25
    y1 = n / 2 + true_dy / 0.25
    x2, y2 = x1 - 24, y1 + 0  # moved 6" west in image 2
    pred2 = dict(pred1)
    ra_shift = 6.0 / 3600 / math.cos(math.radians(dec0))
    pred2['ra'] = ra0 + ra_shift   # prediction moves the same way
    img1 = star_image(n, stars + [(x1, y1, 900)], rng)
    img2 = star_image(n, stars + [(x2, y2, 900), (100.0, 300.0, 4000)], rng)
    img2[300, 100] += 5000  # cosmic ray (single sharp pixel)
    stack = star_image(n, stars, rng, noise=2.0)
    d1, d2, st = detect_sources(img1, hdr), detect_sources(img2, hdr), detect_sources(stack, hdr, thresh=2.0)
    c1 = moving_candidates(d1, st, pred1)
    c2 = moving_candidates(d2, st, pred2)
    links = link_pairs([{'mjd': 56000.0, 'cands': c1, 'exptime': 45}, {'mjd': 56000.0 + 15 / 1440, 'cands': c2, 'exptime': 45}])
    assert links, (len(c1), len(c2))
    b = links[0]
    assert abs(b['c1']['dx'] - true_dx) < 1.5 and abs(b['c1']['dy'] - true_dy) < 1.5, (b['c1']['dx'], b['c1']['dy'])
    assert mahal(0, 0, 10, 5, 0) == 0 and mahal(0, 10, 10, 5, 0) == 1.0
    ox, oy = offsets(ra0 + 1 / 3600, dec0, ra0, dec0)
    assert abs(float(ox) - math.cos(math.radians(dec0))) < 1e-6


@test
def precovery_report_and_review():
    from spacelab.precovery import ades_psv, obs_time_utc, make_review, parse_tsv, parse_ps1_table
    psv = ades_psv('2024 YR4', [{'obsTime': '2013-03-01T10:00:00.000Z', 'ra': 150.123456, 'dec': -5.5}], {'observer_name': 'Test Observer'})
    assert '! mpcCode F51' in psv and '|2024 YR4 |CCD |F51 |2013-03-01T10:00:00.000Z' in psv.replace(' |', '|').replace('|', ' |') or '2024 YR4' in psv
    assert psv.splitlines()[-1].count('|') == 9
    t = obs_time_utc({'MJD-OBS': 56352.4, 'EXPTIME': 45}, None, None)
    assert t.startswith('2013-03-0') and t.endswith('Z')
    rows = parse_tsv('Image\tMJD\tFilter\tExptime\tObject_RA\tObject_Dec\tImage_target\tTelescope/Instrument\tMetaData\tDatalink\n'
                     'o5530g\t56352.4\tr.0000\t45\t150.1\t-5.5\tx\tPan-STARRS1\tm\thttp://x\n')
    assert rows and rows[0]['MJD'] == '56352.4'
    ps = parse_ps1_table('projcell subcell ra dec filter mjd type filename shortname badflag\n'
                         '1784 59 83.6 22.0 r 55555.5 warp /rings.v3/a.fits a.fits 0\n')
    assert ps[0]['filename'] == '/rings.v3/a.fits'
    out = Path(tempfile.mkdtemp())
    rng = np.random.default_rng(1)
    fr = [rng.normal(0, 1, (120, 120)) for _ in range(3)]
    make_review(out / 'r.png', out / 'r.gif', 'test', fr, ['a', 'b', 'c'])
    assert (out / 'r.png').exists() and (out / 'r.gif').exists()


# ---------------------------------------------------------------- Exoplanet Watch
@test
def exowatch_star_matching():
    from spacelab.exowatch import detect, choose_stars
    rng = np.random.default_rng(11)
    ra0, dec0 = 31.04, 46.69
    n_h, n_w, scale = 500, 650, 5.21  # arcsec/px
    cat = []
    for k in range(70):
        dxa, dya = rng.uniform(-1600, 1600), rng.uniform(-1200, 1200)
        cat.append({'ra': ra0 + dxa / 3600 / math.cos(math.radians(dec0)), 'dec': dec0 + dya / 3600,
                    'g': float(rng.uniform(9.5, 14.0)), 'var': 'NOT_AVAILABLE'})
    cat.append({'ra': ra0, 'dec': dec0, 'g': 11.2, 'var': ''})  # the planet host
    rot = math.radians(23.0)
    cx, cy = 340.0, 240.0
    pix = []
    for s in cat:
        xi = (s['ra'] - ra0) * math.cos(math.radians(dec0)) * 3600
        eta = (s['dec'] - dec0) * 3600
        xi = -xi  # mirror flip, as many cameras have
        x = cx + (xi * math.cos(rot) - eta * math.sin(rot)) / scale
        y = cy + (xi * math.sin(rot) + eta * math.cos(rot)) / scale
        f = 10 ** (-0.4 * (s['g'] - 9.0)) * 40000
        pix.append((x, y, f))
    inside = [(x, y, f) for x, y, f in pix if 3 < x < n_w - 3 and 3 < y < n_h - 3]
    yy, xx = np.mgrid[0:n_h, 0:n_w]
    img = rng.normal(300, 6, (n_h, n_w))
    for x, y, f in inside:
        img += f / (2 * math.pi * 1.6 ** 2) * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.6 ** 2))
    xy, fl, pk = detect(img)
    st = choose_stars(img, (ra0, dec0), cat, xy, fl, pk)
    assert abs(st['target'][0] - cx) <= 1 and abs(st['target'][1] - cy) <= 1, st
    assert abs(st['scale'] - scale) < 0.1, st['scale']
    assert len(st['comps']) >= 2


@test
def exowatch_inits_and_results():
    from spacelab import exowatch, config
    tmp = Path(tempfile.mkdtemp())
    planet = {'pl_name': 'HAT-P-32 b', 'hostname': 'HAT-P-32', 'ra': 31.0429, 'dec': 46.6878, 'pl_orbper': 2.15000825,
              'pl_orbpererr1': 1.2e-7, 'pl_tranmid': 2455867.402743, 'pl_tranmiderr1': 4.9e-5, 'pl_ratror': 0.1508,
              'pl_ratdor': 6.05, 'pl_orbincl': 88.98}
    cfg = config.load()
    p = exowatch.write_inits({'name': 'x'}, planet, {'target': [424, 286], 'comps': [[465, 183], [512, 263]], 'scale': 5.21},
                             cfg, {'DATE-OBS': '2017-12-20T05:00:00', 'EXPTIME': 60}, tmp, tmp)
    inits = json.loads(p.read_text())
    ui = inits['user_info']
    for key in ('Directory with FITS files', 'Directory to Save Plots', 'Target Star X & Y Pixel', 'Comparison Star(s) X & Y Pixel',
                'Plate Solution? (y/n)', 'Filter Name (aavso.org/filters)', 'Observation date', 'Add Comparison Stars from AAVSO? (y/n)'):
        assert key in ui, key
    assert ui['Observation date'] == 'December 20, 2017'
    assert inits['planetary_parameters']['Target Star Dec'].startswith('+46:41')
    assert inits['optional_info']['Pixel Scale (Ex: 5.21 arcsecs/pixel)'] == 5.21
    (tmp / 'temp').mkdir()
    (tmp / 'temp' / 'FinalParams_HAT-P-32 b_December 20, 2017.json').write_text(json.dumps({'FINAL PLANETARY PARAMETERS': {
        'Mid-Transit Time (Tmid)': '2458107.714007 +/- 0.000856 BJD_TDB', 'Transit depth (Rp/Rs)^2': '2.26 +/- 0.03 [%]',
        'Scatter in the residuals of the lightcurve fit is': '0.5414 %', 'Transit Duration (day)': '0.1 +/- 0.002'}}))
    (tmp / 'AAVSO_HAT-P-32 b_December 20, 2017.txt').write_text('#TYPE=EXOPLANET')
    r = exowatch.read_results(tmp)
    assert abs(r['tmid'] - 2458107.714007) < 1e-9 and r['scatter_pct'] == 0.5414 and r['aavso']
    oc = exowatch.o_minus_c(r, planet)
    assert oc and abs(oc['oc_min']) < 60, oc
    good, notes = exowatch.quality(r, oc)
    assert good, notes
    assert exowatch.PLANET_RE.search('Target: hat-p-32 b observed').group(0)


@test
def exowatch_plate_solve_wide_catalog():
    """The catalog circle (0.75 deg radius) is much bigger than a MicroObservatory frame; the solver must still match."""
    from spacelab import exowatch
    rng = np.random.default_rng(7)
    n = 550
    r = 0.75 * np.sqrt(rng.random(n)); th = rng.random(n) * 2 * np.pi
    ra0, dec0 = 173.96, -29.16
    cat = [{'ra': ra0 + r[i] * np.cos(th[i]) / np.cos(np.radians(dec0)), 'dec': dec0 + r[i] * np.sin(th[i]),
            'g': float(rng.uniform(8.5, 14.5)), 'var': ''} for i in range(n)]
    cat.sort(key=lambda s: s['g'])
    cxy = exowatch.project(cat, ra0, dec0)
    scale, rot = 5.17, np.radians(179.6)
    R = np.array([[np.cos(rot), -np.sin(rot)], [np.sin(rot), np.cos(rot)]])
    pix = (cxy / scale) @ R.T + np.array([300.0, 250.0])
    inside = (pix[:, 0] > 5) & (pix[:, 0] < 645) & (pix[:, 1] > 5) & (pix[:, 1] < 495)
    img_xy = pix[inside] + rng.normal(0, 0.3, (inside.sum(), 2))
    keep = rng.random(len(img_xy)) > 0.15                 # some stars missed by detection
    sol = exowatch.solve_field(img_xy[keep], cxy)
    assert sol is not None and abs(sol[3] - scale) < 0.1 and sol[2] >= 10, sol and sol[2:]


@test
def exowatch_first_frame_and_darks():
    from astropy.io import fits
    from spacelab import exowatch
    d = Path(tempfile.mkdtemp())
    (d / 'darks').mkdir()
    rng = np.random.default_rng(3)
    dark = np.full((60, 80), 100.0)
    for i, (x, hh) in enumerate(((10, '04:52:38'), (30, '04:55:00'), (50, '04:58:00'))):
        img = dark + rng.normal(0, 1, dark.shape)
        img[20, x] += 500          # the star drifts between frames
        h = fits.Header(); h['DATE-OBS'] = f'2020-04-16T{hh}'
        fits.writeto(d / f'f{2 - i}.fits', img, h)          # file names in the opposite order to time
    fits.writeto(d / 'darks' / 'Dark-1.fits', dark)
    img, hdr, files = exowatch.read_frames(d)
    assert hdr['DATE-OBS'].endswith('04:52:38'), hdr['DATE-OBS']
    assert np.argmax(img[20]) == 10 and abs(np.median(img)) < 1, (np.argmax(img[20]), np.median(img))
    assert exowatch.calib_dirs(d)['darks'].name == 'darks'
    r = {'depth_pct': 0.0, 'tmid_err': 0.0097, 'scatter_pct': 90}
    good, notes = exowatch.quality(r, None, {'pl_ratror': 0.132})
    assert not good and r['usable'] is False and any('no clear transit' in n for n in notes), notes


@test
def exowatch_planet_name_from_readme():
    from spacelab import exowatch
    d = Path(tempfile.mkdtemp())
    (d / 'README.txt').write_text('MicroObservatory data\nTarget: WASP-12b\nDate 2021-01-01')
    assert exowatch.find_planet_name(d) == 'WASP-12 b', exowatch.find_planet_name(d)


# ---------------------------------------------------------------- new sources
@test
def ztf_ipac_parse():
    from spacelab.ztf import parse_ipac
    txt = """\\ fixlen = T
|   ra_obj|  dec_obj| vmag|      filefracday|field|ccdid|qid|filtercode|imgtypecode|        obsjd|
|   double|   double|double|            long|  int|  int|int|      char|       char|       double|
  150.1234   12.3456  19.8  20190312234567   612     5   2         zr           o 2458555.4345
"""
    rows = parse_ipac(txt)
    assert len(rows) == 1, rows
    r = rows[0]
    assert abs(float(r['ra_obj']) - 150.1234) < 1e-6 and str(r['filtercode']).strip() == 'zr', r


@test
def library_parse_and_search():
    from spacelab import library
    xml = b"""<?xml version="1.0"?><OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>
    <record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>1001.0001</id><created>2010-01-02</created>
      <authors><author><keyname>Smith</keyname><forenames>A.</forenames></author></authors>
      <title>Orbital decay of the hot  Jupiter WASP-12b</title><categories>astro-ph.EP</categories>
      <abstract>We measure the orbital decay of WASP-12b from transit timing.</abstract></arXiv></metadata></record>
    <record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>astro-ph/0301001</id><created>2003-01-01</created>
      <title>Too old</title><categories>astro-ph</categories><abstract>x</abstract></arXiv></metadata></record>
    <record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>2401.0002</id><created>2024-01-05</created>
      <title>Dark matter halos of dwarf galaxies</title><categories>astro-ph.GA</categories>
      <abstract>Rotation curves of dwarf galaxies constrain cores.</abstract></arXiv></metadata></record>
    <resumptionToken completeListSize="3">tok123</resumptionToken></ListRecords></OAI-PMH>"""
    recs, tok, total = library.parse_records(xml, 2006)
    assert [r['id'] for r in recs] == ['1001.0001', '2401.0002'] and tok == 'tok123' and total == '3', (recs, tok, total)
    assert recs[0]['title'] == 'Orbital decay of the hot Jupiter WASP-12b' and recs[0]['authors'] == 'A. Smith'
    con = library.connect()
    for r in recs:
        con.execute('INSERT OR IGNORE INTO papers(id,title,authors,abstract,cats,year,created,doi) VALUES(?,?,?,?,?,?,?,?)',
                    (r['id'], r['title'], r['authors'], r['abstract'], r['cats'], r['year'], r['created'], r['doi']))
    con.commit()

    class FakeLLM:
        def embed(self, texts):
            return [[1.0 if ('decay' in t.lower() or 'jupiter' in t.lower()) else 0.0, 1.0 if 'galax' in t.lower() else 0.0] + [0.01] * (library.DIM - 2) for t in texts]
    library.embed_pending(con, FakeLLM(), print, time.time() + 30)
    st = library.status(con)
    assert st['papers'] == 2 and st['embedded'] == 2, st
    res = library.search('hot Jupiter orbital decay', FakeLLM())
    assert res and res[0]['id'] == '1001.0001', res
    assert library.search('hot Jupiter orbital decay', FakeLLM(), y0=2020) == [] or library.search('hot Jupiter orbital decay', FakeLLM(), y0=2020)[0]['year'] >= 2020
    assert all(r['year'] <= 2015 for r in library.search('galaxies', FakeLLM(), y1=2015))


@test
def news_parse():
    from spacelab import news
    rss = """<?xml version="1.0"?><rss version="2.0"><channel><title>AAS Nova</title>
      <item><title>A planet that should not exist</title><link>https://aasnova.org/2026/11/01/x/</link>
      <pubDate>Sun, 01 Nov 2026 12:00:00 +0000</pubDate><description>Text</description></item></channel></rss>"""
    items = news.parse(rss, 'AAS Nova')
    assert len(items) == 1 and items[0]['source'] == 'AAS Nova' and items[0]['url'].startswith('https://aasnova.org'), items
    atom = """<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Atom item</title>
      <link href="https://example.org/a"/><updated>2026-11-02T00:00:00Z</updated></entry></feed>"""
    items = news.parse(atom, 'Example')
    assert len(items) == 1 and items[0]['url'] == 'https://example.org/a', items


@test
def ephem_exoclock_units():
    from spacelab import ephem
    assert abs(ephem._deg('12:30:00', hours=True) - 187.5) < 1e-6
    assert abs(ephem._deg('-05:30:00') + 5.5) < 1e-6
    assert ephem.norm('WASP-12 b') == ephem.norm('wasp12b')


@test
def fpp_verdicts():
    from spacelab import fpp
    assert fpp.verdict(0.01, 0.0001) == 'passes the statistical validation thresholds'
    assert fpp.verdict(0.3, 0.0005) == 'likely planet'
    assert fpp.verdict(0.3, 0.2).startswith('likely a nearby')
    assert fpp.verdict(None, None) == 'not computed'


@test
def library_arxiv_api_fallback():
    from spacelab import library
    atom = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry><id>http://arxiv.org/abs/2101.01234v2</id><published>2021-01-04T19:00:01Z</published>
    <title>A  warm Neptune
      around an M dwarf</title><summary>We report a transiting planet.</summary>
    <author><name>A. Example</name></author><author><name>B. Sample</name></author>
    <arxiv:doi>10.3847/1538-3881/abc123</arxiv:doi>
    <category term="astro-ph.EP" scheme="http://arxiv.org/schemas/atom"/><category term="astro-ph.SR"/></entry>
  <entry><id>http://arxiv.org/abs/astro-ph/0101001v1</id><published>2001-01-01T00:00:00Z</published>
    <title>Old style id</title><summary>x</summary><author><name>C. Old</name></author></entry>
</feed>"""
    recs = library.parse_api(atom)
    assert [r['id'] for r in recs] == ['2101.01234', 'astro-ph/0101001'], recs
    r = recs[0]
    assert r['title'] == 'A warm Neptune around an M dwarf' and r['created'] == '2021-01-04' and r['year'] == 2021
    assert r['authors'] == 'A. Example, B. Sample' and r['doi'] == '10.3847/1538-3881/abc123' and 'astro-ph.EP' in r['cats']
    assert library._next_month('2021-12') == '2022-01' and library._next_month('2021-01') == '2021-02'


@test
def research_in_news_links():
    from spacelab import news
    page = """<a href="https://arxiv.org/abs/2611.01234">paper</a> <a href="https://www.nature.com/articles/s41550-026-02123-x">Nature Astronomy</a>
      <a href='https://iopscience.iop.org/article/10.3847/1538-4357/ad1234/meta'>ApJ</a> <a href="https://doi.org/10.1126/science.abc1234.">Science</a>
      <a href="https://arxiv.org/pdf/2611.01234v2">again</a> <a href="https://www.space.com/other-story">not a paper</a>"""
    refs = news.cited_papers(page)
    assert refs == [('arxiv', '2611.01234'), ('doi', '10.1038/s41550-026-02123-x'), ('doi', '10.3847/1538-4357/ad1234'),
                    ('doi', '10.1126/science.abc1234')], refs


@test
def openalex_records_and_kv_clear():
    from spacelab import library
    js = {'meta': {'count': 2, 'next_cursor': 'abc'}, 'results': [
        {'id': 'https://openalex.org/W1', 'doi': 'https://doi.org/10.1086/167900', 'display_name': 'Extinction  law',
         'publication_year': 1989, 'publication_date': '1989-10-01',
         'authorships': [{'author': {'display_name': 'J. Cardelli'}}],
         'abstract_inverted_index': {'The': [0], 'extinction': [1, 4], 'law': [2], 'and': [3], 'depends': [5], 'on': [6], 'one': [7],
                                     'parameter': [8], 'across': [9], 'the': [10], 'infrared': [11], 'optical': [12], 'ultraviolet.': [13]},
         'primary_topic': {'subfield': {'display_name': 'Astronomy and Astrophysics'}}},
        {'id': 'https://openalex.org/W2', 'display_name': 'No abstract', 'publication_year': 1980, 'abstract_inverted_index': None}]}
    recs, cur, total = library.oa_records(js)
    assert len(recs) == 1 and recs[0]['id'] == 'W1' and recs[0]['doi'] == '10.1086/167900' and cur == 'abc' and total == 2, recs
    assert recs[0]['abstract'].startswith('The extinction law and extinction depends'), recs[0]['abstract']
    assert library.paper_url({'id': 'W1', 'doi': '10.1086/167900'}) == 'https://doi.org/10.1086/167900'
    assert library.paper_url({'id': '1001.0001', 'doi': ''}) == 'https://arxiv.org/abs/1001.0001'
    con = library.connect()
    library.kv(con, 'token', 'xyz'); library.kv(con, 'token', None)
    assert library.kv(con, 'token') is None


# ---------------------------------------------------------------- publishing safety
@test
def publish_schema_and_privacy():
    from spacelab import publish, db, config
    cfg = config.load() if hasattr(config, 'load') else {}
    cfg = dict(cfg, contact_email='secret.person@example.com', observer_name='Secret Person',
               telescope={'lat': '40.12345', 'lon': '-74.54321'}, share={'public_name': 'Space Lab tester'})
    con = db.connect()
    data, files = publish.build_data(con, cfg)
    assert publish.validate(data) == [], publish.validate(data)
    bad = dict(data); bad.pop('tess')
    assert publish.validate(bad), 'missing tess must be refused'
    bad = json.loads(json.dumps(data, default=str)); bad['tess']['stats'] = []
    assert publish.validate(bad), 'wrong type must be refused'
    d = Path(tempfile.mkdtemp())
    (d / 'data.json').write_text(json.dumps({'note': 'contact secret.person@example.com at 40.12345'}))
    hits = publish.scan(d, cfg)
    assert any('email' in h for h in hits) and any('latitude' in h for h in hits), hits
    cfg2 = dict(cfg, aavso_code='QXZ', vstars={'site': {'lat': 35.67891, 'lon': -105.98765}, 'targettool_key': 'tt-secret-key-123'})
    (d / 'data.json').write_text(json.dumps({'a': 'obs QXZ here', 'b': 'QXZQ is fine', 'c': 'site 35.67891', 'd': 'key tt-secret-key-123', 'e': 'host 192.168.1.20',
                                             'f': 'version 1.10.0.5 and galaxy 20854'}))
    labels = ' | '.join(publish.scan(d, cfg2))
    assert all(x in labels for x in ('AAVSO observer code', 'observatory latitude', 'Target Tool key', 'private) IP')) and labels.count('AAVSO observer code') == 1, labels
    (d / 'data.json').write_text(json.dumps({'f': 'version 1.10.0.5 and galaxy 20854', 'g': 'XQXZ'}))
    assert publish.scan(d, cfg2) == [], publish.scan(d, cfg2)
    (d / 'data.json').write_text(json.dumps(data, default=str))
    assert publish.scan(d, cfg) == [], publish.scan(d, cfg)


@test
def publish_git_push_local():
    import subprocess
    from spacelab import publish
    site, remote = Path(tempfile.mkdtemp()), Path(tempfile.mkdtemp()) / 'remote.git'
    subprocess.run(['git', 'init', '--bare', '-q', str(remote)], check=True)
    (site / 'index.html').write_text('<!doctype html><title>t</title>')
    (site / 'data.json').write_text('{}')
    assert publish.git_push(site, str(remote), print)
    out = subprocess.run(['git', '--git-dir', str(remote), 'ls-tree', '--name-only', 'gh-pages'], capture_output=True, text=True).stdout.split()
    assert 'index.html' in out and 'data.json' in out, out


# ---------------------------------------------------------------- server + db
@test
def server_endpoints():
    import threading
    import urllib.request
    from http.server import ThreadingHTTPServer
    from spacelab import server, db
    con = db.connect()
    tid = db.add_task(con, 'test:1', 'tess', 'review', 'Test task', 'detail', {'tab': 'tess', 'id': 1})
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.H)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{port}'
    st = json.loads(urllib.request.urlopen(base + '/api/state').read())
    assert any(t['id'] == tid for t in st['tasks'])
    for path in ('/', '/web/app.js', '/api/arxiv', '/api/tess', '/api/exo', '/api/neo', '/api/logs?job=server', '/api/settings',
                 '/api/public-data', '/api/export/eclipsing-binaries.csv', '/web/extras.js', '/web/beyond.js'):
        assert urllib.request.urlopen(base + path).status == 200, path
    try:
        urllib.request.urlopen(urllib.request.Request(base + '/api/task/%d' % tid, data=b'{"action":"done"}', method='POST'))
        raise AssertionError('POST without header must be refused')
    except urllib.error.HTTPError as e:
        assert e.code == 403
    req = urllib.request.Request(base + '/api/task/%d' % tid, data=b'{"action":"done"}', method='POST',
                                 headers={'X-SpaceLab': '1', 'Content-Type': 'application/json'})
    assert json.loads(urllib.request.urlopen(req).read())['ok']
    assert db.one(con, 'SELECT status FROM tasks WHERE id=?', (tid,))['status'] == 'done'
    try:
        urllib.request.urlopen(base + '/files/../spacelab/db.py')
        raise AssertionError('path traversal not blocked')
    except urllib.error.HTTPError as e:
        assert e.code in (403, 404)
    httpd.shutdown()
    con.execute("DELETE FROM tasks WHERE key='test:1'")


# ---------------------------------------------------------------- Beyond astronomy, community, CTOI, exports
@test
def beyond_offline_parts():
    from spacelab import beyond
    f = beyond.parse_issue('### TIC number\n\n25799110\n\n### What do you think it is?\n\nPlanet-like\n\n### Why?\n\n_No response_')
    assert f['tic number'] == '25799110' and f['what do you think it is?'] == 'Planet-like', f
    assert beyond._rows([['time_tag', 'Kp'], ['2026-11-08 00:00', '3.33']]) == [{'time_tag': '2026-11-08 00:00', 'Kp': '3.33'}]
    sky = beyond.sky_week(print)
    if sky is not None:      # needs astropy's built-in ephemeris (no download)
        assert 0 <= sky['moon']['illumination'] <= 1 and len(sky['planets']) == 5
        assert all(-3 <= s['days'] <= 30 for s in sky['showers'])
    assert beyond._jpeg(b'\xff\xd8\xff' + b'0' * 30000) and not beyond._jpeg(b'<html>error</html>')


@test
def beyond_run_keeps_last_good_copy():
    from spacelab import beyond, db
    con = db.connect()
    db.kv_set(con, 'beyond', {'comets': [{'name': 'C/2026 A1 (Test)'}]})
    fail = lambda *a, **k: None
    saved = {n: getattr(beyond, n) for n in ('space_weather', 'fireballs', 'comets', 'gravitational_waves', 'supernovae',
                                            'launches', 'habitable_worlds', 'sky_week', 'earth_images', 'iss_track', 'earthquakes',
                                            'satellites', 'dark_sky_map', 'zip_index', 'live_images', 'asteroid_wiki',
                                            'community', 'backup_db', 'clean_and_measure', 'sn_nearby', 'flyby_orbits', 'earth_orbits')}
    try:
        for n in saved:
            setattr(beyond, n, fail)
        beyond.fireballs = lambda log: [{'date': '2026-11-01', 'energy_kt': 0.1, 'lat': 12.3, 'lon': -45.6}]
        ctx = type('C', (), {'con': con, 'log': print, 'cfg': {}})()
        msg = beyond.run(ctx)
        b = db.kv_get(con, 'beyond')
        assert b['comets'][0]['name'] == 'C/2026 A1 (Test)', 'a failed source must keep its previous data'
        assert b['fireballs'][0]['lat'] == 12.3 and 'fireballs' in msg, msg
    finally:
        for n, fn in saved.items():
            setattr(beyond, n, fn)


@test
def ctoi_draft_rules():
    from spacelab import server
    c = {'kind': 'periodic', 'tic': 123, 'period': 3.5, 't0': 1500.25, 'depth': 0.002, 'duration': 0.1, 'flags': [], 'fpp': {'fpp': 0.01, 'nfpp': 0.001}}
    d = server.ctoi_draft(c)
    assert d and abs(d['epoch_bjd_tdb'] - 2458500.25) < 1e-6 and d['depth_ppm'] == 2000, d
    assert server.ctoi_draft(dict(c, flags=['odd and even dips differ'])) is None
    assert server.ctoi_draft(dict(c, fpp={'nfpp': 0.5})) is None
    assert server.ctoi_draft(dict(c, kind='single')) is None


@test
def eb_export_and_contributions():
    from spacelab import export, db
    con = db.connect()
    con.execute("INSERT INTO tess_cands (tic, kind, period, t0, depth, duration, flags, status, created) VALUES (?,?,?,?,?,?,?,?,?)",
                (987654321, 'periodic', 2.5, 1600.0, 0.01, 0.08, '["odd and even dips differ"]', 'fake', time.time()))
    txt, n = export.eb_csv(con)
    assert n >= 1 and '987654321' in txt and 'odd/even' in txt, txt
    c = db.contributions(con)
    assert c['tess_reviewed'] >= 1 and set(c) >= {'stars_searched', 'exowatch_reduced'}, c
    con.execute('DELETE FROM tess_cands WHERE tic=987654321')


@test
def tracked_paper_mentions():
    from spacelab import arxiv_digest, db
    con = db.connect()
    con.execute("INSERT INTO tess_cands (tic, kind, period, t0, depth, duration, flags, status, created) VALUES (?,?,?,?,?,?,?,?,?)",
                (55511122, 'periodic', 2.5, 1600.0, 0.01, 0.08, '[]', 'new', time.time()))
    papers = [{'id': '2611.00001', 'title': 'A planet around TIC 55511122', 'abstract': 'We confirm it.', 'url': 'https://arxiv.org/abs/2611.00001'},
              {'id': '2611.00002', 'title': 'Galaxies', 'abstract': 'Nothing tracked.', 'url': 'https://arxiv.org/abs/2611.00002'}]
    hits = arxiv_digest.tracked_mentions(con, papers)
    assert [h['id'] for h in hits] == ['2611.00001'], hits
    assert arxiv_digest.tracked_mentions(con, papers) == [], 'the same paper must not be reported twice'
    con.execute('DELETE FROM tess_cands WHERE tic=55511122')
    con.execute("DELETE FROM tasks WHERE key LIKE 'arxiv:tracked:%'")


# ---------------------------------------------------------------- radar, candidate of the day, results, website extras
@test
def radar_merge_and_fallback_scoring():
    from spacelab import radar
    items = [{'source': 'NASA', 'title': 'Webb finds water vapour on warm exoplanet K2-18 b', 'url': 'https://example.com/1', 'date': 2e9, 'summary': 'x'},
             {'source': 'Space.com', 'title': 'Water vapour found on warm exoplanet K2-18 b by Webb', 'url': 'https://example.com/2', 'date': 2e9 - 10, 'summary': 'y'},
             {'source': 'ESA', 'title': 'Solar flare sparks aurora alert', 'url': 'https://example.com/3', 'date': 2e9 - 20, 'summary': 'z'}]
    m = radar.merge_duplicates(items)
    assert len(m) == 2 and m[0]['sources'] == ['NASA', 'Space.com'], m
    k = radar.keyword_score(m[0])
    assert k['topic'] == 'Exoplanets' and k['score'] >= 5, k
    assert radar.keyword_score(m[1])['topic'] == 'Sun & space weather'


@test
def radar_run_offline():
    from spacelab import radar, net, db, llm
    con = db.connect()
    con.execute("INSERT INTO tess_cands (tic, kind, period, t0, depth, duration, flags, status, plot, snr, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (44455566, 'periodic', 2.5, 1600.0, 0.002, 0.08, '[]', 'new', 'plots/tess/44455566.png', 12.0, time.time()))
    rss = ('<?xml version="1.0"?><rss><channel><item><title>Astronomers revisit TIC 44455566</title><link>https://example.com/tic</link>'
           '<pubDate>{d}</pubDate><description>A new look &nbsp; at a star.</description></item>'
           '<item><title>Rocket launch delayed</title><link>https://example.com/launch</link><pubDate>{d}</pubDate></item></channel></rss>')
    from email.utils import formatdate
    old_fetch, old_sources, old_models = net.fetch_text, radar.SOURCES, llm.LLM.models
    try:
        net.fetch_text = lambda url, **k: rss.format(d=formatdate())
        radar.SOURCES = [('Example feed', 'https://example.com/feed')]
        llm.LLM.models = lambda self: []                  # no Ollama in tests: keyword scoring
        ctx = type('C', (), {'con': con, 'log': print, 'cfg': __import__('spacelab.config', fromlist=['x']).load(), 'time_left': lambda self: 1e9})()
        msg = radar.run(ctx)
        r = db.kv_get(con, 'radar')
        assert len(r['items']) >= 2 and 'read 2 new items' in msg, (msg, r)
        tic = next(x for x in r['items'] if 'TIC' in x['title'])
        assert tic['lab'], 'an item about one of your own targets must be flagged'
        assert radar.run(ctx).startswith('read 0 new'), 'items already read must not be read again'
        d = db.kv_get(con, 'daily')
        assert d and d.get('tic') == 44455566 and d.get('blurb'), d
    finally:
        net.fetch_text, radar.SOURCES, llm.LLM.models = old_fetch, old_sources, old_models
        con.execute('DELETE FROM tess_cands WHERE tic=44455566')
        con.execute("DELETE FROM tasks WHERE key LIKE 'radar:%'")


@test
def news_parse_tolerates_html_entities():
    from spacelab import news
    xml = '<?xml version="1.0"?><rss><channel><item><title>Moon &amp; Mars&nbsp;news &mdash; today</title><link>https://example.com/x</link></item></channel></rss>'
    it = news.parse(xml, 'Example')
    assert it[0]['title'] == 'Moon & Mars news — today', it
    it = news.parse('<rss><channel><item><title>Q&A with an astronaut</title><link>https://example.com/q</link></item></channel></rss>', 'Example')
    assert it[0]['title'] == 'Q&A with an astronaut', it


@test
def daily_pick_rollover_and_votes():
    from spacelab import daily, db, net
    con = db.connect()
    for tic, snr in ((71000001, 20.0), (71000002, 18.0)):
        con.execute("INSERT INTO tess_cands (tic, kind, period, t0, depth, duration, flags, status, plot, snr, created) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (tic, 'periodic', 3.0, 1600.0, 0.003, 0.1, '[]', 'new', f'plots/tess/{tic}.png', snr, time.time()))
    db.kv_set(con, 'daily', {'date': '2000-01-01', 'tic': 71000001})
    db.kv_set(con, 'community', {'tess': {'71000001': {'Planet-like': 2}}})
    cfg = {'share': {'goatcounter': 'example-lab', 'goatcounter_token': 'secret-key-example'}}
    old = net.fetch_json
    try:
        def fake(url, params=None, headers=None, **k):
            assert headers['Authorization'] == 'Bearer secret-key-example'
            if url.endswith('/stats/total'):
                return {'total': 42, 'total_events': 7}
            day = params['start']
            return {'hits': [{'path': f'vote-{day}-planet-like', 'count': 5, 'event': True}, {'path': f'vote-{day}-noise', 'count': 1, 'event': True},
                             {'path': '/tess', 'count': 30}]}
        net.fetch_json = fake
        d = daily.ensure(con, cfg, print, None)
        assert d['date'] == daily.today() and d['prev']['tic'] == 71000001 and d['tic'] == 71000002, d
        assert daily.ensure(con, cfg, print, None)['tic'] == d['tic'], 'the pick must stay the same all day'
        pub = daily.public(con)
        assert pub['votes'] == {'Planet-like': 5, 'Noise / instrument glitch': 1}, pub['votes']
        assert pub['prev']['github'] == {'Planet-like': 2} and pub['prev']['volunteer'] == 'not reviewed yet', pub['prev']
        assert db.kv_get(con, 'visitors')['visitors'] == 42
    finally:
        net.fetch_json = old
        con.execute('DELETE FROM tess_cands WHERE tic IN (71000001, 71000002)')
        for k in ('daily', 'daily_history', 'daily_votes', 'visitors', 'community'):
            con.execute('DELETE FROM kv WHERE k=?', (k,))


@test
def results_log():
    from spacelab import db
    con = db.connect()
    rid = db.save_result(con, {'key': 'ctoi:123', 'kind': 'ctoi', 'target': 'TIC 123', 'title': 'TIC 123 reported', 'link': 'javascript:alert(1)'})
    assert db.save_result(con, {'key': 'ctoi:123', 'kind': 'ctoi', 'title': 'again'}) == rid, 'same key must not make a second entry'
    r = db.one(con, 'SELECT * FROM results WHERE id=?', (rid,))
    assert r['status'] == 'submitted' and r['link'] == '' and r['public'] == 1, r
    db.save_result(con, {'id': rid, 'kind': 'ctoi', 'title': 'TIC 123 reported', 'status': 'confirmed', 'public': False})
    assert db.one(con, 'SELECT status, public FROM results WHERE id=?', (rid,)) == {'status': 'confirmed', 'public': 0}
    con.execute("INSERT INTO exo (name, planet, obs_date, status, created, updated) VALUES ('ds-test', 'WASP-12 b', '2026-10-30', 'uploaded', 1, 1)")
    db.sync_results(con)
    assert db.one(con, "SELECT kind FROM results WHERE key LIKE 'exo:%'")['kind'] == 'aavso'
    con.execute("DELETE FROM results"); con.execute("DELETE FROM exo WHERE name='ds-test'")


@test
def website_extras_and_privacy():
    from spacelab import site, publish
    data = {'site': {'url': 'https://example.github.io/Space-Lab/'}, 'tess': {'cands': [{'tic': 123, 'period': 2.5, 'depth': 0.002, 'rp': 1.4,
            'plot': 'files/plots/tess/123.png', 'created': 1.7e9, 'explain': 'x'}]},
            'daily': {'date': '2026-11-09', 'cand': {'tic': 123, 'period': 2.5, 'rp': 1.4, 'plot': None}, 'blurb': 'b'},
            'results': [], 'radar': {'items': [{'score': 8, 'title': 'Big <news>', 'url': 'https://example.com/n', 'date': 1.7e9, 'topic': 'Stars', 'summary': 's'}]}}
    cfg = {'share': {'goatcounter': 'example-lab', 'goatcounter_token': 'secret-key-example'}}
    tags = site.head_tags(data, cfg, publish.TITLE)
    assert 'og:image' in tags and 'https://example.github.io/Space-Lab/og.png' in tags and 'example-lab.goatcounter.com/count' in tags
    assert 'secret-key-example' not in tags
    assert 'goatcounter' not in site.head_tags(data, {'share': {'goatcounter': 'bad code!'}}, 'x'), 'only valid codes'
    page = site.share_page(data['tess']['cands'][0], data['site']['url'])
    assert 'og:image" content="https://example.github.io/Space-Lab/files/plots/tess/123.png' in page and '#tic-123' in page
    fd = site.feed(data)
    assert 'Big &lt;news&gt;' in fd and 'c/123.html' in fd and fd.startswith('<?xml')
    import xml.etree.ElementTree as ET
    ET.fromstring(fd); ET.fromstring(site.sitemap(data))
    assert 'your visitor-counter key' in publish.forbidden(dict(cfg))
    d = Path(tempfile.mkdtemp())
    (d / 'index.html').write_text('<html><head><title>t</title></head><body></body></html>')
    site.build(d, data, cfg, d, publish.TITLE, log=print)
    for name in ('feed.xml', 'sitemap.xml', 'robots.txt', 'manifest.webmanifest', 'c/123.html'):
        assert (d / name).is_file(), name
    assert 'og:title' in (d / 'index.html').read_text()
    try:
        import matplotlib  # noqa: F401
        assert (d / 'icon-192.png').is_file() and (d / 'og.png').is_file()
    except ImportError:
        pass
    assert publish.scan(d, cfg) == [], publish.scan(d, cfg)


# ---------------------------------------------------------------- stargazing data: events, dark-sky map, ZIP codes, satellites
@test
def events_and_calendar():
    from datetime import datetime, timezone
    from spacelab import beyond, site
    now = datetime(2026, 11, 9, tzinfo=timezone.utc)
    out = {'sky': {'moon_phases': [{'t': '2026-11-24T14:53Z', 'name': 'Full Moon'}, {'t': '2026-11-17T11:47Z', 'name': 'First quarter Moon'}]},
           'launches': {'upcoming': [{'name': 'Falcon 9 | Example', 'net': '2026-11-20T10:00:00Z', 'provider': 'SpaceX'}]},
           'comets': [{'name': 'C/2026 X1', 'days_to_perihelion': 30, 'm1': 9, 'q_au': 0.5}, {'name': 'faint', 'days_to_perihelion': 10, 'm1': 16}]}
    ev = beyond.build_events(out, now)
    kinds = [e['kind'] for e in ev]
    assert [e for e in ev if e['kind'] != 'mission'][0]['title'] == 'Leonids meteor shower peak' and kinds.count('moon') == 1 and 'launch' in kinds, ev[:4]
    assert sum(1 for e in ev if e['kind'] == 'comet') == 1, 'faint comets are left out'
    assert any(e['kind'] == 'eclipse' and e['date'] == '2027-08-02' for e in ev)
    assert all((e.get('t') or e.get('date')) >= '2026-11-08' for e in ev)
    cal = site.ics(ev, 'https://example.github.io/Space-Lab/')
    assert cal.startswith('BEGIN:VCALENDAR\r\n') and cal.count('BEGIN:VEVENT') == len(ev) and 'DTSTART;VALUE=DATE:20261117' in cal
    assert all(len(line.encode()) <= 75 for line in cal.split('\r\n')), 'calendar lines must be folded'
    assert '\\,' in cal and 'TRIGGER:-PT6H' in cal


@test
def moon_phases_are_right():
    from astropy.time import Time
    from spacelab import beyond
    ph = beyond.moon_phases(Time('2026-11-10T00:00'), days=30)
    full = [p for p in ph if p['name'] == 'Full Moon']
    assert full and full[0]['t'].startswith('2026-11-24T1'), ph       # Full Moon: 24 Nov 2026, 14:53 UTC


@test
def dark_sky_map_from_night_lights():
    import io
    import numpy as np
    from PIL import Image
    from spacelab import beyond, net, config
    W, H = 2360, 1040
    night = np.zeros((H, W), np.uint8); night[500:510, 1500:1510] = 255            # one bright city
    day = np.zeros((H, W, 3), np.uint8); day[...] = (60, 90, 40); day[1000:, :] = (10, 20, 60)   # land, water at the bottom
    jpg = lambda a: (lambda b: (Image.fromarray(a).save(b, 'PNG'), b.getvalue())[1])(io.BytesIO())
    old = net.fetch
    try:
        net.fetch = lambda url, params=None, **k: jpg(night if params['LAYERS'] == 'VIIRS_Black_Marble' else day) + b'0' * 20000
        r = beyond.dark_sky_map(print)
        assert r and r['w'] == W // 2 and r['water'] and len(r['cdf']) == 255, r
        img = np.asarray(Image.open(config.DATA / r['path']))
        assert img[(1000 + 10) // 2, 100] == 255, 'water must be marked 255'
        city, far = img[505 // 2, 1505 // 2], img[200 // 2, 200 // 2]
        assert city > far + 50, (city, far)
        assert beyond.dark_sky_map(print, r) is r, 'the map is reused for a month'
    finally:
        net.fetch = old


@test
def zip_codes_and_satellites():
    import io
    import zipfile
    from spacelab import beyond, net, config
    txt = 'GEOID\tALAND\tAWATER\tALAND_SQMI\tAWATER_SQMI\tINTPTLAT\tINTPTLONG \n12345\t1\t1\t1\t1\t40.000\t-105.000\n10001\t1\t1\t1\t1\t40.750\t-73.997\n'
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('2025_Gaz_zcta_national.txt', txt)
    old_f, old_j = net.fetch, net.fetch_json
    try:
        net.fetch = lambda url, **k: b.getvalue() if '2025' in url or '2026' in url else (_ for _ in ()).throw(RuntimeError('404'))
        r = beyond.zip_index(print)
        assert r and r['n'] == 2, r
        assert (config.DATA / r['path']).read_text() == '12345 4000 -10500\n10001 4075 -7400\n'
        net.fetch_json = lambda url, **k: [{'OBJECT_NAME': 'STARLINK-1007'}, {'OBJECT_NAME': 'ISS (ZARYA)'}, {'OBJECT_NAME': 'ONEWEB-0012'}]
        sat = beyond.satellites(print)
        assert sat['active'] == 3 and sat['starlink'] == 1 and sat['oneweb'] == 1
    finally:
        net.fetch, net.fetch_json = old_f, old_j


# ---------------------------------------------------------------- idle-time AI, live pictures, Wikipedia, suggestions
@test
def ai_runs_when_mac_is_idle():
    from spacelab import server, idle, db
    con = db.connect()
    old = idle.user_idle_seconds
    queued = []
    old_enq = server.enqueue
    try:
        server.enqueue = lambda job, why: queued.append((job, why)) or True
        idle.user_idle_seconds = lambda: 30.0
        assert not server.ai_when_idle({'ai_when_idle': True, 'idle_minutes': 5}, con), 'someone is using the Mac'
        idle.user_idle_seconds = lambda: 900.0
        assert not server.ai_when_idle({'ai_when_idle': False}, con), 'switched off in Setup'
        assert server.ai_when_idle({'ai_when_idle': True, 'idle_minutes': 5}, con) and queued[-1] == ('ai', 'Mac idle'), queued
    finally:
        idle.user_idle_seconds, server.enqueue = old, old_enq


@test
def live_pictures_keep_last_good_copy():
    from spacelab import beyond, net, config
    old = net.fetch
    try:
        net.fetch = lambda url, **k: b'\xff\xd8\xff' + b'0' * 30000
        r = beyond.live_images(print)
        assert r and {x['key'] for x in r} == {k for k, *_ in beyond.LIVE} and all((config.DATA / x['path']).is_file() for x in r)
        for x in r:
            x['t'] = 0                         # pretend they are old, then make every source fail
        net.fetch = lambda url, **k: (_ for _ in ()).throw(RuntimeError('offline'))
        r2 = beyond.live_images(print, r)
        assert len(r2) == len(r), 'when a source is down, the last picture stays'
    finally:
        net.fetch = old


@test
def wikipedia_summary_and_citations():
    from spacelab import beyond, net
    html = ('<p>x</p><cite class="citation journal cs1">Smith, J. (2021). "The orbit of Apophis". <i>Icarus</i>. '
            '<a href="https://doi.org/10.1016/j.icarus.2021.1">doi:10.1016</a></cite><cite class="citation web cs1">A web page</cite>'
            '<cite class="citation arxiv cs1">Lee, K. (2024). "Spin of Apophis". <a href="https://arxiv.org/abs/2401.00001">arXiv</a></cite>')
    old = net.fetch_json
    try:
        def fake(url, params=None, **k):
            assert 'SpaceLab' in k['headers']['User-Agent']
            if '/page/summary/' in url:
                return {'title': '99942 Apophis', 'extract': 'Apophis is an asteroid.', 'content_urls': {'desktop': {'page': 'https://en.wikipedia.org/wiki/99942_Apophis'}}}
            if params and params.get('list') == 'search':
                return {'query': {'search': [{'title': '2024 YR4'}]}}
            return {'parse': {'text': html}}
        net.fetch_json = fake
        pg = beyond.wiki_page('99942 Apophis', print)
        assert [c['year'] for c in pg['citations']] == [2024, 2021] and pg['citations'][1]['url'].startswith('https://doi.org/'), pg
        assert beyond.wiki_find('2024 YR4', print) == '2024 YR4' and beyond.wiki_find('2026 ZZ9', print) is None
    finally:
        net.fetch_json = old


@test
def suggestions_from_github():
    from spacelab import beyond, net
    issues = [{'title': 'Suggestion: Sky watch › Space weather', 'labels': [], 'user': {'login': 'a'}, 'html_url': 'https://github.com/x/y/issues/1',
               'created_at': '2026-11-09', 'state': 'open', 'body': '### Which part of the page?\n\nSky watch › Space weather\n\n### Your suggestion\n\nAdd a Kp alert'},
              {'title': 'TIC 123 review', 'labels': [], 'user': {'login': 'b'}, 'body': '### TIC number\n\n123\n\n### What do you think it is?\n\nPlanet-like'},
              {'title': 'A pull request', 'pull_request': {}, 'labels': [], 'user': {'login': 'c'}, 'body': ''}]
    old = net.fetch_json
    try:
        net.fetch_json = lambda url, params=None, **k: issues if params['page'] == 1 else []
        c = beyond.community(print, 'example/repo')
        assert c['suggestions']['Sky watch › Space weather'][0]['text'] == 'Add a Kp alert', c
        assert c['tess'] == {'123': {'Planet-like': 1}} and c['leaderboard'] == [{'user': 'b', 'reviews': 1}], c
    finally:
        net.fetch_json = old


@test
def missions_and_flyby_orbits():
    from datetime import datetime, timezone
    from spacelab import beyond, db, net, publish
    m = beyond.missions()
    assert m['checked'] and len(m['items']) >= 6, m
    for it in m['items']:
        assert all(it.get(k) for k in ('key', 'name', 'agency', 'status', 'what', 'here', 'url')), it
        assert it['url'].startswith('https://') and len(it.get('date') or '2026-01-01') in (7, 10), it
        assert ('date' in it) == ('next' in it), it
    ev = beyond.build_events({}, now=datetime(2026, 11, 1, tzinfo=timezone.utc))
    assert any(e['kind'] == 'mission' and e['date'] == '2026-11-12' and e['title'].startswith('Euclid') for e in ev), ev
    assert not any(e['kind'] == 'mission' and 'PLATO' in e['title'] for e in ev)       # month-only dates are not calendar events
    assert not any(e['kind'] == 'mission' for e in beyond.build_events({}, now=datetime(2030, 1, 1, tzinfo=timezone.utc)))
    con = db.connect()
    db.kv_set(con, 'neo_watch', {'cad': [{'des': '2099 AA', 'dist': '0.02'}, {'des': '2099 BB', 'dist': '0.01'}, {'des': '2099 CC', 'dist': '0.5'}]})
    asked, old = [], net.fetch_json

    def fake(url, params=None, **k):
        asked.append(params['sstr'])
        if params['sstr'] == '2099 CC':
            raise OSError('offline')
        return {'object': {'fullname': params['sstr']}, 'orbit': {'epoch': '2461000.5', 'elements': [
            {'name': 'a', 'value': '1.8'}, {'name': 'e', 'value': '0.5'}, {'name': 'i', 'value': '7.2'}, {'name': 'om', 'value': '40'},
            {'name': 'w', 'value': '100'}, {'name': 'q', 'value': '0.9'}]}}
    try:
        net.fetch_json = fake
        o = beyond.flyby_orbits(con, lambda *_: None)
    finally:
        net.fetch_json = old
    assert asked[0] == '2099 BB' and set(o) == {'2099 AA', '2099 BB'}, (asked, o)       # closest first; a failed lookup is skipped
    assert o['2099 BB'] == {'a': 1.8, 'e': 0.5, 'i': 7.2, 'om': 40.0, 'w': 100.0, 'q': 0.9}, o
    pub = publish._beyond_public({'missions': m, 'orbits': o, 'secret': 1})
    assert 'missions' in pub and 'orbits' in pub and 'secret' not in pub
    assert 'atlas.js' in publish.SITE_FILES and 'atlas-frame.html' in publish.SITE_FILES


def _cheops_visit(depth=0.004, t_mid=2460000.40, dur=0.10, seed=3, n=1400, span=0.8):
    """A synthetic CHEOPS visit: 99-minute roll-angle wobble, a slow drift, gaps every orbit, noise and one box transit."""
    rng = np.random.default_rng(seed)
    t = 2460000.0 + np.sort(rng.uniform(0, span, n))
    phase = ((t - 2460000.0) / (99 / 1440)) % 1
    keep = phase < 0.62                                   # Earth blocks the view for part of every orbit
    t, phase = t[keep], phase[keep]
    roll = phase * 360
    y = 1 + 4e-4 * np.sin(np.radians(roll)) + 2e-4 * np.cos(2 * np.radians(roll)) + 6e-4 * (t - t[0]) + rng.normal(0, 3e-4, len(t))
    if depth:
        y[np.abs(t - t_mid) < dur / 2] -= depth
    return {'obj_date_bjd_vect': list(t - 2400000.0), 'photom_flux_vect': list(y * 1.4e8), 'photom_event_vect': [0] * len(t),
            'photom_roll_angle_vect': list(roll), 'photom_background_vect': [1000.0] * len(t)}


@test
def cheops_blind_search_recovers_known_transit():
    from spacelab import cheops
    pl = {'name': 'Test-1 b', 'period': 2.0, 'period_err': 1e-6, 't0': 2460000.40 - 50 * 2.0, 't0_err': 2e-4, 'dur_h': 2.4, 'depth': 0.004,
          'ra': 10.0, 'dec': 20.0}
    hit = cheops.transit_in_visit(pl, 2460000.0, 2460000.8)
    assert hit and abs(hit[0] - 2460000.40) < 1e-6 and hit[1] < 0.001, hit
    assert cheops.transit_in_visit(pl, 2460000.0, 2460000.45) is None          # the transit must be fully inside, with data on both sides
    p = _cheops_visit()
    p['photom_event_vect'][5] = 1
    p['photom_flux_vect'][9] *= 1.5                                            # a cosmic-ray spike
    t, y, roll = cheops.clean(p)
    assert len(t) == len(p['photom_flux_vect']) - 2 and abs(np.median(y) - 1) < 1e-6
    dip, res, sig = cheops.blind_dip(t, y, roll)
    out = cheops.compare(pl, hit[0], hit[1], dip)
    assert out['recovered'] and out['depth_ok'], out
    assert abs(out['offset_min']) < 15 and 0.85 < out['ratio'] < 1.15 and out['snr'] > 20, out
    assert 200 < sig * 1e6 < 400, sig                                          # the wobble was removed: scatter is back to the noise level
    # no transit in the data: whatever the search finds must not count as a recovery
    t, y, roll = cheops.clean(_cheops_visit(depth=0))
    dip0, _, _ = cheops.blind_dip(t, y, roll)
    out0 = cheops.compare(pl, hit[0], hit[1], dip0)
    assert not out0['recovered'] and (dip0 is None or dip0['snr'] < 6), (out0, dip0)
    # a transit at a different time than published is found but not counted as recovered
    t, y, roll = cheops.clean(_cheops_visit(t_mid=2460000.25))
    dip1, _, _ = cheops.blind_dip(t, y, roll)
    out1 = cheops.compare(pl, hit[0], hit[1], dip1)
    assert dip1['snr'] > 20 and not out1['recovered'] and abs(out1['offset_min'] + 216) < 15, out1


@test
def cheops_job_end_to_end_offline():
    import spacelab.cheops as cheops
    from spacelab import db, net, publish, jobs
    con = db.connect()
    db.kv_set(con, 'cheops', None)
    nea = [{'pl_name': 'Test-1 b', 'hostname': 'Test-1', 'ra': 10.0, 'dec': 20.0, 'pl_orbper': 2.0, 'pl_orbpererr1': 1e-6, 'pl_tranmid': 2459900.40,
            'pl_tranmiderr1': 2e-4, 'pl_trandur': 2.4, 'pl_trandep': 0.4, 'sy_vmag': 9.1},
           {'pl_name': 'Vague-1 b', 'hostname': 'Vague-1', 'ra': 50.0, 'dec': -5.0, 'pl_orbper': 3.0, 'pl_orbpererr1': 0.01, 'pl_tranmid': 2459000.0,
            'pl_tranmiderr1': 0.01, 'pl_trandur': 2.0, 'pl_trandep': 1.0, 'sy_vmag': 10.0}]
    cols = {'file_key': ['CH_PR1_TG1_V0100', 'CH_PR1_TG2_V0100', 'CH_PR1_TG3_V0100'], 'obj_id_catname': ['Test-1', 'Vague-1', 'Nowhere'],
            'ra_deg': [10.0001, 50.0, 200.0], 'dec_deg': [20.0001, -5.0, 0.0], 'bjd_start': [2460000.0, 2460000.0, 2460000.0], 'bjd_end': [2460000.8, 2460002.0, 2460001.0]}
    search = {'parameters': [{'variableName': k, ('stringValues' if isinstance(v[0], str) else 'doubleValues'): v} for k, v in cols.items()]}
    lc = _cheops_visit()
    photo = {'parameters': [{'variableName': k, ('intValues' if k == 'photom_event_vect' else 'doubleValues'): v} for k, v in lc.items()]}
    asked = []
    old_t, old_f = net.fetch_text, net.fetch

    def fake_fetch(url, params=None, data=None, headers=None, **k):
        asked.append((url, data))
        assert headers and headers.get('Accept') == 'application/json'       # without it the archive answers in a binary format
        return json.dumps(search if url.endswith('/search') else photo).encode()
    try:
        net.fetch_text = lambda url, params=None, **k: json.dumps(nea)
        net.fetch = fake_fetch
        ctx = jobs.Ctx('cheops', 0)
        ctx.progress = lambda *_: None
        ctx.log = lambda *_: None
        msg = cheops.run(ctx)
        msg2 = cheops.run(ctx)                                               # a second run finds nothing new and keeps the result
    finally:
        net.fetch_text, net.fetch = old_t, old_f
    assert msg.startswith('answer-key test: the blind search recovered 1 of 1 known transits (1 with the right depth)'), msg
    assert msg2 == msg, msg2
    lcs = [a for a in asked if '/photometry/' in a[0]]
    assert len(lcs) == 1 and lcs[0][0].endswith('/photometry/Test-1') and b'CH_PR1_TG1_V0100' in lcs[0][1], lcs    # the badly-predicted planet is skipped
    st = db.kv_get(con, 'cheops')
    assert st['tested'] == 1 and st['recovered'] == 1 and st['items'][0]['planet'] == 'Test-1 b' and st['items'][0]['plot'].endswith('.png'), st
    data, files = publish.build_data(con, {})
    cc = data['tess']['crosscheck']
    assert cc['recovered'] == 1 and cc['items'][0]['plot'] == 'files/plots/cheops/CH_PR1_TG1_V0100.png' and 'file_key' not in cc['items'][0], cc
    assert 'plots/cheops/CH_PR1_TG1_V0100.png' in files and not publish.validate(data)
    assert 'cheops' in jobs.JOBS


def _synthetic_plate(seed=7, with_target=True, target_offset=(4.0, 1.5), shift_archive=(1.2, -0.8)):
    """A 1954-like scanned plate: stars at their 1954 positions (Gaia 2016 positions moved back with proper motions), a
    slightly distorted true projection, an archive WCS that is off by about a pixel, noise, and a faint object 3" from
    a brighter star. Returns (image, archive wcs, gaia catalogue, truth)."""
    from astropy.wcs import WCS
    from spacelab import plates
    rng = np.random.default_rng(seed)
    ra0, dec0, scale, n = 150.0, -30.0, 0.67, 520
    jd = 2434836.5                                                   # 1954-04-03
    m = 900
    ra16 = ra0 + rng.uniform(-0.09, 0.09, m) / math.cos(math.radians(dec0))
    dec16 = dec0 + rng.uniform(-0.09, 0.09, m)
    pmra = rng.normal(0, 25, m); pmdec = rng.normal(0, 25, m)
    pmra[:40] = rng.normal(0, 150, 40)                               # some fast stars: 9" in 62 years
    g = rng.uniform(12.5, 20.3, m)
    ra54, dec54 = plates.propagate(ra16, dec16, pmra, pmdec, jd)
    th = math.radians(0.7)

    def true_xy(ra, dec):                                            # the plate's real geometry (rotated, slightly curved)
        xi, eta = plates.tangent(ra, dec, ra0, dec0)
        u, v = (math.cos(th) * xi - math.sin(th) * eta) / scale, (math.sin(th) * xi + math.cos(th) * eta) / scale
        return n / 2 - u + 2e-4 * u * u / 10, n / 2 + v + 1.5e-4 * u * v / 10
    yy, xx = np.mgrid[:n, :n]
    img = rng.normal(100.0, 2.0, (n, n))
    s = 3.0 / 2.3548

    def add(x, y, flux, sx=s, sy=s):
        x0, y0 = int(round(x)), int(round(y))
        a, b = max(0, y0 - 12), max(0, x0 - 12)
        sl = (slice(a, y0 + 13), slice(b, x0 + 13))
        img[sl] += flux / (2 * math.pi * sx * sy) * np.exp(-0.5 * (((xx[sl] - x) / sx) ** 2 + ((yy[sl] - y) / sy) ** 2))
    X, Y = true_xy(ra54, dec54)
    for x, y, gg in zip(X, Y, g):
        add(x, y, 4000 * 10 ** (-0.4 * (gg - 15)))
    # a brighter star near the middle, and the faint object 3" from it (the 1991 UK Schmidt situation)
    bra, bdec = plates.untangent(np.array([0.0]), np.array([0.0]), ra0, dec0)
    ra16 = np.append(ra16, bra); dec16 = np.append(dec16, bdec); pmra = np.append(pmra, 0); pmdec = np.append(pmdec, 0); g = np.append(g, 15.2)
    bx, by = true_xy(bra, bdec)
    add(float(bx[0]), float(by[0]), 4000 * 10 ** (-0.4 * 0.2))
    tra, tdec = plates.untangent(np.array([target_offset[0] * 0.67]), np.array([target_offset[1] * 0.67]), ra0, dec0)
    tx, ty = true_xy(tra, tdec)
    if with_target:
        add(float(tx[0]), float(ty[0]), 4000 * 10 ** (-0.4 * (18.6 - 15)), sx=s * 1.25, sy=s)      # slightly trailed along x
    w = WCS(naxis=2)
    w.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    w.wcs.crval = [ra0, dec0]
    w.wcs.crpix = [n / 2 + 1 + shift_archive[0], n / 2 + 1 + shift_archive[1]]
    w.wcs.cdelt = [-scale / 3600, scale / 3600]
    gaia = {'ra': ra16, 'dec': dec16, 'pmra': pmra, 'pmdec': pmdec, 'g': g}
    return img, w, gaia, {'jd': jd, 'ra': float(tra[0]), 'dec': float(tdec[0]), 'x': float(tx[0]), 'y': float(ty[0])}


@test
def plates_astrometry_on_a_synthetic_1954_plate():
    from spacelab import plates
    img, w, gaia, truth = _synthetic_plate()
    # the prediction is 2.6" off, with a 3-sigma area of 30"; the object moves 2"/hour mostly east
    pdx, pdy = 2.0, -1.66
    pra, pdec = plates.untangent(np.array([plates.tangent(truth['ra'], truth['dec'], 150.0, -30.0)[0] + pdx]),
                                 np.array([plates.tangent(truth['ra'], truth['dec'], 150.0, -30.0)[1] + pdy]), 150.0, -30.0)
    pred = {'ra': float(pra[0]), 'dec': float(pdec[0]), 'a': 30.0, 'b': 12.0, 'pa': 80.0}
    m = plates.measure_plate(img, w, gaia, truth['jd'], pred, 3000, (2.0, 0.3), scale=0.67, avoid_mag=21.5, log=lambda *a: None)
    assert m['found'], m.get('why')
    err = math.hypot(*plates.tangent(m['ra'], m['dec'], truth['ra'], truth['dec']))
    assert err < 0.2, (err, m['plate'])                                   # true position recovered to better than 0.2"
    assert m['plate']['n'] >= 60 and m['plate']['rms_ra'] < 0.12 and m['plate']['order'] >= 2, m['plate']
    assert m['snr'] >= 8 and m['n_removed'] >= 1, (m['snr'], m['n_removed'])
    assert abs(m['off'] - 2.6) < 0.3 and m['in_ellipse'] < 1, (m['off'], m['in_ellipse'])
    assert 0.25 <= m['sigma'] < 0.4, m['sigma']                          # floor + small centroid and plate terms
    assert m['shape']['trail_px'] > 1 and m['shape']['verdict'] == 'stretched along the direction of motion', m['shape']
    # without moving Gaia stars to 1954 the plate solution is visibly worse
    g0 = dict(gaia, pmra=np.zeros_like(gaia['pmra']), pmdec=np.zeros_like(gaia['pmdec']))
    m0 = plates.measure_plate(img, w, g0, truth['jd'], pred, 3000, (2.0, 0.3), scale=0.67, avoid_mag=21.5, log=lambda *a: None)
    assert m0['plate']['n'] < m['plate']['n'] or m0['plate']['rms_ra'] > 1.5 * m['plate']['rms_ra'], (m0['plate'], m['plate'])
    # no object on the plate: nothing is claimed
    img2, w2, gaia2, truth2 = _synthetic_plate(with_target=False)
    m2 = plates.measure_plate(img2, w2, gaia2, truth2['jd'], pred, 3000, (2.0, 0.3), scale=0.67, avoid_mag=21.5, log=lambda *a: None)
    assert plates.verdict(m2) in ('not found', 'marginal'), (plates.verdict(m2), m2.get('snr'))
    # the comparison plate: empty at the object's spot on another date, but a static star there is noticed
    c = plates.absent_elsewhere(img2, w2, truth['ra'], truth['dec'], 3.0, gaia2, truth2['jd'], log=lambda *a: None)
    assert c and c['absent'], c                                          # the bright star 3" away is not mistaken for it
    c2 = plates.absent_elsewhere(img, w, truth['ra'], truth['dec'], 3.0, gaia, truth['jd'], log=lambda *a: None)
    assert c2 and not c2['absent'] and plates.verdict(m, c2).startswith('static'), c2
    assert plates.verdict(m, c) == 'detected'
    # noise control: a blob nearly as strong where the object would be on a wrong date makes the detection only marginal
    weak = dict(m, snr=5.0)
    assert plates.verdict(weak, c) == 'detected' and plates.verdict(weak, c, control=4.2) == 'marginal' and plates.verdict(dict(m, snr=11.1), c, control=4.2) == 'detected'
    assert plates.control_snr({0.0: {'found': True, 'snr': 5.0}, -1.0: {'found': True, 'snr': 4.2}, 1.0: {'found': False}}, 0.0) == 4.2
    assert plates.control_snr({0.0: {'found': True, 'snr': 11.1}}, 0.0) == 0.0
    # plate times: moving the archive's date forward by a day is one sidereal day (236 s less than 24 h); its own date needs no shift
    J = 2445047.7716
    assert abs((plates.plate_jd(J) - J) * 86400 + 235.909) < 0.01 and abs((plates.plate_jd(J, 1.0) - (J + 1)) * 86400 + 2 * 235.909) < 0.02
    assert abs(plates.plate_jd(J, -1.0) - (J - 1)) < 1e-9


@test
def plates_pure_helpers():
    from spacelab import plates
    # tangent plane round trip
    xi, eta = plates.tangent(10.5, -40.2, 10.0, -40.0)
    ra, dec = plates.untangent(xi, eta, 10.0, -40.0)
    assert abs(ra - 10.5) < 1e-9 and abs(dec + 40.2) < 1e-9
    # proper motion: 100 mas/yr east for 62 years at dec -60 is 6.2" on the sky
    r2, d2 = plates.propagate([20.0], [-60.0], [100.0], [0.0], plates.J2016 - 62 * 365.25)
    assert abs(plates.tangent(r2[0], d2[0], 20.0, -60.0)[0] + 6.2) < 0.01
    # ellipse distance: on the major axis edge = 1
    assert abs(plates.ellipse_distance(math.sin(math.radians(30)) * 10, math.cos(math.radians(30)) * 10, 10, 2, 30) - 1) < 1e-9
    assert plates.ellipse_distance(2.1, 0, 10, 2, 0) > 1
    # date choice: keep the corrected date unless another is clearly better
    f = lambda snr, e=0.5: {'found': True, 'snr': snr, 'in_ellipse': e}
    assert plates.choose_date({0.0: f(9), -1.0: f(4)}) == 0.0
    assert plates.choose_date({0.0: {'found': False}, -1.0: f(3), 1.0: f(12)}) == 1.0                 # the Eris POSS-I case: +2 d in total
    assert plates.choose_date({0.0: f(4), 1.0: f(6)}) == 0.0                                       # not twice as strong: keep
    assert plates.choose_date({0.0: {'found': False}, 1.0: f(12, e=1.4)}) == 0.0                    # outside the area: no
    off, votes, text = plates.timing_consensus({'(433) Eros': {-1: 0.4, 0: 0.9, 1: 14.0}, '(1) Ceres': {-1: 1.0, 0: 0.2, 1: 30.0}, 'faint': {0: 2, 1: 3}})
    assert off == 1 and len(votes[1]) == 2 and '2 known asteroid' in text, (off, votes, text)
    off, votes, text = plates.timing_consensus({'a': {0: 9, 1: 1}, 'b': {0: 0.5, 1: 9}})
    assert off is None and 'disagree' in text
    assert plates.timing_consensus({})[0] is None
    # SkyBoT text and MPC 80-column lines
    sk = ('# Flag: 1\n# Num | Name | RA(h) | DE(deg) | Class | Mv | Err(arcsec) | d(arcsec) | dRA(arcsec/h) | dDEC(arcsec/h)\n'
          '433 | Eros | 10 00 00.000 | -30 00 00.00 | NEA>Amor | 13.5 | 0.010 | 12.3 | 60 | -5\n')
    r = plates.parse_skybot(sk)
    assert len(r) == 1 and r[0]['name'] == 'Eros' and abs(r[0]['ra'] - 150) < 1e-6 and r[0]['v'] == 13.5, r
    line = '     K26E38D  A1954 04 03.34537 09 59 12.34 -29 57 01.2          18.9 R      675'
    line = line.ljust(80)[:77] + '675'
    o = plates.parse_obs80([line])
    assert len(o) == 1 and o[0]['stn'] == '675' and abs(o[0]['ra'] - (9 + 59 / 60 + 12.34 / 3600) * 15) < 1e-6 and o[0]['dec'] < -29.9, o
    assert plates.already_reported(o, '675', o[0]['jd'] + 1.0) and not plates.already_reported(o, '809', o[0]['jd'])
    assert not plates.already_reported(o, '675', o[0]['jd'] + 3.0)
    assert plates.already_reported([dict(o[0], stn='261')], '675', o[0]['jd']) and plates.already_reported([dict(o[0], stn='413')], '260', o[0]['jd'])   # same telescope, other code
    assert not plates.already_reported([dict(o[0], stn='260')], '675', o[0]['jd'])
    assert plates.already_reported([dict(o[0], stn='262')], '809', o[0]['jd']) and plates.same_site('262', '809') and not plates.same_site('262', '260')   # La Silla-DSS
    # a waiting candidate whose plate someone else has reported since: its object is searched again (the candidate is then taken back)
    from astropy.time import Time as _T
    t_iso = _T(o[0]['jd'] + 0.01, format='jd', scale='utc').isot[:21] + 'Z'
    cands = [{'des': 'X1', 'obs': json.dumps([{'obsTime': t_iso, 'stn': '809'}])}, {'des': 'X2', 'obs': json.dumps([{'obsTime': t_iso, 'stn': '260'}])},
             {'des': 'X3', 'obs': 'not json'}]
    asked = []
    assert plates.stale_objects(cands, lambda d: asked.append(d) or [dict(o[0], stn='262')]) == {'X1'} and asked == ['X1', 'X2'], asked
    # timing check: seconds between the MPC's time and the time used here, for plates that others measured too
    objs = {'X1': {'plates': [{'site': '809', 'obsTime': t_iso, 'validation': {'diff_arcsec': 0.2}}, {'site': '809', 'obsTime': t_iso, 'result': 'detected'}]},
            'X9': {'plates': []}}
    dts = plates.timing_audit(objs, lambda d: [dict(o[0], stn='262', jd=_T(t_iso.replace('Z', ''), format='isot', scale='utc').jd - 236.0 / 86400)])
    assert len(dts) == 1 and abs(dts[0] + 236.0) < 0.2 and abs(objs['X1']['plates'][0]['validation']['mpc_dt_s'] + 236.0) < 0.2, dts
    # an object whose search failed because a server was down is tried again soon (a few times), others after the long wait
    now = 1.0e9
    flaky = {'t': now - 7 * 3600, 'plates': [{'result': 'error: ConnectionError: the Gaia star catalogue could not be reached'}], 'tries': 1}
    assert plates.due_again('A', flaky, [], 120 * 86400, now) and not plates.due_again('A', dict(flaky, t=now - 3600), [], 120 * 86400, now)
    assert not plates.due_again('A', dict(flaky, tries=4), [], 120 * 86400, now)
    assert plates.due_again('A', {'t': now - 7 * 3600, 'plates': [{'result': 'not found (not enough Gaia stars could be matched for a plate solution)'}]}, [], 120 * 86400, now)
    fine = {'t': now - 7 * 3600, 'plates': [{'result': 'not found (nothing above 4 sigma)'}]}
    assert not plates.due_again('A', fine, [], 120 * 86400, now) and plates.due_again('A', dict(fine, t=now - 121 * 86400), [], 120 * 86400, now)
    # your own list: daily while something is open; once settled (all at the MPC / empty / unservable) the normal cycle
    open_ = dict(fine, plates=[{'result': 'detected'}], v=plates.PLATES_V)
    assert plates.due_again('M', dict(open_, t=now - 21 * 3600), ['M'], 120 * 86400, now) and not plates.due_again('M', dict(open_, t=now - 19 * 3600), ['M'], 120 * 86400, now)
    done_ = {'t': now - 3 * 86400, 'v': plates.PLATES_V, 'plates': [{'result': 'already reported by others: used as an accuracy test'},
                                                                   {'result': 'the cutout server gave plate 10257 instead; skipped'}]}
    assert plates.settled(done_) and not plates.due_again('M', done_, ['M'], 120 * 86400, now) and plates.due_again('M', dict(done_, t=now - 121 * 86400), ['M'], 120 * 86400, now)
    assert plates.due_again('M', dict(done_, v=1), ['M'], 120 * 86400, now)                 # measured before the sidereal-time fix: once more
    # the star catalogue: the ESA copy first, then IRSA's; an empty answer is a failure, never "no stars there"
    from spacelab import net as _net
    real_fetch, calls = _net.fetch_text, []
    csv_rows = '\n'.join(f'{10 + k * 0.001},{-5 + k * 0.001},1.5,-2.5,{15 + k * 0.1}' for k in range(12))

    def fake(url, params=None, **kw):
        calls.append(url)
        if 'esac.esa.int' in url:
            raise OSError('HTTP Error 503')
        if 'irsa' in url:
            assert 'gaia_dr3_source' in params['QUERY'] and 'gaiadr3.gaia_source' not in params['QUERY'], params
            return '"dec","ra","pmra","pmdec","phot_g_mean_mag"\n' + '\n'.join(','.join((r.split(',')[1], r.split(',')[0], *r.split(',')[2:])) for r in csv_rows.splitlines())
        raise OSError('unreachable')
    _net.fetch_text = fake
    try:
        g = plates.gaia_cone(10.0, -5.0, 0.1, log=lambda *a: None)
        assert len(g['ra']) == 12 and abs(g['ra'][0] - 10.0) < 1e-9 and abs(g['dec'][0] + 5.0) < 1e-9 and abs(g['pmdec'][0] + 2.5) < 1e-9, g
        assert any('irsa' in c for c in calls)

        def dead(url, params=None, **kw):
            if 'esac.esa.int' in url:
                return 'ra,dec,pmra,pmdec,phot_g_mean_mag\n'          # an empty table
            raise OSError('unreachable')
        _net.fetch_text = dead
        try:
            plates.gaia_cone(10.0, -5.0, 0.1, log=lambda *a: None)
            raise AssertionError('an empty star catalogue must be an error')
        except ConnectionError as e:
            assert 'tried again' in str(e)
    finally:
        _net.fetch_text = real_fetch
    # targets: bright distant objects first seen recently; manual ones first; done ones skipped
    rows = [{'pdes': '2026 ED138', 'full_name': '2026 ED138', 'H': '2.85', 'q': '36.9', 'a': '43.2', 'first_obs': '2016-02-25'},
            {'pdes': '2020 XX1', 'H': '9.5', 'q': '35', 'first_obs': '2020-01-01'},           # far too faint for plates
            {'pdes': '1999 OLD', 'H': '4', 'q': '35', 'first_obs': '1990-01-01'},             # already seen before the plates ended
            {'pdes': '2024 CEN', 'H': '7.0', 'q': '9.0', 'a': '14.0', 'first_obs': '2024-05-01'}]
    t = plates.pick_targets(rows, {'2026 ED138'}, 2026, want=3, manual=['136199'])
    assert [x['des'] for x in t] == ['136199', '2024 CEN'], t
    assert [x['des'] for x in plates.pick_targets(rows, set(), 2026)] == ['2024 CEN', '2026 ED138']      # newer first, both bright enough


@test
def plates_report_passes_the_checker():
    from spacelab import mpccheck, plates
    meas = {'obsTime': '1954-04-03T08:16:52.5Z', 'ra': 149.80514, 'dec': -29.950333, 'sigma': 0.31,
            'plate': {'order': 2, 'n': 140, 'rms_ra': 0.12, 'rms_dec': 0.11}}
    txt = plates.ades_plate('2026 ED138', meas, {'observer_name': 'A. Tester', 'sarc_verified': True}, '675', 'POSS-I E (S) 1029 (1954-04-03)',
                            'SSA plate date +1 d.')
    assert '|PHO |675 |1954-04-03T08:16:52.5Z |149.8051400 |-29.9503330 |0.310 |0.310 |Gaia3' in txt.replace(' |', '|').replace('|', ' |') or \
        'PHO' in txt and 'Gaia3' in txt, txt
    assert '! detector PHO' in txt and '! mpcCode 675' in txt and 'proper motions' in txt
    res = mpccheck.check(txt, None, {'sarc_verified': True}, online=False)
    errs = [r['msg'] for r in res if r['level'] == 'error']
    assert not errs, res
    assert any('photographic plate' in r['msg'] for r in res), res


@test
def mpc_send_needs_review_test_email_sarc_and_confirmation():
    from spacelab import mpcsubmit, plates, server, db
    meas = {'obsTime': '1991-03-24T15:02:11.0Z', 'ra': 210.1, 'dec': -28.2, 'sigma': 0.3, 'plate': {'order': 2, 'n': 90, 'rms_ra': 0.1, 'rms_dec': 0.1}}
    cfg = {'observer_name': 'A. Tester', 'contact_email': '', 'sarc_verified': False}
    ades = plates.ades_plate('2099 AB1', meas, cfg, '260', 'SERC-R/AAO-R 14248', 'SSA plate date +1 d.')
    con = db.connect()
    cid = con.execute('INSERT INTO pc_cands(des,night,archive,obs,offset,mdist,score,gif,png,ades,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('2099 AB1', '1991-03-24', 'SuperCOSMOS SERC-R/AAO-R 14248', json.dumps([{'objtype': 'tno', 'mode': 'PHO'}]),
                       1.0, 0.1, 12, None, None, ades, 'new', time.time())).lastrowid
    sent = []
    old_post, old_check, old_load = mpcsubmit.post, mpcsubmit.mpccheck.check, server.config.load
    mpcsubmit.post = lambda url, fields, timeout=120: (sent.append((url, fields)) or
                                                       ('Submission format valid.' if url.endswith('_test') else 'Submission ID is 2026-11-10T01:02:03.456_0000AbCd'))
    mpcsubmit.mpccheck.check = lambda text, c, cfg, online=True: old_check(text, c, cfg, online=False)
    try:
        server.config.load = lambda: cfg
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': True})
        assert code == 409 and any('mark the sighting as real' in b for b in out['blocked']) and any('email' in b for b in out['blocked']), out
        con.execute("UPDATE pc_cands SET status='real' WHERE id=?", (cid,))
        cfg['contact_email'] = 'tester@example.com'
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': True})
        assert code == 200 and out['ok'] and sent[-1][0].endswith('/submit_psv_test'), out
        f = sent[-1][1]
        assert f['ac2'] == 'tester@example.com' and f['obj_type'] == 'tno' and f['source'] == ades and '2099 AB1' in f['ack'], f
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': False})
        assert code == 400 and 'confirmation' in out['error'], out              # the page must ask you first
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': False, 'confirm': True})
        assert code == 409 and any('SARC' in b for b in out['blocked']), out
        cfg['sarc_verified'] = True
        con.execute('UPDATE pc_cands SET ades=? WHERE id=?', (ades.replace('0.300', '0.310'), cid))      # changed after the test
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': False, 'confirm': True})
        assert code == 409 and any('exact file' in b for b in out['blocked']), out
        con.execute('UPDATE pc_cands SET ades=? WHERE id=?', (ades, cid))
        n = len(sent)
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': False, 'confirm': True})
        assert code == 200 and out['ok'] and out['id'] == '2026-11-10T01:02:03.456_0000AbCd' and len(sent) == n + 1 and sent[-1][0].endswith('/submit_psv'), out
        code, out = server.post_action(f'/api/pc/{cid}/submit', {'test': False, 'confirm': True})
        assert code == 409 and any('already sent' in b for b in out['blocked']), out                     # never twice
        st = json.loads(db.one(con, 'SELECT mpc FROM pc_cands WHERE id=?', (cid,))['mpc'])
        assert st['test']['ok'] and st['sent']['id'], st
        r = db.one(con, "SELECT * FROM results WHERE key=?", (f'mpc:{cid}',))
        assert r and r['status'] == 'submitted' and '0000AbCd' in r['note'] and not r['public'], r
    finally:
        mpcsubmit.post, mpcsubmit.mpccheck.check, server.config.load = old_post, old_check, old_load
    body, ctype = mpcsubmit.multipart({'ack': 'x', 'source': 'a|b\nc'})
    assert ctype.startswith('multipart/form-data; boundary=') and b'name="source"\r\n\r\na|b\nc\r\n' in body
    assert mpcsubmit.test_passed('Submission format valid.') and not mpcsubmit.test_passed('XML schema validation error: wrong ades version')
    # the public page never shows an old-plate position before it has been sent
    from spacelab import publish
    pid = con.execute('INSERT INTO pc_cands(des,night,archive,obs,offset,mdist,score,gif,png,ades,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('2099 AB2', '1982-03-19', 'SuperCOSMOS ESO-R 4699', json.dumps([{'obsTime': '1982-03-19T03:00:00.0Z', 'ra': 12.3456, 'dec': -45.6789, 'filter': 'E', 'snr': 9}]),
                       1.0, 0.1, 9, None, None, ades, 'new', time.time())).lastrowid
    data, _ = publish.build_data(con, {})
    pub = {c['id']: c for c in data['asteroids']['cands']}
    assert 'ra' not in pub[pid]['obs'][0] and pub[pid]['obs'][0]['snr'] == 9 and '12.3456' not in json.dumps(data), pub[pid]
    # a weak plate detection (a blob nearly as strong at a wrong-date position) is published as weak, still without its position
    wid = con.execute('INSERT INTO pc_cands(des,night,archive,obs,offset,mdist,score,gif,png,ades,status,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                      ('2099 AB3', '1984-07-17', 'SuperCOSMOS SERC-R/AAO-R 9450', json.dumps([{'obsTime': '1984-07-17T10:32:03.5Z', 'ra': 98.7654, 'dec': -1.2345, 'filter': 'R', 'snr': 5.0,
                                                                                               'date_offset': 0.0, 'date_probe': {'+0': 5.0, '-1': 4.2, '+1': None}}]),
                       0.76, 0.5, 5.0, None, None, ades, 'new', time.time())).lastrowid
    data, _ = publish.build_data(con, {})
    pub = {c['id']: c for c in data['asteroids']['cands']}
    assert pub[wid].get('weak') is True and 'weak' not in pub[pid] and '98.7654' not in json.dumps(data) and not publish.validate(data), pub[wid]


@test
def publish_makes_smaller_webp_pictures():
    from spacelab import publish
    from PIL import Image
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    root = Path(tempfile.mkdtemp())
    (root / 'files' / 'plots').mkdir(parents=True)
    fig, ax = plt.subplots(figsize=(8, 5))
    x = np.linspace(0, 10, 4000)
    ax.plot(x, np.sin(x) + 0.05 * np.random.default_rng(1).normal(size=x.size), '.', ms=1)
    ax.set_title('TIC 1 light curve')
    fig.savefig(root / 'files' / 'plots' / 'a.png', dpi=100)
    plt.close(fig)
    Image.new('RGB', (40, 40), 'white').save(root / 'files' / 'plots' / 'unused.png')
    data = {'tess': {'cands': [{'tic': 1, 'plot': 'files/plots/a.png'}]}, 'note': 'files/plots/a.png is a plot'}
    n, saved = publish.webp_copies(root, data)
    assert n == 1 and saved > 0 and data['tess']['cands'][0]['plot'] == 'files/plots/a.webp', (n, data)
    assert data['note'] == 'files/plots/a.png is a plot'                     # only whole file references change
    assert (root / 'files' / 'plots' / 'a.png').is_file() and (root / 'files' / 'plots' / 'a.webp').is_file()   # PNG kept for link previews
    assert not (root / 'files' / 'plots' / 'unused.webp').exists()
    with Image.open(root / 'files' / 'plots' / 'a.webp') as im:
        assert im.size == (800, 500)


@test
def variable_stars_found_checked_and_prepared_for_vsx():
    from spacelab import variables, db, publish, server, config
    rng = np.random.default_rng(11)
    t = np.concatenate([np.arange(1000, 1026, 2 / 1440), np.arange(1030, 1056, 2 / 1440)])      # two sectors, 2-min cadence
    noise = rng.normal(0, 1e-3, t.size)
    # a spotted cool dwarf: 5 ppt, every 2.3 days, with a few flares
    f = 1 + 2.5e-3 * np.sin(2 * np.pi * t / 2.3) + noise
    for t0 in (1003.1, 1011.7, 1035.2, 1047.9):
        k = np.searchsorted(t, t0)
        f[k:k + 6] += np.array([30, 22, 15, 9, 5, 3]) * 1e-3
    r = variables.analyze(t, f, teff=3400)
    assert r['strong'] and r['kind'] == 'rotation' and abs(r['period'] - 2.3) < 0.01 and 4 < r['amp'] < 6 and r['flares'] >= 3, r
    assert r['vsx_type'] == 'BY+UV' and r['fap'] < 1e-10 and r['cycles'] > 20, r
    # a detached eclipsing binary: two eclipses per 3.1-day orbit; the periodogram finds half, the shape gives the orbit
    ph = ((t - 1000.3) / 3.1) % 1
    eb = 1 + noise * 0.5 - 0.02 * np.exp(-0.5 * (np.minimum(ph, 1 - ph) / 0.01) ** 2) - 0.012 * np.exp(-0.5 * ((ph - 0.5) / 0.01) ** 2)
    r2 = variables.analyze(t, eb, teff=4800)
    assert r2['strong'] and r2['kind'] == 'eclipsing (detached)' and abs(r2['period'] - 3.1) < 0.01 and r2['vsx_type'] == 'EA' and 17 < r2['amp'] < 22, r2
    # noise alone, and a slow drift over a sector (a typical instrument effect), are not variable stars
    assert not variables.analyze(t, 1 + noise)['strong']
    sector0 = np.where(t < 1028, 1000, 1030)
    ramp = variables.analyze(t, 1 + noise + 0.004 * (t - sector0) / 26)                       # each sector drifts the same way
    assert not ramp['strong'] and 'a slow trend, not a repeat' in ramp['why_not'], ramp
    one = variables.analyze(t, 1 + noise + np.where(t > 1028, 3e-3 * np.sin(2 * np.pi * t / 1.7), 0))   # only in the second sector
    assert not one['strong'] and 'seen in only one stretch of data' in one['why_not'], one
    assert r['segments'] == [2, 2] and r['why_not'] == []
    assert variables.analyze(t[:100], f[:100]) is None
    # catalogues: a service that cannot be asked means nothing can be concluded
    res, ok = variables.known(0, 0, {'VSX': lambda: [], 'Gaia DR3 variability': lambda: (_ for _ in ()).throw(OSError('down')), 'ASAS-SN variables': lambda: []})
    assert not ok and res['Gaia DR3 variability'] is None
    res, ok = variables.known(0, 0, {'VSX': lambda: [{'name': 'V0001 Xyz', 'type': 'BY'}], 'X': lambda: []})
    assert ok and res['VSX'][0]['name'] == 'V0001 Xyz'
    # a VizieR mirror whose database is down answers with an error page: that is no answer, the next mirror is asked
    from spacelab import net as _net, catalogs
    real, asked = _net.fetch_text, []
    good = '#Name: II/366/catv2021\n#Table\tII_366_catv2021:\nID\tType\tPer\t_r\n \t \t \t \n--\t--\t--\t--\nASASSN-V J0001\tROT\t2.09\t1.2\n'

    def fake(url, params=None, **kw):
        asked.append(url)
        if 'cds.unistra' in url:
            raise OSError('unreachable')
        if 'cfa.harvard' in url:
            return 'Content-Type: text/html\n{\\html\n****\n**** -- no connection\n'
        return good
    _net.fetch_text = fake
    try:
        hit = variables._vizier('II/366/catv2021', 42.85, 13.38, 30)
        assert hit[0]['name'] == 'ASASSN-V J0001' and hit[0]['type'] == 'ROT' and len(asked) == 3, (hit, asked)
        _net.fetch_text = lambda url, params=None, **kw: 'Content-Type: text/html\n****\n'
        try:
            variables._vizier('II/366/catv2021', 42.85, 13.38, 30)
            raise AssertionError('an error page must not count as "no match"')
        except ConnectionError:
            pass
        try:
            catalogs._asu({'-source': 'X'})
            raise AssertionError('error pages are not answers')
        except ConnectionError:
            pass
    finally:
        _net.fetch_text = real
    # neighbours within two TESS pixels: an equally bright star 20" away could cause 5 ppt; a faint one 30" away could not
    g = {'ra': np.array([150.0, 150.0 + 20 / 3600, 150.0 - 30 / 3600]), 'dec': np.array([10.0, 10.0, 10.0]), 'g': np.array([12.0, 12.0, 19.0])}
    b = variables.blend_check(150.0, 10.0, 5.0, g)
    assert b['target_g'] == 12.0 and len(b['suspects']) == 1 and abs(b['suspects'][0]['sep'] - 19.7) < 0.5 and 'could cause it' in b['verdict'], b
    b2 = variables.blend_check(150.0, 10.0, 5.0, {k: v[[0, 2]] for k, v in g.items()})
    assert not b2['suspects'] and b2['verdict'] == 'only this star is bright enough to cause it', b2
    # end to end: stored by the planet hunter, checked, picture and VSX fields made, judged by you, shown on the public page
    con = db.connect()
    con.execute('INSERT OR REPLACE INTO tess_targets(tic, ra, dec, tmag, teff, status, sectors) VALUES(?,?,?,?,?,?,?)', (999001, 150.0, 10.0, 11.2, 3400, 'done', '45,46'))

    class LC:
        pass
    lc = LC(); lc.time = LC(); lc.flux = LC(); lc.time.value = t; lc.flux.value = f
    rr = variables.from_lightcurve(999001, lc, 3400)
    assert rr['strong'] and (config.DATA / rr['npz']).is_file()
    variables.record(con, 999001, rr)
    variables.record(con, 999002, {'status': 'no TESS data'})
    row = db.one(con, 'SELECT * FROM tess_var WHERE tic=999001')
    assert row['status'] == 'new'
    st = variables.check_one(con, {'observer_name': 'A. Tester'}, lambda *a: None, None, row,
                             gaia_fn=lambda ra, dec: {'ra': g['ra'][[0, 2]], 'dec': g['dec'][[0, 2]], 'pmra': np.zeros(2), 'pmdec': np.zeros(2), 'g': g['g'][[0, 2]]},
                             lookups={'VSX': lambda: [], 'Gaia DR3 variability': lambda: [], 'ASAS-SN variables': lambda: []},
                             ztf_fn=lambda ra, dec, p, amp: {'ok': True, 'verdict': 'ZTF sees the same period (test)'})
    row = db.one(con, 'SELECT * FROM tess_var WHERE tic=999001')
    ch = json.loads(row['checks'])
    fields = dict(ch['vsx'])
    assert st == 'candidate' and row['status'] == 'candidate' and (config.DATA / row['plot']).is_file(), row
    assert fields['Variability type'] == 'BY+UV' and abs(float(fields['Period (days)']) - 2.3) < 0.01 and fields['Discoverer'] == 'A. Tester', fields
    assert fields['Coordinates (J2000)'].startswith('10:00:00.00 +10:00:00') and fields['Magnitude range (TESS passband)'].startswith('11.200 - 11.2'), fields
    assert 2457000 + 1000 < ch['epoch'] < 2457000 + 1056 and ch['blend']['verdict'] == 'only this star is bright enough to cause it'
    assert db.one(con, "SELECT * FROM tasks WHERE key='var:999001'")
    # a star a catalogue already has is "known"; one that could not be checked waits
    variables.record(con, 999003, dict(rr, npz=rr['npz']))
    con.execute('INSERT OR REPLACE INTO tess_targets(tic, ra, dec, tmag, status) VALUES(?,?,?,?,?)', (999003, 20.0, -5.0, 9.0, 'done'))
    r3 = db.one(con, 'SELECT * FROM tess_var WHERE tic=999003')
    assert variables.check_one(con, {}, lambda *a: None, None, r3, lookups={'VSX': lambda: None.x}) == 'new'
    assert variables.check_one(con, {}, lambda *a: None, None, r3, lookups={'VSX': lambda: [{'name': 'NSV 1'}]}) == 'known'
    # a candidate checked by older code is checked again (its review task is withdrawn until then)
    con.execute("UPDATE tess_var SET status='candidate', checks=? WHERE tic=999003", (json.dumps({'catalogues': {}}),))

    class Ctx:
        pass
    cx = Ctx(); cx.con, cx.log, cx.cfg = con, (lambda *a: None), {'variables': {'backfill_min': 0}}
    cx.time_left = lambda: 1e9
    _k, variables.known = variables.known, (lambda ra, dec, lookups=None: ({'VSX': [{'name': 'NSV 1'}]}, True))
    try:
        variables.run(cx)
    finally:
        variables.known = _k
    assert db.one(con, 'SELECT status FROM tess_var WHERE tic=999003')['status'] == 'known'
    assert db.one(con, 'SELECT status FROM tess_var WHERE tic=999001')['status'] == 'candidate'          # checked by this code: kept
    # the same period in a star 60" away: TESS sees one star's light in both
    con.execute('INSERT OR REPLACE INTO tess_targets(tic, ra, dec, tmag, status) VALUES(?,?,?,?,?)', (999004, 150.0 + 60 / 3600 / math.cos(math.radians(10)), 10.0, 12.5, 'done'))
    variables.record(con, 999004, dict(rr))
    tw = variables.twins(con, variables._star(con, 999001), rr['period'])
    assert len(tw) == 1 and tw[0]['tic'] == 999004 and abs(tw[0]['sep'] - 60) < 0.5, tw
    assert not variables.twins(con, variables._star(con, 999001), rr['period'] * 1.02)
    con.execute('DELETE FROM tess_var WHERE tic=999004')
    s = variables.summary(con)
    assert s['candidates'] == 1 and s['known'] == 1 and s['clear'] == 2 and s['searched'] == 2, s
    # public page: nothing until you look; then counts and the star you called real
    data, files = publish.build_data(con, {})
    v = data['tess']['variables']
    assert v['candidates'] == 1 and v['stars'] == [] and not publish.validate(data), v
    assert server.post_action('/api/var/999001', {'v': 'bogus'})[0] == 400
    assert server.post_action('/api/var/999001', {'v': 'real'})[0] == 200
    assert db.one(con, "SELECT status FROM tasks WHERE key='var:999001'")['status'] == 'done'
    data, files = publish.build_data(con, {})
    v = data['tess']['variables']
    assert v['stars'][0]['tic'] == 999001 and v['stars'][0]['type'] == 'BY+UV' and v['stars'][0]['plot'].startswith('files/plots/variables/'), v
    api = server.api_var()
    assert api['cands'][0]['tic'] == 999001 and api['cands'][0]['checks']['vsx'] and 'npz' not in api['cands'][0]


@test
def countdown_for_running_and_queued_jobs():
    from spacelab import server, db
    con = db.connect()
    con.execute("DELETE FROM runs WHERE job IN ('zz_a', 'zz_b', 'zz_c')")
    t0 = 1.0e9
    for k, (d, trig) in enumerate([(600, 'schedule'), (3000, 'schedule'), (480, 'full-time mode'), (500, 'full-time mode'), (520, 'full-time mode'), (4000, 'schedule')]):
        con.execute('INSERT INTO runs(job, started, finished, status, trigger) VALUES(?,?,?,?,?)', ('zz_a', t0 + k, t0 + k + d, 'ok', trig))
    con.execute('INSERT INTO runs(job, started, finished, status, trigger) VALUES(?,?,?,?,?)', ('zz_a', t0 + 50, t0 + 51, 'error', 'full-time mode'))
    con.execute('INSERT INTO runs(job, started, finished, status, trigger) VALUES(?,?,?,?,?)', ('zz_b', t0, t0 + 40, 'ok', 'you'))
    assert server.typical_seconds(con, 'zz_a', 'full-time mode') == (500, 3)          # background runs judged by background runs
    assert server.typical_seconds(con, 'zz_a', 'you') == (600, 6)                     # too few of that kind: all finished runs
    assert server.typical_seconds(con, 'zz_c') == (None, 0)
    old = dict(server.STATE), list(server.ORDER)
    try:
        server.STATE.update(running='zz_a', run_started=t0 + 9000, run_trigger='full-time mode')
        server.ORDER[:] = [('zz_b', 'you')]
        e = server.eta(con, {'zz_a': {'time_budget_min': 120}}, now=t0 + 9100)
        assert e['running']['typical'] == 500 and e['running']['budget'] == 3000 and e['queue'] == [{'job': 'zz_b', 'typical': 40, 'n': 1}], e
        assert abs(e['total'] - 440) < 1e-6, e                                         # 400 s left of this one + 40 s for the next
        server.ORDER[:] = [('zz_b', 'you'), ('zz_c', 'you')]
        assert server.eta(con, {}, now=t0 + 9100)['total'] is None                     # a job that never ran: no promise
        server.STATE.update(running=None)
        server.ORDER[:] = []
        assert server.eta(con, {}) == {'running': None, 'queue': [], 'total': None}
    finally:
        server.STATE.clear(); server.STATE.update(old[0]); server.ORDER[:] = old[1]
    con.execute("DELETE FROM runs WHERE job IN ('zz_a', 'zz_b', 'zz_c')")


@test
def solar_system_positions_from_horizons():
    from spacelab import solar, db, publish
    K = 0.01720209895                                                     # Gauss constant: a circular orbit of radius r turns at K / r^1.5 rad/day
    asked = []

    def circ(r, jd, phase=0.0):
        w = K / r ** 1.5
        a = w * (jd - 2461000.0) + phase
        return [r * math.cos(a), r * math.sin(a), 0.0, -r * w * math.sin(a), r * w * math.cos(a), 0.0]
    radius = {'199': 0.39, '299': 0.72, '399': 1.0, '499': 1.52, '5': 5.2, '6': 9.5, '7': 19.2, '8': 30.1, '-31': 170.0, '-96': 0.3, '-170': 1.01, '1;': 2.77, '9': 35.0}
    names = {'199': 'Mercury (199)', '299': 'Venus (299)', '399': 'Earth (399)', '499': 'Mars (499)', '5': 'Jupiter Barycenter (5)', '6': 'Saturn Barycenter (6)',
             '7': 'Uranus Barycenter (7)', '8': 'Neptune Barycenter (8)', '-31': 'Voyager 1 (spacecraft) (-31)', '-96': 'Parker Solar Probe (spacecraft) (-96)',
             '-170': 'James Webb Space Telescope (spacecraft) (-170)', '1;': '1 Ceres (A801 AA)', '9': 'Pluto Barycenter (9)', '-32': 'Some Other Probe (-32)'}

    def fake(params):
        hid = params['COMMAND'].strip("'")
        asked.append((hid, params['START_TIME'], params['STEP_SIZE']))
        if hid not in names:
            return 'No matches found.'
        import calendar
        j0 = calendar.timegm(time.strptime(params['START_TIME'].strip("'"), '%Y-%m-%d %H:%M')) / 86400 + 2440587.5
        j1 = calendar.timegm(time.strptime(params['STOP_TIME'].strip("'"), '%Y-%m-%d %H:%M')) / 86400 + 2440587.5
        if hid == '-31' and j0 < 2443392.05:                                 # asked for before its path starts: Horizons says when it starts
            return 'No ephemeris for target "Voyager 1 (spacecraft)" prior to A.D. 1977-SEP-05 12:59:07.1810 TDB'
        st = params['STEP_SIZE'].strip("'")
        n = int(st) if st.isdigit() else int(round((j1 - j0) / (float(st.split()[0]) / (24 if st.endswith('h') else 1))))
        lines = []
        for k in range(n + 1):
            jd = j0 + (j1 - j0) * k / n
            v = circ(radius.get(hid, 1.0), jd)
            e = [f'{x: .15E}' for x in v]                                    # exactly as Horizons prints it (checked against the live service)
            lines.append(f'{jd:.9f} = A.D. 2026-Nov-10 12:00:00.0000 TDB \n X ={e[0]} Y ={e[1]} Z ={e[2]}\n VX={e[3]} VY={e[4]} VZ={e[5]}')
        return f'Target body name: {names[hid]}   {{source: x}}\n$$SOE\n' + '\n'.join(lines) + '\n$$EOE\n'
    real = ('$$SOE\n2461355.000000000 = A.D. 2026-Nov-10 12:00:00.0000 TDB \n X =-3.221143497811347E+01 Y =-1.371429763277359E+02 Z = 9.922520425841653E+01\n'
            ' VX=-1.195249269248731E-03 VY=-7.861203967160285E-03 VZ= 5.678201728773907E-03\n$$EOE')    # Voyager 1, copied from Horizons
    rr = solar.parse_vectors('Target body name: Voyager 1 (spacecraft) (-31)    {source: x}\n' + real)
    assert rr[0] == 'Voyager 1 (spacecraft) (-31)' and len(rr[1]) == 1 and abs(math.hypot(*rr[1][0][1:4]) - 172.31) < 0.05 and abs(rr[1][0][6] - 5.678201728773907e-3) < 1e-12, rr
    assert solar.parse_vectors('$$SOE\n2461355.000000000, A.D. 2026-Nov-10 12:00:00.0000,\n$$EOE')[1] == []        # the empty comma layout: no rows, never zeros
    assert solar.parse_vectors('$$SOE\n2461355.0, A.D. x, 1.0, 2.0, 3.0, 0.1, 0.2, 0.3,\n$$EOE')[1] == [[2461355.0, 1.0, 2.0, 3.0, 0.1, 0.2, 0.3]]
    assert solar.limit_from_error('x prior to A.D. 1977-SEP-05 12:59:07.1810 TDB')[0] == 'prior to' and abs(solar.cal_jd('1977-SEP-05 12:59') - 2443392.0409) < 0.001
    # every spacecraft listed at a world has a Wikipedia article, and so does every object the map can show
    for body, items in solar.AT.items():
        for d in items:
            assert d['n'] and d['note'] and (d.get('wiki') or d.get('links')), (body, d)
            for lab, t in d.get('links', []):
                assert lab and t and '_' not in t, (body, lab, t)
    keys = {o[0] for o in solar.OBJECTS}
    assert set(solar.AT) <= keys and {k for k, *_ in solar.OBJECTS if k != 'ed138'} <= set(solar.WIKI), set(k for k, *_ in solar.OBJECTS) - set(solar.WIKI)
    assert solar.WIKI['euclid'] == 'Euclid (space telescope)' and solar.PHOTO_Q['psyche'] == 'Psyche spacecraft'
    assert solar.step_text(0.25) == '6 h' and solar.step_text(5) == '5 d' and solar.cal(2440587.5) == '1970-01-01 00:00'
    logs = []
    t0 = 2461355.0
    meta, hist = solar.build(logs.append, fetch=fake, now=t0)
    full = json.loads((solar.SITE_DIR / 'solar.json').read_text())
    keys = [o['key'] for o in full['objects']]
    assert {'earth', 'mercury', 'voyager1', 'parker', 'jwst', 'ceres', 'pluto'} <= set(keys) and 'voyager2' not in keys and 'juice' not in keys, keys
    assert 'Voyager 2' in meta['skipped'] and any("Voyager 2: left out (Horizons answered for 'Some Other Probe (-32)')" in x for x in logs), logs[:6]   # wrong name: never shown
    tr = solar.read(solar.SITE_DIR / 'tracks.bin', full)
    e = tr['earth']
    assert abs(e[0, 0] - (t0 - 30)) < 1e-3 and abs(e[-1, 0] - (t0 + 400)) < 1e-3 and np.allclose(np.linalg.norm(e[:, 1:4], axis=1), 1.0, atol=1e-6)
    v1 = next(o for o in full['objects'] if o['key'] == 'voyager1')
    assert any(a[0] == '-31' and '1977-09-05 14' in a[1] for a in asked), [a for a in asked if a[0] == '-31']   # asked again from where its path starts
    assert v1['nh'] > 100 and tr['voyager1'][0, 0] < 2443500 and np.all(np.diff(tr['voyager1'][:, 0]) > 0) and 'voyager1' in hist     # path since launch, in time order
    assert v1['launched'] == '1977-09-05' and 'golden record' in v1['what'] and full['at']['mars'][2]['n'] == 'MAVEN' and 'ended in 2026' in full['at']['mars'][2]['note'] and v1['wiki'] == 'Voyager 1' and v1['q'] == 'Voyager 1'
    pk = tr['parker']
    assert abs(np.median(np.diff(pk[:, 0])) - 0.25) < 1e-3 and len(pk) > 1700
    r = solar.summary_now(full['objects'], tr, t0)
    assert abs(r['voyager1'] - 170) < 1e-3 and abs(r['parker'] - 0.3) < 1e-4
    n_before = len(asked)
    solar.build(lambda *a: None, fetch=fake, now=t0 + 1, history=hist)                                 # next day: the paths since launch are reused
    assert not any(a[2] == f"'{solar.HISTORY_N}'" for a in asked[n_before:])
    # too little from Horizons: the map is left as it was
    old = (solar.SITE_DIR / 'tracks.bin').read_bytes()
    try:
        solar.build(lambda *a: None, fetch=lambda p: 'No matches found.', now=t0 + 2)
        raise AssertionError('must refuse')
    except RuntimeError as ex:
        assert 'left as it was' in str(ex)
    assert (solar.SITE_DIR / 'tracks.bin').read_bytes() == old
    con = db.connect()
    db.kv_set(con, 'solar', meta)
    data, files = publish.build_data(con, {})
    so = data['beyond']['solar']
    assert so['json'] == 'files/plots/solar/solar.json' and so['bin'] == 'files/plots/solar/tracks.bin' and so['n_craft'] == 3 and not publish.validate(data), so
    assert 'solar.js' in publish.SITE_FILES and {'plots/solar/solar.json', 'plots/solar/tracks.bin'} <= set(files)
    for k, hid, name, kind, group, step, color in solar.OBJECTS:                                        # every object has its text
        assert k in solar.ABOUT and solar.ABOUT[k][2] and (kind != 'craft' or re.match(r'\d{4}-\d{2}-\d{2}$', solar.ABOUT[k][1])), k
        assert any(g[0] == group for g in solar.GROUPS), group


@test
def radar_never_lists_the_same_story_twice():
    from spacelab import radar, news
    # arXiv ids are read from the article's own text and links (Universe Today links the paper it covers)
    xml = ('<rss><channel><item><title>Astronomers map a giant void</title><link>https://www.universetoday.com/a/</link>'
           '<description>&lt;p&gt;A new study (&lt;a href="https://arxiv.org/abs/2611.01234v2"&gt;paper&lt;/a&gt;) maps it.&lt;/p&gt;</description>'
           '</item></channel></rss>')
    it = news.parse(xml, 'Universe Today')[0]
    assert it['arxiv'] == ['2611.01234'] and 'paper' in it['summary'], it
    assert news.arxiv_ids('see arXiv:2610.38693 and https://arxiv.org/pdf/2611.02371v1') == ['2610.38693', '2611.02371']
    mk = lambda src, title, url, arxiv=(), summ='': dict(source=src, title=title, url=url, date=1.0, summary=summ, arxiv=list(arxiv))
    raw = [mk('arXiv (new research)', 'A Bayesian census of cosmic voids in 2MRS', 'https://arxiv.org/abs/2611.01234', ['2611.01234']),
           mk('Universe Today', 'Astronomers map a giant void', 'https://www.universetoday.com/a/', ['2611.01234'], 'plain words'),
           mk('Space.com', 'Rubin finds a distant dwarf planet candidate', 'https://space.com/x'),
           mk('Phys.org', 'New dwarf planet candidate spotted beyond Neptune by Rubin', 'https://phys.org/y'),
           mk('ESA', 'Euclid releases its first big data set', 'https://esa.int/z'),
           mk('NASA', 'Webb sees water on a hot planet', 'https://nasa.gov/w', ['2611.09999'])]
    merged = radar.merge_duplicates(raw)
    # a fake embedding: stories about the same thing point the same way
    topic = lambda t: [1.0 if k in t.lower() else 0.0 for k in ('void', 'dwarf planet', 'euclid', 'webb')] + [0.05]
    recent = [{'id': 'old1', 'title': 'Euclid opens its first big release', 'summary': '', 'sources': ['ESO'], 'arxiv': []},
              {'id': 'old2', 'title': 'Hot planet has water, Webb finds', 'summary': '', 'sources': ['NASA Science'], 'arxiv': ['2611.09999']}]
    fresh, grew, folded = radar.dedupe(merged, recent, lambda texts: [topic(t) for t in texts])
    titles = sorted(x['title'] for x in fresh)
    assert titles == ['Astronomers map a giant void', 'Rubin finds a distant dwarf planet candidate'], titles
    void = next(x for x in fresh if 'void' in x['title'])
    assert void['sources'] == ['arXiv (new research)', 'Universe Today'] and void['url'] == 'https://www.universetoday.com/a/', void
    dp = next(x for x in fresh if 'dwarf' in x['title'])
    assert set(dp['sources']) == {'Space.com', 'Phys.org'}, dp
    assert grew == {'old1': ['ESA'], 'old2': ['NASA']} and set(folded) == {'https://esa.int/z', 'https://nasa.gov/w'}, (grew, folded)
    # without the embedding model the arXiv-id rules still work and nothing crashes
    fresh2, grew2, _ = radar.dedupe(radar.merge_duplicates(raw), recent, None)
    assert len(fresh2) == 3 and grew2 == {'old2': ['NASA']}, (len(fresh2), grew2)     # void (by arXiv id), dwarf planet (by words), Euclid
    # a story already listed under nearly the same title is caught by its words alone (no model needed)
    again = radar.merge_duplicates([mk('EarthSky', 'A stellar odd couple at an unusual stage of evolution', 'https://earthsky.org/o')])
    fresh3, grew3, folded3 = radar.dedupe(again, [{'id': 'o1', 'title': 'A Stellar Odd Couple at an Unusual Stage of Evolution', 'summary': '',
                                                    'sources': ['NASA'], 'arxiv': []}], None)
    assert not fresh3 and grew3 == {'o1': ['EarthSky']} and folded3 == ['https://earthsky.org/o'], (fresh3, grew3)
    # the stored list heals itself: repeats inside it are merged, the better-rated one stays and takes the other's sources
    it = lambda i, title, score, src, arx=(): {'id': i, 'title': title, 'url': 'https://x/' + i, 'summary': '', 'sources': [src], 'links': ['https://x/' + i],
                                                'arxiv': list(arx), 'score': score, 'found': 5.0, 'date': 5.0, 'lab': []}
    stored = [it('a', 'Mysterious gas on Mars suggests magma below', 6, 'Phys.org'), it('b', 'Webb sees water on a hot planet', 7, 'NASA', ['2611.09999']),
              it('c', 'Mars volcano gas: the dwarf planet link', 8, 'Space.com'), it('d', 'Water found by Webb in hot world air', 5, 'ESA', ['2611.09999']),
              it('e', 'New dwarf planet candidate spotted', 4, 'EarthSky'), it('f', 'Euclid releases its first big data set', 6, 'ESA')]
    out, gone = radar.tidy(stored, lambda texts: [topic(t) for t in texts])
    assert gone == 2 and [x['id'] for x in out] == ['a', 'b', 'c', 'f'], (gone, [x['id'] for x in out])
    assert next(x for x in out if x['id'] == 'b')['sources'] == ['NASA', 'ESA'] and next(x for x in out if x['id'] == 'c')['sources'] == ['Space.com', 'EarthSky']
    # the same story in Spanish and English: one item, and the English one leads
    assert radar._spanish('La NASA busca a la próxima generación de astronautas') and radar._spanish('La NASA destina un millón de dólares al reto estudiantil Base Lunar')
    assert not radar._spanish("NASA's ESCAPADE Completes Powered Earth Flybys, Now En Route to Mars") and not radar._spanish('New images from La Silla show the comet')
    es = [it('s1', 'La NASA busca a la próxima generación de astronautas (webb)', 8, 'NASA'), it('s2', 'NASA needs more astronauts: the Webb way', 5, 'Space.com')]
    out3, gone3 = radar.tidy(es, lambda texts: [topic(t) for t in texts])
    assert gone3 == 1 and out3[0]['title'].startswith('NASA needs more astronauts') and out3[0]['url'] == 'https://x/s2' and out3[0]['score'] == 8 and out3[0]['sources'] == ['NASA', 'Space.com'], out3
    # a headline that only exists in Spanish is translated by the local AI; a bad answer changes nothing
    sp = [it('t1', 'La NASA destina un millón de dólares al reto estudiantil Base Lunar', 6, 'NASA'), it('t2', 'Webb sees water on a hot planet', 7, 'NASA'),
          it('t3', 'La NASA busca a la próxima generación de astronautas', 6, 'NASA')]
    said = iter(['"NASA puts one million dollars into the Moon Base student challenge"\nextra line', 'La NASA busca a la próxima generación de astronautas'])
    assert radar.english_titles(sp, lambda q: next(said)) == 1
    assert sp[0]['title'] == 'NASA puts one million dollars into the Moon Base student challenge' and sp[0]['title_es'].startswith('La NASA destina')
    assert sp[1]['title'] == 'Webb sees water on a hot planet' and sp[2]['title'].startswith('La NASA busca') and 'title_es' not in sp[2]
    out2, gone2 = radar.tidy([dict(x, sources=list(x['sources'])) for x in stored[:2]] + [it('g', 'Hot planet water seen by Webb', 3, 'BBC', ['2611.09999'])], None)
    assert gone2 == 1 and len(out2) == 2 and 0.7 < radar.SAME < 0.85, (gone2, radar.SAME)


@test
def universe_flow_model_matches_the_paper_and_behaves():
    """Mould et al. 2000: Local Group frame, three attractors. Checked against numbers published with the model."""
    from spacelab import universe as uv
    # eq. A1 at the galactic poles and towards l = 90, b = 0
    assert abs(uv.lg_velocity(1000, 0, 90) - 964) < 1e-6 and abs(uv.lg_velocity(1000, 90, 0) - 1296) < 1e-6
    # the attractor positions are the paper's B1950 ones
    from astropy.coordinates import SkyCoord, FK4, ICRS
    from astropy.time import Time
    for (name, ra, dec, *_), (r0, d0) in zip(uv.ATTRACTORS, (('12h28m19s', '+12d40m'), ('13h20m00s', '-44d00m'), ('13h30m00s', '-31d00m'))):
        c = SkyCoord(r0, d0, frame=FK4(equinox=Time('B1950'))).transform_to(ICRS())
        assert abs(c.ra.deg - ra) < 0.01 and abs(c.dec.deg - dec) < 0.01, name
    # Virgo's own cosmic velocity: 17.4 Mpc at H0 = 70 (as used with this model by Nasonova et al. 2011, arXiv:1105.3750)
    name, ra, dec, v_a, v_fid = uv.ATTRACTORS[0]
    v_virgo = uv.flow_velocity([v_a], [ra], [dec])[0]
    assert abs(v_virgo / 70.0 - 17.4) < 0.1, v_virgo
    # an object at home shares our motion: no correction, in any direction
    rng = np.random.default_rng(5)
    v = rng.normal(size=(500, 3)); v /= np.linalg.norm(v, axis=1)[:, None]
    rr, dd = np.degrees(np.arctan2(v[:, 1], v[:, 0])) % 360, np.degrees(np.arcsin(v[:, 2]))
    assert np.abs(uv.flow_velocity(np.zeros(500), rr, dd)).max() < 1e-6
    # very far away only our own motion is left: the paper tunes its size to the CMB dipole of the Local Group (about 620 km/s)
    far = uv.flow_velocity(np.full(500, 1e7), rr, dd) - 1e7
    assert 560 < far.max() < 640 and -640 < far.min() < -560, (far.max(), far.min())
    # more redshift always means farther, also straight through the attractors (no folding)
    for r0, d0 in ((ra, dec), uv.ATTRACTORS[1][1:3], uv.ATTRACTORS[2][1:3], (150.0, 20.0)):
        vo = np.linspace(-500, 20000, 2051)
        assert np.diff(uv.flow_velocity(vo, np.full(vo.size, r0), np.full(vo.size, d0))).min() > 0
    # blend: flow model near home, CMB frame far away
    l, b = uv.eq_to_gal([150.0], [20.0])
    near = uv.cosmic_velocity(np.array([1500.0]), l, b, [150.0], [20.0])[0]
    assert abs(near - uv.flow_velocity(uv.lg_velocity(1500.0, l, b), [150.0], [20.0])[0]) < 1e-6
    assert abs(uv.cosmic_velocity(np.array([9000.0]), l, b, [150.0], [20.0])[0] - uv.cmb_velocity(9000.0, l, b)[0]) < 1e-6
    # comoving distance against astropy
    from astropy.cosmology import FlatLambdaCDM
    co = FlatLambdaCDM(H0=uv.H0, Om0=uv.OMEGA_M)
    for z in (0.004, 0.05, 0.1):
        assert abs(float(uv.comoving_mpc(z * uv.C_KMS)) - co.comoving_distance(z).value) < 0.01
    # measured distances: only the precise, nearby ones are used; matched by PGC number, or by position when there is none
    cf4 = [{'PGC': '1', 'DM': '29.86', 'e_DM': '0.07', 'DMtrgb': '29.80', 'DMsbf': '29.98', 'RAJ2000': '190.0', 'DEJ2000': '-11.6'},
           {'PGC': '2', 'DM': '30.00', 'e_DM': '0.45', 'DMtf': '30.00', 'RAJ2000': '10.0', 'DEJ2000': '10.0'},      # too rough
           {'PGC': '3', 'DM': '34.90', 'e_DM': '0.05', 'DMsnIa': '34.90', 'RAJ2000': '20.0', 'DEJ2000': '20.0'},    # too far
           {'PGC': '4', 'DM': '24.37', 'e_DM': '0.045', 'DMceph': '24.40', 'RAJ2000': '10.6848', 'DEJ2000': '41.2689'}]
    d, meth = uv.measured_distances(cf4, np.array([1, 2, 3, 0, 0]), np.array([0.0, 0, 0, 10.685, 50.0]), np.array([0.0, 0, 0, 41.269, 50.0]))
    assert abs(d[0] - 9.376) < 0.01 and meth[0] == 1 + 8 and np.isnan(d[1]) and np.isnan(d[2]), (d, meth)
    assert abs(d[3] - 0.748) < 0.005 and meth[3] == 2 and np.isnan(d[4]), (d, meth)


@test
def universe_never_replaces_the_map_with_a_broken_download():
    from spacelab import universe, net as _net
    universe.SITE_DIR.mkdir(parents=True, exist_ok=True)
    (universe.SITE_DIR / 'galaxies.bin').write_bytes(b'GOOD')
    real = _net.fetch_text
    _net.fetch_text = lambda url, params=None, **kw: 'Content-Type: text/html\n{\\html\n****\n**** -- no connection\n'
    try:
        try:
            universe.build(lambda *a: None)
            raise AssertionError('an error page must stop the build')
        except ConnectionError:
            pass
        # what the mirror really sent on 10 Nov 2026 while its database was down: a tidy header that names no table
        _net.fetch_text = lambda url, params=None, **kw: '#\n#   VizieR Astronomical Server\n#INFO\tpublisher=CDS\n\n#RESOURCE=\n#Name:\n#Title:\n'
        try:
            universe.build(lambda *a: None)
            raise AssertionError('an empty reply must stop the build')
        except ConnectionError:
            pass
        head = '#Name: J/ApJS/199/26/table3\nID\tRAJ2000\n \t \n--\t--\n'
        _net.fetch_text = lambda url, params=None, **kw: head + '00000000+0000000\t0.0\n'
        try:
            universe.build(lambda *a: None)
            raise AssertionError('a download with one row must stop the build')
        except RuntimeError as e:
            assert 'left as it was' in str(e)
    finally:
        _net.fetch_text = real
    assert (universe.SITE_DIR / 'galaxies.bin').read_bytes() == b'GOOD'


@test
def universe_build_collapses_clusters_and_finds_the_web():
    from spacelab import universe
    rng = np.random.default_rng(3)
    rows, tully, cf4, intended = [], [], [], {}
    used = set()
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    vgrid = np.linspace(0, 40000, 4001)
    dgrid = universe.comoving_mpc(vgrid)

    def helio(v_cosmic, ra, dec, l, b):
        """The heliocentric velocity that this map turns into the wanted cosmic velocity (bisection)."""
        lo, hi = v_cosmic - 3000.0, v_cosmic + 3000.0
        for _ in range(50):
            mid = 0.5 * (lo + hi)
            if universe.cosmic_velocity(np.array([mid]), [l], [b], [ra], [dec])[0] < v_cosmic:
                lo = mid
            else:
                hi = mid
        return 0.5 * (lo + hi)

    def add(ra, dec, d=None, cz=None, nest=0, nmb=1, name='', meas=None):
        c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg).galactic
        i = len(rows)
        if cz is None:
            cz = helio(float(np.interp(d, dgrid, vgrid)), ra, dec, c.l.deg, c.b.deg)
            if not nest and not meas:
                intended[i] = d
        rid = universe.units_id(int(ra / 15 * 360000), int(abs(dec) * 36000) * 2 + (dec < 0))   # a 2MASS-style ID of its own position
        while rid in used:                                                                       # two fake galaxies in the same 0.01 s
            rid = universe.units_id(universe.id_units(rid)[0] + 1, universe.id_units(rid)[1])
        used.add(rid)
        rows.append({'ID': rid, 'RAJ2000': f'{ra:.5f}', 'DEJ2000': f'{dec:.5f}', 'GLON': f'{c.l.deg:.5f}', 'GLAT': f'{c.b.deg:.5f}',
                     'Ktmag': '10.5', 'type': ' 3A2s', 'cz': f'{cz:.1f}', 'CAT': name})
        tully.append({'PGC': str(i + 1), '_RA.icrs': f'{ra:.4f}', '_DE.icrs': f'{dec:+.4f}', 'MType': '3', 'Vcmba': str(int(cz)),
                      'Nest': str(nest or 900000 + i), 'Nmb': str(nmb), 'Dist': '1.0'})
        if meas:
            cf4.append({'PGC': str(i + 1), 'DM': f'{5 * math.log10(meas[0]) + 25:.3f}', 'e_DM': str(meas[1]), meas[2]: '1', 'RAJ2000': f'{ra:.4f}', 'DEJ2000': f'{dec:.4f}'})
        return i
    # a filament of field galaxies along x, plus a uniform background, plus a "cluster" whose members spread +-1000 km/s
    for k in range(3000):
        d = rng.uniform(20, 150)
        v = rng.normal(size=3); v /= np.linalg.norm(v)
        add(math.degrees(math.atan2(v[1], v[0])) % 360, math.degrees(math.asin(v[2])), d)
    for k in range(2500):
        x = rng.uniform(-120, 120); y, z = rng.normal(0, 2.5, 2) + (60, 10)
        d = math.sqrt(x * x + y * y + z * z)
        add(math.degrees(math.atan2(y, x)) % 360, math.degrees(math.asin(z / d)), d)
    coma_c = SkyCoord(ra=194.95 * u.deg, dec=27.98 * u.deg).galactic
    coma_cz = helio(7300.0, 194.95, 27.98, coma_c.l.deg, coma_c.b.deg)
    spread = rng.normal(0, 1000, 150)
    spread -= spread.mean()
    for k in range(150):                                                  # Coma-like: one direction, one group
        add(194.95 + rng.normal(0, 0.4), 27.98 + rng.normal(0, 0.4), cz=coma_cz + spread[k], nest=100001, nmb=150,
            name='NGC_4889' if k == 0 else '')
    m31 = add(10.68, 41.27, cz=-300, name='MESSIER_031')                  # Andromeda: blueshifted, no measured distance given here
    g270 = SkyCoord(l=270 * u.deg, b=0 * u.deg, frame='galactic').icrs
    add(float(g270.ra.deg), float(g270.dec.deg), cz=200, name='NGC_6946')  # redshifted, yet at rest in the Local Group frame
    m32 = add(10.67, 40.87, cz=-200, name='NGC_0221', meas=(0.79, 0.05, 'DMtrgb'))        # blueshifted but measured: placed
    scl = add(11.888, -25.288, cz=243, name='NGC_0253', meas=(3.61, 0.02, 'DMtrgb'))      # redshift says 4 Mpc; measured wins
    rough = add(40.0, -10.0, 30.0, meas=(12.0, 0.45, 'DMtf'))                               # a rough measurement is not used
    grp = [add(120.0 + 0.2 * k, 50.0, 12.0, nest=100777, nmb=4, meas=m) for k, m in
           enumerate([(10.0, 0.08, 'DMsbf'), (10.4, 0.08, 'DMsbf'), None, None])]            # half the group measured: all move
    add(197.4487, -23.3840, cz=2961, name='g1309477-232302')              # NGC 4993 under a coordinate-style survey name
    ngc4993 = rows[-1]['ID']
    ant = add(180.4721, -18.8772, 22.0, name='ARP_244')                   # the Antennae: one survey entry, two NGC numbers
    m81 = add(148.8882, 69.0653, cz=-34, name='NGC_3031')                 # blueshifted, not in Cosmicflows-4: the Local Volume catalogue places it
    lvmem = add(150.4851, 55.6807, 12.0, name='NGC_3077x')                # its group's distance in the Local Volume catalogue
    lvh = add(155.0, 40.0, 11.0)                                          # Local Volume distance from its redshift only: not used
    lv = [{'Name': 'NGC3031', 'RAJ2000': '09 55 33.2', 'DEJ2000': '+69 03 55', 'Dist': '3.61', 'f_Dist': 'TRGB'},
          {'Name': 'X', 'RAJ2000': '10 01 56.4', 'DEJ2000': '+55 40 50', 'Dist': '3.82', 'f_Dist': 'mem'},
          {'Name': 'Y', 'RAJ2000': '10 20 00.0', 'DEJ2000': '+40 00 00', 'Dist': '2.00', 'f_Dist': 'h'}]
    logs = []
    meta, pos = universe.build(logs.append, (rows, tully, cf4, lv), famous_lookup={
        'NGC 4993': {'id': ngc4993, 'pos': None},
        'NGC 5194': {'id': None, 'pos': (float(rows[5]['RAJ2000']) + 2e-3, float(rows[5]['DEJ2000']))},
        'NGC 4038': {'id': '12015300-1852033', 'pos': (180.47084, -18.86759)}, 'NGC 4039': {'id': None, 'pos': (180.47296, -18.88619)}})
    np_ = {x['cat']: x for x in meta['blueshift']}
    assert meta['n'] == len(rows) - 2 and meta['n_left_out'] == 2, (meta['n'], len(rows), meta['blueshift'])
    assert np_['NGC 224']['why'] == 'blue' and np_['NGC 224']['name'] == 'Andromeda Galaxy' and 'blueshifted' in meta['blueshift_note'], np_
    assert np_['NGC 6946']['why'] == 'near' and np_['NGC 6946']['name'] == 'Fireworks Galaxy' and 'almost no speed' in meta['near_note'], np_
    f4993 = next(f for f in meta['famous'] if f['cat'] == 'NGC 4993')
    G0 = universe.read_bin(universe.SITE_DIR / 'galaxies.bin', universe.SITE_DIR / 'galaxies-names.txt')
    assert G0['ids'][f4993['i']] == ngc4993 and G0['names'][f4993['i']] == '', f4993     # matched by 2MASS id; junk name hidden
    assert any(f['cat'] == 'NGC 5194' for f in meta['famous'])                                          # matched by position
    antf = next(f for f in meta['famous'] if f['cat'] == 'NGC 4038')
    assert antf['i'] == pos[rows[ant]['ID']] and antf['also'] == ['NGC 4039'] and antf['name'] == 'Antennae Galaxies', antf
    assert sum(f['name'] == 'Antennae Galaxies' for f in meta['famous']) == 1
    sid, sp = universe.parse_sesame('# NGC 4993\n%J 197.44871198 -23.38397612 = 13 09 47.691\n%I NGC 4993\n%I 2MASX J13094770-2323017\n')
    assert sid == '13094770-2323017' and abs(sp[0] - 197.4487) < 1e-3 and sp[1] < -23
    assert universe.clean_name('g1309477-232302') == '' and universe.clean_name('A0627-1719') == '' and universe.clean_name('ARP 244') == 'ARP 244'
    blob = (universe.SITE_DIR / 'galaxies.bin').read_bytes()
    n = meta['n']
    assert blob[:4] == b'SLU2' and int.from_bytes(blob[4:8], 'little') == n and len(blob) == 16 + n * 18 and meta['txt'].endswith('galaxies-names.txt')
    G = universe.read_bin(universe.SITE_DIR / 'galaxies.bin', universe.SITE_DIR / 'galaxies-names.txt')
    xyz, env, nmb, czs, how, meth = G['xyz'], G['env'], G['nmb'], G['cz'], G['how'], G['meth']
    assert all(G['ids'][k] == i for i, k in pos.items()), 'the IDs rebuilt from the compact file are the survey IDs'
    rr = np.linalg.norm(xyz, axis=1)
    # the field galaxies sit where they were put: the velocity corrections were undone exactly, nothing else moved them
    d_out = np.array([rr[pos[rows[i]['ID']]] for i in sorted(intended)[:400]])
    d_want = np.array([intended[i] for i in sorted(intended)[:400]])
    assert np.abs(d_out - d_want).max() < 0.05, np.abs(d_out - d_want).max()
    coma = nmb == 150
    d = rr[coma]
    want = float(universe.comoving_mpc(7300.0))
    assert coma.sum() >= 150 and np.ptp(d) < 1.0 and abs(d.mean() - want) < 0.3 and 103 < want < 104, (np.ptp(d), d.mean(), want)   # finger of God collapsed
    assert (env[coma] == 3).mean() > 0.9, np.bincount(env[coma])                                       # a knot
    assert (how[coma] == 1).all()
    fil = np.abs(np.linalg.norm(xyz[:, 1:] - [60, 10], axis=1)) < 3
    fil &= np.abs(xyz[:, 0]) < 80
    assert fil.sum() > 800 and np.isin(env[fil], [2, 3]).mean() > 0.7, (fil.sum(), np.bincount(env[fil], minlength=5))   # the filament
    # measured distances
    k32, kscl, krough = pos[rows[m32]['ID']], pos[rows[scl]['ID']], pos[rows[rough]['ID']]
    assert abs(rr[k32] - 0.79) < 0.015 and how[k32] == 2 and meth[k32] == 1 and czs[k32] == -200, (rr[k32], how[k32], meth[k32])
    assert abs(rr[kscl] - 3.61) < 0.015 and how[kscl] == 2, rr[kscl]
    assert abs(rr[krough] - 30.0) < 0.2 and how[krough] == 0 and meth[krough] == 0, (rr[krough], how[krough])
    kg = [pos[rows[i]['ID']] for i in grp]
    assert abs(rr[kg[0]] - 10.0) < 0.02 and abs(rr[kg[1]] - 10.4) < 0.02 and how[kg[0]] == 2 and meth[kg[0]] == 8
    assert abs(rr[kg[2]] - 10.2) < 0.02 and abs(rr[kg[3]] - 10.2) < 0.02 and how[kg[2]] == 3 == how[kg[3]], rr[kg]
    assert meta['n_measured'] == 4 and meta['n_group_measured'] == 2, (meta['n_measured'], meta['n_group_measured'])
    assert rows[m31]['ID'] not in pos and any('check NGC 4889' in x for x in logs), logs[-6:]
    k81, kmem, kh = pos[rows[m81]['ID']], pos[rows[lvmem]['ID']], pos[rows[lvh]['ID']]
    assert abs(rr[k81] - 3.61) < 0.015 and how[k81] == 4 and meth[k81] == 1 and czs[k81] == -34, (rr[k81], how[k81], meth[k81])
    assert abs(rr[kmem] - 3.82) < 0.015 and how[kmem] == 5 and meth[kmem] == 0, (rr[kmem], how[kmem])
    assert abs(rr[kh] - 11.0) < 0.1 and how[kh] == 0 and meta['n_local_volume'] == 2, (rr[kh], how[kh], meta['n_local_volume'])
    assert len(G['names']) == n and 'NGC 4889' in G['names']
    assert any(f['name'] == 'NGC 4889' and G['names'][f['i']] == 'NGC 4889' for f in meta['famous']), meta['famous']
    assert bool(np.all(np.diff([universe.id_units(i)[0] for i in G['ids']]) >= 0))   # sorted by RA
    # positions in the compact file: within 0.02 Mpc in distance and a fraction of an arcsecond in direction
    rows_by_id = {r['ID']: r for r in rows}
    for k in range(0, n, 97):
        r = rows_by_id[G['ids'][k]]
        w_dir = universe.unit([float(r['RAJ2000'])], [float(r['DEJ2000'])])[0]
        assert math.degrees(math.acos(min(1.0, float(xyz[k] @ w_dir) / rr[k]))) * 3600 < 1.0, (k, G['ids'][k])
    coma_mark = next(m for m in meta['landmarks'] if m['name'] == 'Coma Cluster')
    assert coma_mark['measured'] and abs(coma_mark['dist'] - want) < 0.3 and '150 galaxies' in coma_mark['note'], coma_mark
    assert not next(m for m in meta['landmarks'] if m['name'] == 'Virgo Cluster')['measured'] and meta['virgo_dist'] is None
    assert meta['messier']['M87'] == 'NGC 4486' and len(meta['eq_to_sg']) == 3 and 'Mould' in meta['method']
    assert universe.clean_name('NGC_0253') == 'NGC 253' and universe.clean_name('MESSIER_087') == 'NGC 4486' and universe.clean_name('2MASXJ123') == ''
    assert universe.morph(' -5') == 1 and universe.morph(' 3A2s') == 3 and universe.morph(' 98') == 0 and universe.morph('10') == 5
    from spacelab import db, publish
    con = db.connect()
    db.kv_set(con, 'universe', meta)
    data, files = publish.build_data(con, {})
    uv = data['beyond']['universe']
    assert uv['bin'] == 'files/plots/universe/galaxies.bin' and uv['txt'] == 'files/plots/universe/galaxies-names.txt' and uv['n'] == n, uv.keys()
    assert {'plots/universe/galaxies.bin', 'plots/universe/galaxies-names.txt'} <= set(files) and not publish.validate(data)
    assert 'universe.js' in publish.SITE_FILES



@test
def catalogue_copies_survive_outages():
    import gzip, os
    from spacelab import localcat, variables, universe
    logs = []
    head = '#Name: X\nRAJ2000\tDEJ2000\tASASSN-V\tType\tPer\ndeg\tdeg\t\t\td\n---\t---\t---\t---\t---\n'
    good = head + '10.0000\t+20.0000\tJ004000.00+200000.0\tEW\t0.4\n10.0100\t+20.0000\tJ004002.40+200000.0\tROT\t3.1\n50.0\t-5.0\tJ032000.00-050000.0\tEA\t2.0\n'
    name = 'ASAS-SN variables'
    p = localcat._path(name)
    if p.exists():
        p.unlink()
    # no copy and the service is down: the error is passed on, nothing is made up
    try:
        localcat.table_text(name, lambda: (_ for _ in ()).throw(ConnectionError('down')), lambda t: True)
        raise AssertionError('no copy + no service must fail')
    except ConnectionError:
        pass
    # a complete download is kept; while it is recent it is used without asking again
    assert localcat.table_text(name, lambda: good, lambda t: len(localcat.rows(t)) >= 3) == good and p.exists()
    asked = []
    assert localcat.table_text(name, lambda: asked.append(1) or 'x', lambda t: True) == good and not asked
    # once old: an incomplete download never replaces it, and an outage falls back to it (the log says so)
    old = time.time() - 40 * 86400
    os.utime(p, (old, old))
    assert localcat.table_text(name, lambda: head + '1\t1\tA\tB\t1\n', lambda t: len(localcat.rows(t)) >= 3, log=logs.append) == good
    assert gzip.decompress(p.read_bytes()).decode() == good and any('incomplete' in x for x in logs), logs
    assert localcat.table_text(name, lambda: (_ for _ in ()).throw(TimeoutError('slow')), lambda t: True, log=logs.append) == good
    assert any('did not answer' in x and 'copy kept on this Mac' in x for x in logs), logs
    # positions answered from the whole copy: within the radius only, nearest first, across RA 0/360 too
    os.utime(p, None)
    localcat._ARR.clear()
    hits = localcat.local_cone(name, 10.0, 20.0, 40)
    assert [h['name'] for h in hits] == ['J004000.00+200000.0', 'J004002.40+200000.0'] and hits[0]['sep'] == 0 and abs(hits[1]['sep'] - 33.8) < 0.2, hits
    assert hits[0]['type'] == 'EW' and hits[0]['period'] == 0.4 and localcat.local_cone(name, 200.0, 20.0, 40) == []
    # single-position answers: reused while fresh, used when the service is down, never older than a year
    calls = []
    ask = lambda: calls.append(1) or [{'name': 'V1', 'type': 'RR', 'period': 0.5, 'sep': 1.0}]
    assert localcat.cone('VSX', 120.0, -30.0, 30, ask)[0]['name'] == 'V1' and localcat.cone('VSX', 120.00001, -30.00001, 30, ask) and len(calls) == 1
    down = lambda: (_ for _ in ()).throw(ConnectionError('down'))
    later = time.time() + (localcat.FRESH_DAYS + 5) * 86400
    assert localcat.cone('VSX', 120.0, -30.0, 30, down, log=logs.append, now=later)[0]['name'] == 'V1' and any('could not be reached' in x for x in logs)
    try:
        localcat.cone('VSX', 120.0, -30.0, 30, down, now=time.time() + (localcat.KEEP_DAYS + 5) * 86400)
        raise AssertionError('an answer older than a year must not be used')
    except ConnectionError:
        pass
    try:
        localcat.cone('VSX', 1.0, 1.0, 30, down)
        raise AssertionError('never asked before + service down must fail')
    except ConnectionError:
        pass
    # the variable-star check: with every catalogue service down, ASAS-SN is still answered from the copy, the others are not
    real_viz, real_vsx = variables._vizier, variables._vsx_api
    variables._vizier = variables._vsx_api = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('down'))
    try:
        res, all_ok = variables.known(10.0, 20.0)
    finally:
        variables._vizier, variables._vsx_api = real_viz, real_vsx
    assert res['ASAS-SN variables'][0]['name'] == 'J004000.00+200000.0' and res['VSX'] is None and res['Gaia DR3 variability'] is None and not all_ok, res
    # the 3D map's tables come from the copy when the service is down
    two = localcat._path('2MRS')
    rowsx = ''.join(f'{i:08d}+0000000\t{i * 0.001:.5f}\t+10.00000\n' for i in range(universe.MIN_ROWS['2MRS']))
    two.parent.mkdir(parents=True, exist_ok=True)
    two.write_bytes(gzip.compress(('ID\tRAJ2000\tDEJ2000\n\t\t\n--\t--\t--\n' + rowsx).encode()))
    os.utime(two, (old, old))
    real = universe._asu
    universe._asu = lambda *a, **k: (_ for _ in ()).throw(ConnectionError('down'))
    try:
        got = universe._catalog('2MRS', logs.append)
    finally:
        universe._asu = real
    assert len(got) == universe.MIN_ROWS['2MRS'] and got[1]['ID'] == '00000001+0000000', len(got)
    two.unlink()
    from spacelab import config as _cfg, jobs as _jobs
    assert 'catalogs' in _jobs.JOBS and _cfg.DEFAULTS['schedule']['catalogs'] == 'every 168h'


@test
def health_check_finds_problems_and_alerts_once():
    from spacelab import health, db, config as cfgm
    con = db.connect()
    con.execute("DELETE FROM runs")
    now = time.time()
    add = lambda job, ago_h, st, summ='': con.execute('INSERT INTO runs (job, started, finished, status, summary, trigger) VALUES (?,?,?,?,?,?)',
                                                     (job, now - ago_h * 3600, now - ago_h * 3600 + 60, st, summ, 'schedule'))
    add('variables', 30, 'ok'); add('variables', 20, 'error', 'no VizieR mirror answered'); add('variables', 8, 'error', 'no VizieR mirror answered')
    add('publish', 20, 'ok'); add('solar', 2, 'ok'); add('universe', 2000, 'ok')
    con.commit()
    cfg = cfgm.load()
    assert health._hours('every 3h') == 3 and health._hours('01:30') == 24 and health._hours(None) is None
    jobs = {j['job']: j for j in health.job_report(con, cfg, now)}
    assert jobs['variables']['fails_in_a_row'] == 2 and not jobs['solar']['overdue'] and jobs['publish']['overdue'] and jobs['universe']['overdue']
    assert jobs['selftest']['every_h'] is None and not jobs['selftest']['overdue']          # run by hand: never "overdue"
    # services: a mirror that answers with an error page or an empty header is down; one that sends a table is up
    table = '#Name: I/355\n#Table\tI_355_gaiadr3:\nSource\n\n--\n1\n'
    fake = lambda u: (table if 'idia' in u else '#RESOURCE=\n#Name:\n' if 'cfa' in u else 'Content-Type: text/html\n****\n' if 'vizier' in u else 'ok')
    sv = {s['name']: s for s in health.probe(fake)}
    assert sv['VizieR, South Africa mirror']['ok'] and not sv['VizieR, Harvard mirror']['ok'] and not sv['VizieR, Strasbourg (CDS)']['ok'] and sv['arXiv']['ok'] and not sv['ZTF light curves (IRSA)']['ok']
    down = {'VizieR, Harvard mirror': now - 7 * 3600, 'VizieR, Strasbourg (CDS)': now - 9 * 3600, 'VizieR, India mirror': now - 3600, 'arXiv': now - 7 * 3600}
    arx = dict(sv['arXiv'], ok=False, msg='HTTP 503')
    probs = health.problems(list(jobs.values()), [x for x in sv.values() if x['name'] != 'arXiv'] + [arx], {'free_gb': 12.0}, 20.0, down, now)
    keys = {p['key'] for p in probs}
    assert {'job:variables:failing', 'job:publish:overdue', 'site:stale', 'disk:low', 'service:arXiv'} <= keys, keys
    assert not any(k.startswith('service:VizieR') for k in keys) and 'job:solar:overdue' not in keys       # one mirror still answers; on time
    nov = health.problems(list(jobs.values()), [dict(x, ok=False) for x in sv.values() if x['name'].startswith('VizieR')],
                          None, None, {x: now - 8 * 3600 for x in sv if x.startswith('VizieR')}, now)
    assert [p['key'] for p in nov if p['key'].startswith('service:')] == ['service:VizieR (every mirror)'], nov
    add('health', 0.01, 'running'); con.commit()                                                        # its own first run: not overdue
    assert not {j['job']: j for j in health.job_report(con, cfg, now)}['health']['overdue']
    con.execute("DELETE FROM runs WHERE job='health'"); con.commit()
    assert any('failed 2 times in a row' in p['text'] and 'no VizieR mirror answered' in p['text'] for p in probs)
    # alerts: each problem once, not again within a day, again after a day, and a "back to normal" when solved
    sink = []
    con.execute("DELETE FROM kv WHERE k='health_alerts'")
    assert len(health.alert(con, cfg, probs, now, sink)) == len(probs) and len(sink) == len(probs)
    sink.clear()
    assert health.alert(con, cfg, probs, now + 3600, sink) == [] and not sink
    assert len(health.alert(con, cfg, probs, now + 25 * 3600, sink)) == len(probs)
    sink.clear()
    left = [p for p in probs if p['key'] != 'disk:low']
    sent = health.alert(con, cfg, left, now + 26 * 3600, sink)
    assert sent == ['ok:disk:low'] and sink[0][0] == 'Space Lab: back to normal' and 'Only 12.0 GB free' in sink[0][1], (sent, sink)
    # the hourly job: probes, remembers since when a service is down, reports, alerts through the sink
    class C:
        pass
    c = C(); c.con = con; c.logs = []; c.log = c.logs.append
    summ = health.run(c, fetch=fake, sink=[])
    st = db.kv_get(con, 'health_services')
    assert 'VizieR, Harvard mirror' in st['down_since'] and 'VizieR, South Africa mirror' not in st['down_since'] and 'outside services answering' in summ
    rep = health.report(con, cfg)
    assert rep['disk']['free_gb'] > 0 and rep['checked'] and len(rep['services']) == len(health.SERVICES) and 'problems' in rep
    from spacelab import jobs as _jobs
    assert 'health' in _jobs.JOBS and cfgm.DEFAULTS['schedule']['health'] == 'every 1h' and cfgm.DEFAULTS['alerts'] == {'mac': True, 'ntfy_topic': ''}
    con.execute("DELETE FROM runs"); con.execute("DELETE FROM kv WHERE k IN ('health_alerts','health_services')"); con.commit()


@test
def ztf_second_opinion_on_variable_stars():
    from spacelab import variables as V
    rng = np.random.default_rng(7)
    def csv_of(period, amp_mag, base=15.0, n=320, err=0.01, band='zr', flagged=0):
        mjd = np.sort(58300 + rng.uniform(0, 5 * 365, n) + np.round(rng.uniform(0, 1, n)) * 0)  # nightly sampling, as from the ground
        mjd = np.floor(mjd) + 0.25 + rng.normal(0, 0.03, n)
        mag = base + (amp_mag / 2 * np.sin(2 * np.pi * mjd / period) if period else 0) + rng.normal(0, err, n)
        rows = ['oid,expid,hjd,mjd,mag,magerr,catflags,filtercode,ra,dec']
        rows += [f'1,{i},{m + 2400000.5:.5f},{m:.6f},{g:.4f},{err},{32768 if i < flagged else 0},{band},150.0,20.0' for i, (m, g) in enumerate(zip(mjd, mag))]
        return '\n'.join(rows) + '\n'
    r = V.ztf_check(150.0, 20.0, 2.3, 30.0, fetch=lambda p: csv_of(2.3, 0.03))
    assert r['ok'] is True and 'the same period' in r['verdict'] and abs(r['p_best'] - 2.3) < 0.01 and r['band'] == 'r' and r['n'] == 320, r
    r = V.ztf_check(150.0, 20.0, 0.62, 200.0, fetch=lambda p: csv_of(0.31, 0.15))          # an eclipsing star often shows half its period
    assert r['ok'] is True and 'half the period' in r['verdict'], r
    r = V.ztf_check(150.0, 20.0, 2.3, 30.0, fetch=lambda p: csv_of(None, 0))                # should be plain to see, and is not there
    assert r['ok'] is False and 'neighbouring star' in r['verdict'] and r['expect_snr'] >= 7, r
    r = V.ztf_check(150.0, 20.0, 2.3, 0.6, fetch=lambda p: csv_of(None, 0))                 # too small a change to tell either way
    assert r['ok'] is None and 'too faint' in r['verdict'], r
    r = V.ztf_check(150.0, 20.0, 2.3, 30.0, fetch=lambda p: csv_of(2.3, 0.03, base=11.0))
    assert r['ok'] is None and 'too bright' in r['verdict'], r
    r = V.ztf_check(150.0, 20.0, 2.3, 30.0, fetch=lambda p: csv_of(2.3, 0.03, n=60, flagged=30))   # flagged points are left out
    assert r['ok'] is None and 'too few clean measurements here (30)' in r['verdict'], r
    asked = []
    r = V.ztf_check(150.0, -45.0, 2.3, 30.0, fetch=lambda p: asked.append(p) or '')
    assert r['ok'] is None and 'south' in r['verdict'] and not asked
    err = ('<VOTABLE><RESOURCE type="results"><INFO name="QUERY_STATUS" value="ERROR">UsageFault: Unable to fetch url: '
           'https://irsa-shadow.ipac.caltech.edu/TAP/sync</INFO></RESOURCE></VOTABLE>')                      # what IRSA sent on 10 Nov 2026
    r = V.ztf_check(150.0, 20.0, 2.3, 30.0, fetch=lambda p: err)
    assert r['ok'] is None and r['verdict'].startswith('not checked yet') and 'Unable to fetch url' in r['error'], r
    got = []
    V.ztf_check(150.0, 20.0, 2.3, 30.0, fetch=lambda p: got.append(p) or csv_of(2.3, 0.03))
    assert got[0]['POS'] == 'CIRCLE 150.000000 20.000000 0.0014' and got[0]['FORMAT'] == 'csv'


@test
def fake_planets_measure_what_the_search_finds():
    from spacelab import injection as I, db, config as cfgm
    con = db.connect()
    I.ensure(con)
    con.execute('DELETE FROM injections')
    rng = np.random.default_rng(11)
    t = np.arange(1000.0, 1027.0, 30 / 1440)
    t = t[(t < 1013.0) | (t > 1014.5)]                                       # the gap in the middle of a sector
    f = 1200.0 * (1 + 0.002 * np.sin(2 * np.pi * t / 6.0) + rng.normal(0, 8e-4, t.size))   # a slow wave and noise, in e-/s
    shp = I.transit_shape(np.array([0.0, 0.049, 0.5, 1.0]), 1.0, 0.0, 0.1, 0.01)
    assert abs(shp[0] - 0.99) < 1e-12 and 0.99 < shp[1] < 1 and shp[2] == 1 and abs(shp[3] - 0.99) < 1e-12, shp
    tc = dict(cfgm.DEFAULTS['tess'])
    star = {'rad': 0.4, 'mass': 0.4}
    big = I.trial(t, f, star, 3.1, 0.01, 1000.7, tc)                       # a 1% dip every 3.1 days: plainly found
    assert big['found'] == 1 and abs(big['p_found'] - 3.1) < 0.03 and big['sde'] >= tc['sde_min'], big
    small = I.trial(t, f, star, 3.1, 0.00005, 1000.7, tc)                  # 50 ppm in 800 ppm noise: not found
    assert small['found'] == 0 and small['alias'] == 0, small
    # cells, plain sentences, and the picture
    rows = [{'quiet': 1, 'period': 1.5, 'depth': 3e-4, 'found': k < 3, 'alias': 0} for k in range(10)] + \
           [{'quiet': 1, 'period': 3.0, 'depth': 3e-3, 'found': 1, 'alias': 0} for _ in range(12)] + \
           [{'quiet': 0, 'period': 3.0, 'depth': 3e-3, 'found': k % 2, 'alias': 0} for k in range(12)]
    g = I.grid(rows, 1)
    assert g['n'][1][0] == 10 and g['found'][1][0] == 3 and g['n'][2][3] == 12 and sum(map(sum, g['n'])) == 22, g
    h = I.headline(g)
    assert h[0].startswith('30% of shallow dips') and h[-1].startswith('100% of deep dips') and '(12 trials)' in h[-1], h
    png = I.plot(rows)
    assert png.is_file() and png.stat().st_size > 10000
    # a quiet star's light curve is kept; the job picks light curves, hides planets, stores and sums up
    assert I.keep_curve(400, t, f) and (I.INJ_DIR / 'TIC400.npz').is_file() and I.keep_curve(404, t[:50], f[:50]) is None
    con.execute('INSERT OR REPLACE INTO tess_targets(tic, ra, dec, tmag, rad, mass, status) VALUES (400, 1, 1, 11, 0.3, 0.3, "done")')
    pool = I.curves(con)
    assert any(x[0] == 400 and x[2] == 1 and x[3]['rad'] == 0.3 for x in pool)
    calls = []
    def fake_search(ts, ys, es, pmin, pmax, engine):
        calls.append(len(ts))
        return {'period': 2.0, 'sde': 12.0, 'snr': 9.0}
    class C:
        pass
    c = C(); c.con = con; c.cfg = {'injection': {'trials_per_run': 6}}; c.logs = []; c.log = c.logs.append; c.time_left = lambda: 1e9
    summ = I.run(c, search=fake_search, rng=np.random.default_rng(3))
    n = db.one(con, 'SELECT COUNT(*) AS n FROM injections')['n']
    assert n == 6 and len(calls) == 6 and '6 fake planets hidden this run' in summ, (n, summ)
    s = db.kv_get(con, 'injection')
    assert s['n'] == 6 and s['plot'] == 'plots/injection/completeness.png' and 'half-hour' in s['note'] and s['n_quiet'] >= 1
    from spacelab import jobs as _jobs
    assert 'injection' in _jobs.JOBS and cfgm.DEFAULTS['schedule']['injection'] == 'every 720h'
    con.execute('DELETE FROM injections'); con.execute('DELETE FROM tess_targets WHERE tic=400'); con.commit()
    (I.INJ_DIR / 'TIC400.npz').unlink()


@test
def what_we_got_wrong_is_published():
    from spacelab import corrections, publish, db
    assert len(corrections.CORRECTIONS) >= 10 and all(len(c) == 7 and all(c) for c in corrections.CORRECTIONS)
    for d, area, wrong, effect, found, fix, sha in corrections.CORRECTIONS:
        assert len(d) == 10 and d[4] == '-' and len(sha) >= 7 and all(ch in '0123456789abcdef' for ch in sha), (d, sha)
    pub = corrections.public('owner/repo')
    assert pub[0]['date'] >= pub[-1]['date'] and pub[0]['url'] == f"https://github.com/owner/repo/commit/{pub[0]['sha']}"
    assert all(p['url'] is None for p in corrections.public(''))
    got = publish._corrections({'share': {'github_repo': 'owner/repo'}})              # what build_data puts in data['site']['corrections']
    assert got[0]['url'].startswith('https://github.com/owner/repo/commit/') and "'corrections': _corrections(cfg)" in open(publish.__file__).read()


@test
def supernova_watch_alerts_for_nearby_galaxies():
    from spacelab import snwatch as W, db
    con = db.connect()
    con.execute("DELETE FROM kv WHERE k='sn_seen'"); con.execute("DELETE FROM tasks WHERE key LIKE 'sn:%'"); con.commit()
    def unit(ra, dec):
        a, d = math.radians(ra), math.radians(dec)
        return [math.cos(d) * math.cos(a), math.cos(d) * math.sin(a), math.sin(d)]
    gal = {'u': np.array([unit(150.0, 20.0), unit(200.0, -10.0), unit(30.0, 5.0)]), 'd': np.array([18.0, 80.0, 300.0]),
           'name': ['NGC 9001', 'NGC 9002', 'UGC 9003']}
    now = time.time(); mjd = now / 86400 + 40587
    items = {'SN': [{'oid': 'ZTF26near', 'meanra': 150.0 + 40 / 3600, 'meandec': 20.0, 'firstmjd': mjd - 1, 'lastmjd': mjd, 'probability': 0.7},
                    {'oid': 'ZTF26old', 'meanra': 150.0, 'meandec': 20.0, 'firstmjd': mjd - 40, 'lastmjd': mjd, 'probability': 0.9}],
             'SNIa': [{'oid': 'ZTF26mid', 'meanra': 200.0, 'meandec': -10.0 + 30 / 3600, 'firstmjd': mjd - 3, 'lastmjd': mjd, 'probability': 0.8},
                      {'oid': 'ZTF26near', 'meanra': 150.0 + 40 / 3600, 'meandec': 20.0, 'firstmjd': mjd - 1, 'lastmjd': mjd, 'probability': 0.9},
                      {'oid': 'ZTF26far', 'meanra': 30.0, 'meandec': 5.0, 'firstmjd': mjd - 2, 'lastmjd': mjd, 'probability': 0.9},
                      {'oid': 'ZTF26field', 'meanra': 100.0, 'meandec': 50.0, 'firstmjd': mjd - 2, 'lastmjd': mjd, 'probability': 0.9}]}
    asked = []
    fetch = lambda p: asked.append(p) or {'items': items.get(p['class_name'], [])}
    light = lambda u: {'detections': [{'mjd': mjd - 1, 'magpsf': 17.9, 'fid': 1}, {'mjd': mjd - 0.2, 'magpsf': 16.4, 'fid': 2}]}
    sink, logs = [], []
    out = W.check(con, {}, logs.append, None, fetch=fetch, gal=gal, now=now, sink=sink, light=light)
    assert {q['classifier'] for q in asked} == {'stamp_classifier', 'lc_classifier'} and all(q['page_size'] == 100 for q in asked)
    assert [x['id'] for x in out] == ['ZTF26near', 'ZTF26mid'], out          # far galaxy (300 Mpc), no galaxy, and 40-day-old: left out
    n = out[0]
    assert n['host'] == 'NGC 9001' and n['dist'] == 18.0 and 35 < n['sep'] < 45 and n['prob'] == 0.9 and n['kind'] == 'SNIa' and n['mag_last'] == 16.4 and n['band'] == 'r'
    assert len(sink) == 1 and 'ZTF26near in NGC 9001' in sink[0][1] and 'not confirmed' in sink[0][1].lower() and 'magnitude 16.4' in sink[0][1]
    assert db.one(con, "SELECT * FROM tasks WHERE key='sn:ZTF26near'") and not db.one(con, "SELECT * FROM tasks WHERE key='sn:ZTF26mid'")
    sink.clear()
    out2 = W.check(con, {}, logs.append, out, fetch=fetch, gal=gal, now=now + 3 * 3600, sink=sink, light=light)
    assert not sink and [x['id'] for x in out2] == ['ZTF26near', 'ZTF26mid']                   # one alert per supernova
    later = W.check(con, {}, logs.append, out2, fetch=lambda p: {'items': []}, gal=gal, now=now + 70 * 86400, sink=sink, light=light)
    assert later == []                                                                             # forgotten after 60 days
    real_load = W.load_galaxies
    W.load_galaxies = lambda: None
    try:
        assert W.check(con, {}, logs.append, [{'id': 'kept'}], fetch=fetch, gal=None, now=now) == [{'id': 'kept'}]    # no galaxy map yet: keep the last list
    finally:
        W.load_galaxies = real_load
    assert W.check(con, {}, logs.append, ['junk', None], fetch=lambda p: {'items': []}, gal=gal, now=now, sink=[]) == []
    con.execute("DELETE FROM kv WHERE k='sn_seen'"); con.execute("DELETE FROM tasks WHERE key LIKE 'sn:%'"); con.commit()


@test
def around_earth_orbits_for_the_page():
    from spacelab import earthorbit as E, beyond, config as cfgm
    from datetime import datetime, timezone
    now = 1794300000.0
    ep = datetime.fromtimestamp(now - 3600, timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%f')
    def omm(nid, name, mm, ecc=0.0007, inc=51.6, raan=200.0, ma=280.0):
        return {'OBJECT_NAME': name, 'OBJECT_ID': '2000-001A', 'EPOCH': ep, 'MEAN_MOTION': mm, 'ECCENTRICITY': ecc, 'INCLINATION': inc,
                'RA_OF_ASC_NODE': raan, 'ARG_OF_PERICENTER': 80.0, 'MEAN_ANOMALY': ma, 'EPHEMERIS_TYPE': 0, 'CLASSIFICATION_TYPE': 'U',
                'NORAD_CAT_ID': nid, 'ELEMENT_SET_NO': 999, 'REV_AT_EPOCH': 1000, 'BSTAR': 0.0001, 'MEAN_MOTION_DOT': 0.00001, 'MEAN_MOTION_DDOT': 0}
    names = {n[0]: n[6] for n in E.NOTABLE}
    def fetch(p):
        if 'CATNR' in p:
            nid = p['CATNR']
            if nid == 63182:
                return [omm(nid, 'STARLINK-99999', 15.2)]                           # the wrong object: left out
            if nid == 25867:
                return [omm(nid, 'CXO', 0.378, ecc=0.76, inc=28.5)]                # Chandra's stretched 63-hour orbit
            if nid in (60133, 51850):
                return [omm(nid, names[nid], 1.0027, ecc=0.0001, inc=0.05)]
            return [omm(nid, ('ISS (ZARYA)' if nid == 25544 else names[nid]), 15.49)]
        g = p['GROUP']
        n = {'gps-ops': 32, 'galileo': 30, 'geo': 50, 'starlink': 900}[g]
        mm = {'gps-ops': 2.005, 'galileo': 1.7, 'geo': 1.0027, 'starlink': 15.06}[g]
        return [omm(70000 + k, f'{g.upper()}-{k}', mm, ecc=0.001, inc=55.0, raan=k * 7.0, ma=k * 13.0) for k in range(n)]
    logs = []
    meta = E.build(logs.append, fetch=fetch, now=now, rng=np.random.default_rng(1))
    full = json.loads(E.OUT.read_text())
    keys = {o['key']: o for o in full['objects']}
    assert 'spherex' not in keys and 'SPHEREx' in full['left_out'] and meta['n'] == len(E.NOTABLE) - 1, full['left_out']
    iss = keys['iss']
    r = np.linalg.norm(iss['pts'], axis=1)
    assert len(iss['pts']) == E.N_ORBIT and 5.5e3 < iss['period_s'] < 5.6e3 and 6650 < r.min() and r.max() < 6900 and 250 < iss['perigee_km'] < 500, (iss['period_s'], r.min(), r.max())
    cx = keys['chandra']
    assert cx['apogee_km'] > 100000 and cx['perigee_km'] < 30000 and cx['period_s'] > 2e5
    assert 35000 < keys['goes19']['perigee_km'] < 36500 and keys['goes19']['wiki'] == 'GOES-19'
    sw = full['swarms']
    assert len(sw['starlink']['pts']) == E.STARLINK_SAMPLE and sw['starlink']['total'] == 900 and sw['navigation']['total'] == 62
    assert sw['navigation']['names'] == ['GPS (32)', 'Galileo (30)'] and len(sw['geo']['pts']) == 50
    for p in sw['navigation']['pts'][:5] + sw['starlink']['pts'][:5]:
        rr, nn = np.array(p[:3]), np.array(p[3:6])
        assert abs(np.linalg.norm(nn) - 1) < 1e-3 and abs(rr @ nn) / np.linalg.norm(rr) < 1e-3       # the axis is square to where it is
    assert 700 < sw['navigation']['pts'][0][6] < 730 and 90 < sw['starlink']['pts'][0][6] < 100      # GPS: 12 h; Starlink: 95 min
    assert meta['json'] == 'plots/earth/orbits.json' and full['source'].startswith('Orbits: CelesTrak')
    # CelesTrak down: the file stays as it was
    before = E.OUT.read_bytes()
    assert E.build(logs.append, fetch=lambda p: [], now=now) is None and E.OUT.read_bytes() == before
    # the Sky watch job reuses a recent file and rebuilds an old one
    recent = dict(meta, made=time.time())
    assert beyond.earth_orbits(logs.append, recent) is recent
    from spacelab import publish
    assert 'earthview.js' in publish.SITE_FILES


@test
def variable_star_observatory_plans_measures_and_makes_aavso_files():
    """Another observer's automated observatory: tonight's targets (Target Tool export) -> N.I.N.A. CSV; images -> ensemble
    photometry against AAVSO chart stars -> one checked AAVSO Extended file per finished night. Made-up stars and place."""
    from datetime import datetime, timezone
    from astropy.io import fits
    from astropy.wcs import WCS
    from spacelab import config, db, jobs, server, vsobs, vsphot
    T = lambda x: datetime.fromisoformat(x).replace(tzinfo=timezone.utc).timestamp()
    con = db.connect()
    work = Path(tempfile.mkdtemp(prefix='vstars-'))
    (work / 'lights').mkdir()
    (work / 'tt.csv').write_text(
        'Star Name,RA,Dec,Var Type,Min Mag,Max Mag,Period,Obs Cadence,Obs Section,Last Data Point,Priority\n'
        'XX Tst,22:00:00.0,+45:00:00,UGSS,14.0,12.0,,1,"Alerts / Campaigns",2026-10-20 00:00:00,true\n'
        'ZZ Low,03:00:00.0,-80:00:00,M,13,9,300,7,Long Period Variables,2026-11-01 00:00:00,false\n'
        'WW Fnt,02:00:00.0,+20:00:00,EA,18.5,17.9,1.2,3,Eclipsing Variables,2026-11-01 00:00:00,false\n'
        'VV Ok,01:00:00.0,+40:00:00,RRAB,13.0,12.0,0.55,3,Short Period Pulsators,2026-11-09 00:00:00,false\n')
    vs = {'enabled': True, 'site': {'lat': 40.0, 'lon': -100.0, 'elev_m': 100}, 'targettool_csv': str(work / 'tt.csv'),
          'images_dir': str(work / 'lights'), 'pixel_scale': 1.5, 'obscode': 'TEST1', 'transform': {'V': ['B', 'V', 0.05]},
          'targettool_key': 'secret-test-key-1234'}
    cfg = config._merge(config.DEFAULTS, {'vstars': vs})
    logs = []

    class Ctx:
        pass
    cx = Ctx(); cx.con, cx.log, cx.cfg, cx.time_left = con, logs.append, cfg, (lambda: 1e9)
    # a switched-off observatory is neither scheduled nor checked by the health check
    assert not jobs.wanted('vsphot', config.DEFAULTS) and jobs.wanted('vsphot', cfg) and jobs.wanted('tess', config.DEFAULTS)
    assert vsobs.run(type('C', (), {'con': con, 'log': logs.append, 'cfg': config.DEFAULTS})()).startswith('variable-star observatory not set up')
    # ---- the plan: the overdue alert star first; the one too far south and the one too faint are left out, with the reason
    api_rows = [{'star_name': 'AB Api', 'ra': 15.0, 'dec': 30.0, 'obs_section': ['Alerts / Campaigns'], 'obs_cadence': 2, 'priority': True,
                 'last_data_point': '2026-11-01 12:00:00', 'min_mag': 13.5, 'max_mag': 12.5, 'obs_mode': 'photometry'}]
    assert vsobs.normalise(api_rows[0], 'x')['ra'] == 15.0 and vsobs.normalise({'Star Name': 'A', 'RA': '01 00 00', 'Dec': '-10 30 00'}, 'x')['dec'] == -10.5
    got = vsobs.target_tool(dict(vs, targettool_key=''), log=logs.append)
    assert [t['name'] for t in got] == ['XX Tst', 'ZZ Low', 'WW Fnt', 'VV Ok'] and got[0]['ra'] == 330.0 and got[0]['priority']
    assert vsobs.target_tool(vs, fetch=lambda: {'targets': api_rows}, log=logs.append)[0]['name'] == 'AB Api'
    now = T('2026-11-10T20:00:00')
    cx.cfg = config._merge(config.DEFAULTS, {'vstars': dict(vs, targettool_key='')})
    out = vsobs.run(cx, now=now)
    plan = db.kv_get(con, 'vs_plan')
    names = [t['name'] for t in plan['targets']]
    assert set(names) == {'XX Tst', 'VV Ok'} and plan['day'] == '2026-11-10', (out, plan['left'])
    xx = next(t for t in plan['targets'] if t['name'] == 'XX Tst')
    assert xx['score'] > 9 and xx['why'][0] == 'AAVSO alert or campaign' and 'overdue' in xx['why'][1], xx['why']
    left = {x['name']: x['why'] for x in plan['left']}
    assert 'not high enough' in left['ZZ Low'] and 'too faint' in left['WW Fnt'], left
    assert [e['exp'] for e in xx['exposures']] == [90, 45, 45, 45] and xx['class'] == 'VS long'    # magnitude 13: longer than at 12
    csv_text = (config.DATA / plan['csv']).read_text()
    assert csv_text.splitlines()[0] == 'Type,Name,Ra,Dec,Rotation,ROI' and 'VS long,XX Tst,22h 00m 00s,"45º 00\' 00""",0,100' in csv_text, csv_text
    assert vsobs.hms(359.99999) == '00h 00m 00s' and vsobs.dms(-0.5) == '-00º 30\' 00"'
    # ---- three images of XX Tst (B and V with a plate solution, V without one), a flat and an H-alpha image to skip
    rng = np.random.default_rng(7)
    RA0, DE0, SC, N = 330.0, 45.0, 1.5, 600
    w = WCS(naxis=2); w.wcs.ctype = ['RA---TAN', 'DEC--TAN']; w.wcs.crval = [RA0, DE0]; w.wcs.crpix = [N / 2 + .5, N / 2 + .5]; w.wcs.cdelt = [-SC / 3600, SC / 3600]
    pos = lambda k: w.all_pix2world(*rng.uniform(60, N - 60, (2, k)), 0)
    cra, cde = pos(9)
    comps = []
    for k in range(9):
        V, bv = rng.uniform(11.0, 14.0), rng.uniform(0.3, 1.2)
        comps.append({'ra': float(cra[k]), 'dec': float(cde[k]), 'V': V, 'B': V + bv, 'auid': f'000-TST-{k:03d}', 'label': str(int(round(V * 10)))})
    fra, fde = pos(30)
    field = [{'ra': float(a), 'dec': float(b), 'V': float(rng.uniform(12, 15.5))} for a, b in zip(fra, fde)]
    TV, TBV = 12.30, 0.90
    yy, xx_ = np.mgrid[0:N, 0:N]

    def image(band, wcs_in, when, mag_t, imtype='LIGHT', filt=None):
        img = np.full((N, N), 500.0)
        def put(ra, de, m):
            x, y = w.all_world2pix([ra], [de], 0)
            x0, y0 = int(round(x[0])), int(round(y[0]))
            sl = (slice(max(0, y0 - 12), y0 + 13), slice(max(0, x0 - 12), x0 + 13))
            img[sl] += 10 * 10 ** (-0.4 * (m - 22.0)) / (2 * np.pi * 1.69) * np.exp(-((xx_[sl] - x[0]) ** 2 + (yy[sl] - y[0]) ** 2) / 3.38)
        put(RA0, DE0, mag_t)
        for c in comps:
            put(c['ra'], c['dec'], c[band] + (0.4 if c['auid'].endswith('003') else 0))      # comparison star 003 is secretly variable
        for f in field:
            put(f['ra'], f['dec'], f['V'] + (0.7 if band == 'B' else 0))
        h = fits.Header()
        if wcs_in:
            h.update(w.to_header())
        h.update({'OBJECT': 'XX Tst', 'FILTER': filt or band, 'EXPTIME': 10.0, 'DATE-OBS': when, 'IMAGETYP': imtype, 'RA': RA0 + .003, 'DEC': DE0 - .002})
        return fits.PrimaryHDU(rng.normal(img, np.sqrt(img) + 3).astype(np.float32), h)
    frames = [('B', True, '2026-11-11T02:00:00', 'LIGHT', None), ('V', True, '2026-11-11T02:01:00', 'LIGHT', None),
              ('V', False, '2026-11-11T02:30:00', 'LIGHT', None), ('V', True, '2026-11-11T01:00:00', 'FLAT', None),
              ('V', True, '2026-11-11T01:10:00', 'LIGHT', 'Ha')]
    for i, (band, wi, when, it, filt) in enumerate(frames):
        p = work / 'lights' / f'XX_Tst_{i}.fits'
        image(band, wi, when, TV + (TBV if band == 'B' else 0), it, filt).writeto(p)
    tnow = T('2026-11-11T06:00:00')
    for p in (work / 'lights').iterdir():
        os.utime(p, (tnow - 3600, tnow - 3600))
    asked = []

    def vsp(q):
        asked.append(q)
        assert q['star'] == 'XX Tst' and q['format'] == 'json' and q['fov'] == 30, q
        sexa = lambda v: f'{int(v):02d}:{int(v * 60 % 60):02d}:{v * 3600 % 60:05.2f}'           # VSP style: 21:58:31.07, +44:57:10.10
        return {'chartid': 'X12345AB', 'photometry': [{'auid': c['auid'], 'label': c['label'], 'ra': sexa(c['ra'] / 15), 'dec': '+' + sexa(c['dec']),
                                                       'bands': [{'band': 'V', 'mag': round(c['V'], 3), 'error': 0.01}, {'band': 'B', 'mag': round(c['B'], 3), 'error': 0.02}]}
                                                      for c in comps]}
    gaia = lambda ra, dec, r, gmax: [{'ra': c['ra'], 'dec': c['dec'], 'g': c['V']} for c in comps + field] + [{'ra': RA0, 'dec': DE0, 'g': TV}]
    cx.cfg = cfg
    out = vsphot.run(cx, vsp=vsp, gaia_fn=gaia, now=tnow)
    assert out.startswith('5 new images: 3 measured and ready, 0 to look at, 0 failed, 2 skipped') and len(asked) == 1, (out, logs[-6:])
    m = db.rows(con, 'SELECT * FROM vs_meas ORDER BY jd')
    assert [r['filter'] for r in m] == ['B', 'V', 'V'] and all(r['night'] == '2026-11-10' and r['status'] == 'ok' for r in m)
    assert abs(m[0]['mag'] - (TV + TBV)) < 0.03 and abs(m[1]['mag'] - TV) < 0.02 and abs(m[2]['mag'] - TV) < 0.02, [r['mag'] for r in m]
    assert all('000-TST-003' not in json.loads(r['comps']) and r['kname'] and abs(r['kdev']) < 0.05 for r in m)    # variable comp left out
    assert 'Gaia' in db.one(con, "SELECT note FROM vs_frames WHERE path LIKE '%XX_Tst_2.fits'")['note']                # solved without a WCS
    assert {r['note'][:20] for r in db.rows(con, "SELECT note FROM vs_frames WHERE status='skipped'")} == {'not a light frame (f', "filter 'Ha' is not i"}
    assert not (config.DATA / 'vstars' / 'reports').exists() or not list((config.DATA / 'vstars' / 'reports').iterdir())   # the night is not over
    assert (config.PLOTS / 'vstars' / 'XX_Tst-2026-11-10.png').exists()
    assert vsphot.which_star(fits.Header({'OBJECT': '', 'RA': 330.01, 'DEC': 45.01}), vsphot.known_targets(con))['name'] == 'XX Tst'
    # ---- next afternoon: one AAVSO file for the night, grouped, V transformed, checked
    out = vsphot.run(cx, vsp=vsp, gaia_fn=gaia, now=T('2026-11-11T20:00:00'))
    assert out.endswith('1 AAVSO file(s) made'), out
    txt = (config.DATA / 'vstars' / 'reports' / 'aavso-2026-11-10.txt').read_text()
    lines = [ln.split(',') for ln in txt.splitlines() if not ln.startswith('#')]
    assert txt.startswith('#TYPE=EXTENDED\n#OBSCODE=TEST1\n') and '#DATE=JD' in txt and vsphot.validate(txt) == [], vsphot.validate(txt)
    assert [(ln[4], ln[5], ln[7], ln[8], ln[12], ln[13]) for ln in lines] == [('B', 'NO', 'ENSEMBLE', 'na', '1', 'X12345AB'),
                                                                               ('V', 'YES', 'ENSEMBLE', 'na', '1', 'X12345AB'), ('V', 'NO', 'ENSEMBLE', 'na', '2', 'X12345AB')], lines
    m = db.rows(con, 'SELECT * FROM vs_meas ORDER BY jd')
    assert abs(m[1]['mag'] - m[1]['mag_raw'] - 0.05 * ((m[0]['mag_raw'] - m[1]['mag_raw']) - m[1]['ci_comp'])) < 1e-9 and m[1]['trans'] == 1
    assert db.one(con, "SELECT status FROM tasks WHERE key='vs:upload:aavso-2026-11-10.txt'")['status'] == 'open'
    bad = txt.replace('ENSEMBLE,na', 'ENSEMBLE,9.1', 1).replace(',B,NO,', ',XX,NO,', 1).replace('#OBSCODE=TEST1', '#OBSCODE=')
    assert {p[2][:16] for p in vsphot.validate(bad)} == {'no AAVSO observe', 'XX is not an AAV', 'an ensemble need'}, vsphot.validate(bad)
    # ---- the local page: the tab, rejecting a filed measurement remakes the file, marking it uploaded, the settings (key masked)
    config.replace_user('vstars', vs)
    try:
        x = server.api_vs()
        assert x['settings']['targettool_key'].startswith(server.MASK) and 'secret' not in json.dumps(x) and all(r[1] for r in x['ready'] if 'Image' not in r[0])
        assert len(x['recent']) == 3 and x['reports'][0]['file'] == 'aavso-2026-11-10.txt' and x['plots'] == ['plots/vstars/XX_Tst-2026-11-10.png']
        assert server.post_action(f'/api/vs/{m[2]["id"]}', {'action': 'reject'})[0] == 200
        txt2 = (config.DATA / 'vstars' / 'reports' / 'aavso-2026-11-10.txt').read_text()
        assert len([ln for ln in txt2.splitlines() if not ln.startswith('#')]) == 2 and vsphot.validate(txt2) == []
        assert server.post_action('/api/vs/0', {'action': 'uploaded', 'file': 'aavso-2026-11-10.txt'})[0] == 200
        assert db.one(con, 'SELECT status FROM vs_meas WHERE id=?', (m[0]['id'],))['status'] == 'reported'
        assert server.post_action(f'/api/vs/{m[0]["id"]}', {'action': 'reject'})[0] == 400              # already uploaded: change it at WebObs
        assert server.post_action('/api/vs/0', {'action': 'settings', 'settings': dict(vs, filters=[{'fits': 'V', 'aavso': 'Vj'}])})[0] == 400
        assert server.post_action('/api/vs/0', {'action': 'settings', 'settings': dict(vs, targettool_key=x['settings']['targettool_key'], max_targets=5)})[0] == 200
        kept = config.load()['vstars']
        assert kept['targettool_key'] == 'secret-test-key-1234' and kept['max_targets'] == 5
    finally:
        config.replace_user('vstars', {})
    assert not config.load()['vstars']['enabled']


@test
def confirmation_page_positions_for_useful_tonight():
    from spacelab import publish
    r = publish._scout_sky({'ra': '16:54', 'dec': '-19', 'Vmag': '20.5', 'rate': '2.2', 'unc': '59', 'uncP1': '76', 'elong': '45'})
    assert r == {'ra_deg': 253.5, 'dec_deg': -19.0, 'v': 20.5, 'rate': 2.2, 'unc': 59.0, 'unc1': 76.0, 'elong': 45.0}, r
    assert publish._scout_sky({'ra': None, 'dec': 'n/a'}) == {'ra_deg': None, 'dec_deg': None, 'v': None, 'rate': None, 'unc': None, 'unc1': None, 'elong': None}
    assert 'useful.js' in publish.SITE_FILES and publish.SITE_FILES.index('useful.js') > publish.SITE_FILES.index('stargazing.js')
    from spacelab import db
    con = db.connect()
    con.execute('INSERT OR REPLACE INTO neocp(name, first, last, data) VALUES(?,?,?,?)', ('P21test', time.time(), time.time(),
                json.dumps({'objectName': 'P21test', 'neoScore': 99, 'ra': '01:30', 'dec': '+12', 'Vmag': '19.1', 'rate': '1.5', 'unc': '4'})))
    try:
        data, _files = publish.build_data(con, {})
        o = next(x for x in data['asteroids']['neocp'] if x['name'] == 'P21test')
        assert o['ra_deg'] == 22.5 and o['dec_deg'] == 12.0 and o['v'] == 19.1 and o['neoScore'] == 99 and not publish.validate(data), o
    finally:
        con.execute("DELETE FROM neocp WHERE name='P21test'")


@test
def second_look_practice_cases_and_weighted_votes():
    """Ideas 51-52: practice cases with a known answer (found fake planets; signals proved to be two stars) for the
    "Planet or not?" review, and the weighted tally of visitors' answers from GoatCounter."""
    from spacelab import injection as I, db, publish, daily, config as cfgm
    con = db.connect()
    I.ensure(con)
    rng = np.random.default_rng(5)
    t = np.arange(1000.0, 1027.0, 30 / 1440)
    f = 1200.0 * (1 + rng.normal(0, 6e-4, t.size))
    star = {'rad': 0.4, 'mass': 0.4}
    r = I.trial(t, f, star, 3.1, 0.01, 1000.7, dict(cfgm.DEFAULTS['tess']), keep=True)
    assert r['found'] == 1 and r['_lc'][2].get('bls') is not None
    cur = con.execute('INSERT INTO injections (tic, quiet, period, depth, duration, t0, found, alias, at) VALUES (?,?,?,?,?,?,?,?,?)',
                      (424242, 1, 3.1, 0.01, r['duration'], 1000.7, 1, 0, time.time()))
    pth = I.case_plot(cur.lastrowid, 424242, star, r['_lc'])                # drawn exactly like a real candidate
    assert pth and pth.startswith('plots/injection/cases/') and 'inj' not in Path(pth).name and (cfgm.DATA / pth).stat().st_size > 20000
    con.execute('UPDATE injections SET plot=? WHERE id=?', (pth, cur.lastrowid))
    (cfgm.PLOTS / 'tess').mkdir(parents=True, exist_ok=True)
    for name, flags in (('eb.png', ['odd and even dips differ (6.2σ): likely two stars eclipsing']), ('big.png', ['too big for a planet (~25× Earth)'])):
        (cfgm.PLOTS / 'tess' / name).write_bytes(b'png')
        con.execute("INSERT INTO tess_cands (tic, kind, period, t0, depth, duration, flags, status, plot, created) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (515151 if name == 'eb.png' else 515152, 'periodic', 1.1, 3000.0, 0.02, 0.1, json.dumps(flags), 'auto-rejected', f'plots/tess/{name}', time.time()))
    try:
        ak = I.answer_key(con)
        by = {x['tic']: x for x in ak}
        assert by[424242]['answer'] == 'planet-like' and by[515151]['answer'] == 'eclipsing-binary' and 515152 not in by, ak
        assert all(len(x['key']) == 10 and not x['key'].startswith(('inj', 'eb')) for x in ak) and 'odd and even' in by[515151]['why']
        db.kv_set(con, 'second_look', {'tess': {'111': {'n': 3, 'w': {'planet-like': 5}}}, 't': time.time()})
        data, files = publish.build_data(con, {})
        pub = {x['tic']: x for x in data['tess']['answer_key']}
        assert pub[424242]['plot'] == 'files/' + pth and pth in files and data['community']['second_look']['tess']['111']['n'] == 3
        assert not publish.validate(data)
    finally:
        con.execute("DELETE FROM tess_cands WHERE tic IN (515151, 515152)"); con.execute('DELETE FROM injections WHERE tic=424242')
        db.kv_set(con, 'second_look', {})
    # the tally: two pages from GoatCounter; odd paths and unknown answers are ignored; weights multiply
    pages = [{'hits': [{'path': '/look-111-planet-like-w3', 'count': 2, 'path_id': 1}, {'path': 'look-111-noise-w1', 'count': 1, 'path_id': 2},
                       {'path': 'vote-2026-11-10-noise', 'count': 9, 'path_id': 3}], 'more': True},
             {'hits': [{'path': 'look-222-eclipsing-binary-w2', 'count': 4, 'path_id': 4}, {'path': 'look-222-banana-w2', 'count': 4, 'path_id': 5},
                       {'path': 'look-333-planet-like-w9', 'count': 1, 'path_id': 6}], 'more': False}]
    asked = []
    def fetch(p):
        asked.append(dict(p))
        return pages[len(asked) - 1]
    cfg = {'share': {'goatcounter': 'example', 'goatcounter_token': 'x'}}
    out = daily.second_look(cfg, lambda *a: None, fetch=fetch)
    assert out['tess'] == {'111': {'n': 3, 'w': {'planet-like': 6, 'noise': 1}}, '222': {'n': 4, 'w': {'eclipsing-binary': 8}}}, out
    assert 'exclude_paths' not in asked[0] and asked[1]['exclude_paths'] == [1, 2, 3]
    assert daily.second_look({}, lambda *a: None) is None


@test
def stored_paths_follow_a_moved_folder():
    from spacelab import db
    con = db.connect()
    old, new = '/tmp/example/Meridian/SpaceLab/', '/tmp/example/SpaceLab'
    con.execute("INSERT INTO runs(job, started, status, log) VALUES ('t', 1, 'ok', ?)", (old + 'logs/t/1.log',))
    rid = con.execute('SELECT MAX(id) FROM runs').fetchone()[0]
    con.execute("INSERT INTO tasks(key, project, kind, title, detail, status, created, updated) VALUES ('mv:1', 'x', 'x', 't', ?, 'done', 1, 1)",
                ('see "' + old + 'inbox/a.zip" and "' + old + 'data/b.txt"',))
    try:
        assert db.repair_moved_paths(con, new) == 2
        assert db.one(con, 'SELECT log FROM runs WHERE id=?', (rid,))['log'] == new + '/logs/t/1.log'
        assert db.one(con, "SELECT detail FROM tasks WHERE key='mv:1'")['detail'] == 'see "' + new + '/inbox/a.zip" and "' + new + '/data/b.txt"'
        assert db.repair_moved_paths(con, new) == 0
    finally:
        con.execute('DELETE FROM runs WHERE id=?', (rid,)); con.execute("DELETE FROM tasks WHERE key='mv:1'")

import urllib.error  # noqa: E402

if __name__ == '__main__':
    ok = sum(1 for _, g, _ in RES if g)
    for name, good, err in RES:
        print(('PASS ' if good else 'FAIL ') + name + ('' if good else '\n   ' + err.replace('\n', '\n   ')))
    print(f'\n{ok}/{len(RES)} passed')
    sys.exit(0 if ok == len(RES) else 1)
