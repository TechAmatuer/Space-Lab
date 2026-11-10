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
                                            'satellites', 'dark_sky_map', 'zip_index', 'live_images', 'asteroid_wiki',
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


@test
def universe_build_collapses_clusters_and_finds_the_web():
    from spacelab import universe
    rng = np.random.default_rng(3)
    rows, tully = [], []
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    def add(ra, dec, cz, nest=0, nmb=1, gv=None, name=''):
        c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg).galactic
        i = len(rows)
        rid = f'{i:08d}+{i % 90:02d}00000'
        rows.append({'ID': rid, 'RAJ2000': f'{ra:.5f}', 'DEJ2000': f'{dec:.5f}', 'GLON': f'{c.l.deg:.5f}', 'GLAT': f'{c.b.deg:.5f}',
                     'Ktmag': '10.5', 'type': ' 3A2s', 'cz': str(int(cz)), 'CAT': name})
        v = gv if gv is not None else universe.cmb_velocity(cz, c.l.deg, c.b.deg)
        tully.append({'PGC': str(i), '_RA.icrs': f'{ra:.4f}', '_DE.icrs': f'{dec:+.4f}', 'MType': '3', 'Vcmba': str(int(cz)),
                      'Nest': str(nest), 'Nmb': str(nmb), 'Dist': f'{v / 100:.1f}'})
    # a filament of field galaxies along x, plus a uniform background, plus a "cluster" whose members spread +-1000 km/s
    for k in range(3000):
        d = rng.uniform(20, 150)
        v = rng.normal(size=3); v /= np.linalg.norm(v)
        ra, dec = math.degrees(math.atan2(v[1], v[0])) % 360, math.degrees(math.asin(v[2]))
        add(ra, dec, d * 70)
    for k in range(2500):
        x = rng.uniform(-120, 120); y, z = rng.normal(0, 2.5, 2) + (60, 10)
        d = math.sqrt(x * x + y * y + z * z)
        add(math.degrees(math.atan2(y, x)) % 360, math.degrees(math.asin(z / d)), d * 70)
    for k in range(150):                                                  # Coma-like: one direction, one group velocity
        add(194.95 + rng.normal(0, 0.4), 27.98 + rng.normal(0, 0.4), 7300 + rng.normal(0, 1000), nest=100001, nmb=150, gv=7300,
            name='NGC_4889' if k == 0 else '')
    add(10.68, 41.27, -300, name='MESSIER_031')                          # Andromeda: blueshifted
    add(11.888, -25.288, 243, name='NGC_0253', gv=10)                     # Sculptor: far too close for a redshift distance
    add(197.4487, -23.3840, 2961, name='g1309477-232302')                 # NGC 4993 under a coordinate-style survey name
    groups = [{'Nest': '100001', 'Nmb': '150', 'PGC1': '44715', '<Vcmba>': '7300', 'DM': f'{5 * math.log10(73.0) + 25:.2f}',
               'sigV': '900', '_RA.icrs': '194.95', '_DE.icrs': '+27.98'}]
    ngc4993 = rows[-1]['ID']
    meta, pos = universe.build(lambda *a: None, (rows, tully, groups), famous_lookup={'NGC 4993': {'id': ngc4993, 'pos': None},
                                                                                       'NGC 5194': {'id': None, 'pos': (float(rows[5]['RAJ2000']) + 2e-3, float(rows[5]['DEJ2000']))}})
    np_ = {b['cat']: b for b in meta['blueshift']}
    assert meta['n'] == len(rows) - 2 and np_['NGC 224']['why'] == 'blue' and np_['NGC 224']['name'] == 'Andromeda Galaxy', meta['blueshift']
    assert np_['NGC 253']['why'] == 'near' and np_['NGC 253']['name'] == 'Sculptor Galaxy' and 'so close' in meta['near_note'], np_
    f4993 = next(f for f in meta['famous'] if f['cat'] == 'NGC 4993')
    txt0 = (universe.SITE_DIR / 'galaxies.txt').read_text().splitlines()
    assert txt0[f4993['i']].startswith(ngc4993) and txt0[f4993['i']].endswith('\t'), txt0[f4993['i']]     # matched by 2MASS id; junk name hidden
    assert any(f['cat'] == 'NGC 5194' for f in meta['famous'])                                          # matched by position
    sid, sp = universe.parse_sesame('# NGC 4993\n%J 197.44871198 -23.38397612 = 13 09 47.691\n%I NGC 4993\n%I 2MASX J13094770-2323017\n')
    assert sid == '13094770-2323017' and abs(sp[0] - 197.4487) < 1e-3 and sp[1] < -23
    assert universe.clean_name('g1309477-232302') == '' and universe.clean_name('A0627-1719') == '' and universe.clean_name('ARP 244') == 'ARP 244'
    blob = (universe.SITE_DIR / 'galaxies.bin').read_bytes()
    assert blob[:4] == b'SLU1' and int.from_bytes(blob[4:8], 'little') == meta['n'] and len(blob) == 16 + meta['n'] * 24
    n = meta['n']
    xyz = np.frombuffer(blob, '<f4', 3 * n, 16).reshape(n, 3)
    env = np.frombuffer(blob, np.uint8, n, 16 + 12 * n)
    nmb = np.frombuffer(blob, np.uint8, n, 16 + 15 * n)
    coma = nmb == 150
    d = np.linalg.norm(xyz[coma], axis=1)
    assert coma.sum() >= 150 and np.ptp(d) < 1.0 and abs(d.mean() - 104.3) < 0.5, (np.ptp(d), d.mean())   # finger of God collapsed
    assert (env[coma] == 3).mean() > 0.9, np.bincount(env[coma])                                       # a knot
    fil = np.abs(np.linalg.norm(xyz[:, 1:] - [60, 10], axis=1)) < 3
    fil &= np.abs(xyz[:, 0]) < 80
    assert np.isin(env[fil], [2, 3]).mean() > 0.7, np.bincount(env[fil], minlength=5)                 # the filament
    txt = (universe.SITE_DIR / 'galaxies.txt').read_text().splitlines()
    assert len(txt) == n and any(l.endswith('\tNGC 4889') for l in txt)
    assert any(f['name'] == 'NGC 4889' and txt[f['i']].endswith('NGC 4889') for f in meta['famous']), meta['famous']
    coma_mark = next(m for m in meta['landmarks'] if m['name'] == 'Coma Cluster')
    assert coma_mark['measured'] and abs(coma_mark['dist'] - 104.3) < 0.5 and '150 galaxies' in coma_mark['note'], coma_mark
    assert not next(m for m in meta['landmarks'] if m['name'] == 'Virgo Cluster')['measured']
    assert meta['messier']['M87'] == 'NGC 4486' and len(meta['eq_to_sg']) == 3
    assert universe.clean_name('NGC_0253') == 'NGC 253' and universe.clean_name('MESSIER_087') == 'NGC 4486' and universe.clean_name('2MASXJ123') == ''
    assert universe.morph(' -5') == 1 and universe.morph(' 3A2s') == 3 and universe.morph(' 98') == 0 and universe.morph('10') == 5
    from spacelab import db, publish
    con = db.connect()
    db.kv_set(con, 'universe', meta)
    data, files = publish.build_data(con, {})
    uv = data['beyond']['universe']
    assert uv['bin'] == 'files/plots/universe/galaxies.bin' and uv['txt'] == 'files/plots/universe/galaxies.txt' and uv['n'] == n, uv.keys()
    assert {'plots/universe/galaxies.bin', 'plots/universe/galaxies.txt'} <= set(files) and not publish.validate(data)
    assert 'universe.js' in publish.SITE_FILES


import urllib.error  # noqa: E402

if __name__ == '__main__':
    ok = sum(1 for _, g, _ in RES if g)
    for name, good, err in RES:
        print(('PASS ' if good else 'FAIL ') + name + ('' if good else '\n   ' + err.replace('\n', '\n   ')))
    print(f'\n{ok}/{len(RES)} passed')
    sys.exit(0 if ok == len(RES) else 1)
