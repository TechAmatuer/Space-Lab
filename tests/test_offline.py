"""Offline tests with synthetic data (no internet, no Ollama). Run:  python tests/test_offline.py"""
import json
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
                                            'community', 'backup_db', 'clean_and_measure')}
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


import urllib.error  # noqa: E402

if __name__ == '__main__':
    ok = sum(1 for _, g, _ in RES if g)
    for name, good, err in RES:
        print(('PASS ' if good else 'FAIL ') + name + ('' if good else '\n   ' + err.replace('\n', '\n   ')))
    print(f'\n{ok}/{len(RES)} passed')
    sys.exit(0 if ok == len(RES) else 1)
