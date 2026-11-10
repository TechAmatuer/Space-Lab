"""The local universe in 3D: about 43,500 galaxies of the 2MASS Redshift Survey placed where they are, for the public page.

Built once a month (the catalogues do not change), from two public catalogues:
  * 2MRS (Huchra et al. 2012, ApJS 199, 26): position, brightness, morphology and redshift of every galaxy with Ks < 11.75
    over 91% of the sky. The survey's own input name (NGC, UGC, ESO...) is kept so people can search for it.
  * The 2MASS group catalogue (Tully 2015, AJ 149, 171): which group each galaxy belongs to. Placing every member at its
    group's mean velocity removes the "Fingers of God" (clusters stretched along the line of sight by their galaxies'
    orbital motions).
A redshift only gives a distance once the galaxy's own motion is taken out, so the velocity is corrected first:
  * near home, with the flow model of Mould et al. 2000 (ApJ 529, 786; the one NED uses): the Local Group frame plus the
    infall of us and of the galaxy towards the Virgo Cluster, the Great Attractor and the Shapley Supercluster. Without it
    the nearest galaxies come out two to three times too far (they share our own 600 km/s motion);
  * far away (beyond about 100 Mpc) the velocity against the cosmic microwave background is used, which is the right frame
    there; in between the two are blended.
Distance = comoving distance for that velocity (flat universe, H0 = 70 km/s/Mpc, matter 0.3).
A redshift distance is still off by a galaxy's own motion (about 250 km/s, 3.6 Mpc), which is a lot near home, and a galaxy
with almost no velocity (the Local Group) cannot be placed by redshift at all. So a third catalogue is used where it is
better than that:
  * Cosmicflows-4 (Tully et al. 2023, ApJ 944, 94): distances measured with red-giant stars, Cepheids, masers, surface
    brightness fluctuations and supernovae. A galaxy within 45 Mpc whose measured distance is more precise than its
    redshift distance is placed at the measured one, and so is the rest of its group when enough members are measured.
Each galaxy's card says which kind of distance it shows.

Each galaxy also gets its cosmic environment - void, sheet, filament or knot (cluster) - from the tidal field of the
smoothed galaxy density (the "T-web" method, Hahn et al. 2007; Forero-Romero et al. 2009), with each galaxy weighted for
the survey seeing fewer faint galaxies far away.
"""
import json
import math
import struct
import time

import numpy as np

from . import config, db, net

MIRRORS = ('https://vizier.cfa.harvard.edu', 'https://vizier.cds.unistra.fr')
H0 = 70.0
OMEGA_M = 0.3
C_KMS = 299792.458
V_MIN = 150.0                                  # km/s: slower than this, a redshift says nothing about distance (Local Group)
# Flow model of Mould et al. 2000, Table A1: attractor, RA, Dec (J2000; the paper lists B1950), its velocity in the Local
# Group frame and the infall of the Local Group towards it (km/s). Density profile r^-2 as in the paper.
ATTRACTORS = (('Virgo Cluster', 187.712, 12.391, 957.0, 200.0),
              ('Great Attractor', 200.735, -44.261, 4380.0, 400.0),
              ('Shapley Supercluster', 203.206, -31.257, 13600.0, 85.0))
VIRGO_CONE = (10.0, -600.0, 2300.0)            # the paper's mask: radius (deg) and Local Group velocity range of the cluster
FLOW_FULL, FLOW_NONE = 3500.0, 7000.0          # km/s: flow model alone below, CMB frame alone above, blended in between
SIGMA_Z = 250.0 / H0                           # Mpc: typical error of a redshift distance (a galaxy's own motion)
MEAS_MAX_ERR, MEAS_MAX_DIST = 0.20, 45.0       # measured distances used: error in distance modulus (mag), and how far (Mpc)
METHODS = ('DMtrgb', 'DMceph', 'DMmas', 'DMsbf', 'DMsnIa', 'DMsnII', 'DMtf', 'DMfp')      # bit order of the method mask
HOW = ('redshift', 'group redshift', 'measured', 'group measured')
SITE_DIR = config.PLOTS / 'universe'
ENV = ['void', 'sheet', 'filament', 'knot', 'unknown']

# Messier galaxies -> NGC (for search and names)
MESSIER = {31: 224, 32: 221, 33: 598, 49: 4472, 51: 5194, 58: 4579, 59: 4621, 60: 4649, 61: 4303, 63: 5055, 64: 4826,
           65: 3623, 66: 3627, 74: 628, 77: 1068, 81: 3031, 82: 3034, 83: 5236, 84: 4374, 85: 4382, 86: 4406, 87: 4486,
           88: 4501, 89: 4552, 90: 4569, 91: 4548, 94: 4736, 95: 3351, 96: 3368, 98: 4192, 99: 4254, 100: 4321, 101: 5457,
           102: 5866, 104: 4594, 105: 3379, 106: 4258, 108: 3556, 109: 3992, 110: 205}
# proper names: catalogue name -> (name, one line)
FAMOUS = {
    'NGC 224': ('Andromeda Galaxy', 'Our big neighbour, 2.5 million light-years away. It is coming towards us.'),
    'NGC 598': ('Triangulum Galaxy', 'Third-largest galaxy of the Local Group. Also coming towards us.'),
    'NGC 5194': ('Whirlpool Galaxy', 'A face-on spiral pulling on its smaller companion, NGC 5195.'),
    'NGC 4594': ('Sombrero Galaxy', 'Seen almost edge-on, with a dark lane of dust like a hat brim.'),
    'NGC 4486': ('Virgo A (M87)', 'The giant galaxy at the heart of the Virgo Cluster; its black hole was the first ever imaged (2019).'),
    'NGC 3034': ('Cigar Galaxy', 'A galaxy forming stars so fast that winds blow out of it.'),
    'NGC 3031': ("Bode's Galaxy", 'A bright grand-design spiral in Ursa Major.'),
    'NGC 5457': ('Pinwheel Galaxy', 'A huge face-on spiral near the Big Dipper.'),
    'NGC 5055': ('Sunflower Galaxy', 'A spiral with short, patchy arms.'),
    'NGC 4826': ('Black Eye Galaxy', 'A dark band of dust in front of its bright centre.'),
    'NGC 5236': ('Southern Pinwheel', 'One of the closest and brightest barred spirals.'),
    'NGC 1068': ('Cetus A (M77)', 'A famous active galaxy with a hidden, hungry black hole.'),
    'NGC 4258': ('M106', 'Its water masers gave one of the most precise distances to any galaxy.'),
    'NGC 4472': ('M49', 'The brightest galaxy in the Virgo Cluster.'),
    'NGC 4736': ('Cat\'s Eye Galaxy (M94)', 'A spiral with a bright ring of young stars.'),
    'NGC 3627': ('M66 (Leo Triplet)', 'One of three galaxies tugging on each other in Leo.'),
    'NGC 3623': ('M65 (Leo Triplet)', 'One of three galaxies tugging on each other in Leo.'),
    'NGC 3628': ('Hamburger Galaxy (Leo Triplet)', 'An edge-on spiral with a thick dust lane.'),
    'NGC 628': ('Phantom Galaxy (M74)', 'A perfect face-on spiral, a favourite of the Webb telescope.'),
    'NGC 4321': ('M100', 'A grand-design spiral in the Virgo Cluster.'),
    'NGC 5128': ('Centaurus A', 'A galaxy with a giant black hole firing jets, the result of a merger.'),
    'NGC 253': ('Sculptor Galaxy', 'A dusty spiral bursting with new stars.'),
    'NGC 1300': ('NGC 1300', 'A textbook barred spiral.'),
    'NGC 1365': ('Great Barred Spiral', 'One of the largest barred spirals known, in the Fornax Cluster.'),
    'NGC 4993': ('NGC 4993', 'Host of GW170817 (2017): the first neutron-star merger seen in gravitational waves and in light.'),
    'NGC 1275': ('Perseus A', 'The giant galaxy at the centre of the Perseus Cluster.'),
    'NGC 4889': ('NGC 4889', 'One of the two giant galaxies at the heart of the Coma Cluster.'),
    'NGC 4874': ('NGC 4874', 'One of the two giant galaxies at the heart of the Coma Cluster.'),
    'NGC 6240': ('NGC 6240', 'Two galaxies merging, with two black holes in the middle.'),
    'NGC 4038': ('Antennae Galaxies', 'Two colliding galaxies with long tails of stars.'),
    'NGC 4039': ('Antennae Galaxies', 'Two colliding galaxies with long tails of stars.'),
    'NGC 2683': ('UFO Galaxy', 'A spiral seen nearly edge-on.'),
    'NGC 891': ('NGC 891', 'An edge-on spiral that looks like our Milky Way from outside.'),
    'NGC 4565': ('Needle Galaxy', 'A thin, perfectly edge-on spiral.'),
    'NGC 7331': ('NGC 7331', 'Sometimes called the Milky Way\'s twin.'),
    'NGC 1316': ('Fornax A', 'A bright radio galaxy built from swallowed neighbours.'),
    'NGC 1399': ('NGC 1399', 'The giant elliptical at the centre of the Fornax Cluster.'),
    'NGC 3115': ('Spindle Galaxy', 'A lens-shaped galaxy with a billion-sun black hole.'),
    'NGC 5866': ('Spindle Galaxy (M102)', 'A lens-shaped galaxy seen edge-on.'),
    'NGC 2403': ('NGC 2403', 'A nearby spiral in Camelopardalis.'),
    'NGC 6946': ('Fireworks Galaxy', 'Ten supernovae seen in a century, more than any other galaxy.'),
    'NGC 4631': ('Whale Galaxy', 'An edge-on spiral shaped like a whale.'),
    'NGC 4656': ('Hockey Stick Galaxy', 'Bent by its neighbour, the Whale Galaxy.'),
    'NGC 4696': ('NGC 4696', 'The giant galaxy at the centre of the Centaurus Cluster.'),
    'NGC 6872': ('Condor Galaxy', 'One of the largest spiral galaxies known, stretched by a neighbour.'),
    'NGC 1097': ('NGC 1097', 'A barred spiral with a bright ring around its black hole.'),
    'NGC 7479': ('NGC 7479', 'A barred spiral with one arm much brighter than the other.'),
    'NGC 4374': ('M84', 'A big elliptical in the Virgo Cluster, part of Markarian\'s Chain.'),
    'NGC 4406': ('M86', 'An elliptical in the Virgo Cluster moving towards us through the cluster.'),
    'NGC 4649': ('M60', 'A giant elliptical in Virgo with a small spiral companion.'),
    'NGC 4382': ('M85', 'The northernmost bright galaxy of the Virgo Cluster.'),
    'NGC 4501': ('M88', 'A spiral in the Virgo Cluster.'),
    'NGC 4552': ('M89', 'A round elliptical in the Virgo Cluster.'),
    'NGC 4254': ('M99', 'A lopsided spiral in the Virgo Cluster.'),
    'NGC 3351': ('M95', 'A barred spiral with a ring of young stars.'),
    'NGC 3368': ('M96', 'A spiral in the Leo I group.'),
    'NGC 3379': ('M105', 'An elliptical in the Leo I group.'),
    'NGC 3556': ('Surfboard Galaxy (M108)', 'An edge-on spiral in Ursa Major.'),
    'NGC 3992': ('M109', 'A barred spiral in Ursa Major.'),
    'NGC 4303': ('M61', 'A spiral in Virgo with many supernovae.'),
    'NGC 2207': ('NGC 2207', 'A spiral colliding with IC 2163.'),
    'NGC 1052': ('NGC 1052', 'An elliptical in Cetus with an active centre.'),
    'NGC 4261': ('NGC 4261', 'An elliptical whose black hole is surrounded by a dark disk of dust.'),
    'NGC 3077': ('NGC 3077', 'A small galaxy disturbed by M81 and M82.'),
    'NGC 2976': ('NGC 2976', 'A small spiral in the M81 group.'),
}
BLUESHIFT_NOTE = ('It is moving towards us, so its light is blueshifted, not redshifted. A redshift only gives a distance '
                  'when the expansion of the universe carries a galaxy away, and no measured distance precise enough is '
                  'in the catalogue used here, so the map cannot place it. Almost every galaxy like this is one of the '
                  'Milky Way\'s closest neighbours, right next to Home.')
NEAR_NOTE = ('The survey measures almost no speed away from us for it. So close to home a galaxy\'s own motion matters more '
             'than the expansion of the universe, so its redshift cannot give a distance, and no measured distance precise '
             'enough is in the catalogue used here. It is left out; it sits in or just outside the Local Group, right next to Home.')
# landmarks: clusters are snapped to the richest group of the 2MASS group catalogue near the given direction and velocity;
# the others are approximate centres from the literature, shown as such
LANDMARKS = [
    # name, kind, RA, Dec, CMB-frame velocity (km/s), note
    ('Virgo Cluster', 'cluster', 187.70, 12.39, 1500, 'The nearest big cluster, about 1,500 galaxies; the Milky Way falls towards it.'),
    ('Coma Cluster', 'cluster', 194.95, 27.98, 7300, 'A rich cluster where dark matter was first proposed (Zwicky, 1933).'),
    ('Perseus Cluster', 'cluster', 49.95, 41.51, 5100, 'The brightest cluster in X-rays, part of the Perseus-Pisces chain.'),
    ('Centaurus Cluster', 'cluster', 192.20, -41.31, 3600, 'A cluster in the Hydra-Centaurus supercluster.'),
    ('Hydra Cluster', 'cluster', 159.17, -27.53, 4000, 'A nearby cluster of about 160 bright galaxies.'),
    ('Norma Cluster', 'cluster', 243.89, -60.91, 4900, 'A massive cluster behind the Milky Way, near the Great Attractor.'),
    ('Fornax Cluster', 'cluster', 54.62, -35.45, 1300, 'The second-richest cluster within 20 Mpc.'),
    ('Leo Cluster', 'cluster', 176.15, 19.75, 6500, 'Abell 1367, joined to Coma by a wall of galaxies.'),
    ('Hercules Cluster', 'cluster', 241.15, 17.72, 11000, 'A cluster full of spiral galaxies in the Hercules supercluster.'),
    ('Shapley Supercluster', 'cluster', 201.98, -31.50, 14500, 'The most massive concentration of galaxies in the nearby universe.'),
    ('Ophiuchus Cluster', 'cluster', 258.11, -23.37, 8500, 'A massive cluster just above the Milky Way\'s plane.'),
    ('Antlia Cluster', 'cluster', 157.50, -35.30, 2800, 'A nearby cluster dominated by two giant ellipticals.'),
    ('Great Attractor', 'region', None, None, 4900, 'The heart of the Laniakea supercluster that our Milky Way flows towards.', (307.0, 9.0)),
    ('Perseus-Pisces Supercluster', 'region', 25.0, 33.0, 5200, 'A long chain of clusters and filaments.'),
    ('CfA2 Great Wall', 'region', 210.0, 30.0, 8000, 'A sheet of galaxies about 600 million light-years long, found in 1989.'),
    ('Horologium-Reticulum Supercluster', 'region', 52.0, -50.0, 19000, 'A huge supercluster in the southern sky.'),
    ('Sloan Great Wall', 'region', 195.0, 2.0, 22500, 'One of the largest structures known, near the edge of this map.'),
    ('Local Void', 'void', 285.0, 5.0, 1500, 'A big empty region right next to the Local Group; it pushes us away.'),
    ('Boötes Void', 'void', 222.5, 46.0, 15500, 'The "Great Nothing": a nearly empty region about 330 million light-years across.'),
]


def _asu(params, timeout=300):
    err = None
    for base in MIRRORS:
        try:
            return net.fetch_text(base + '/viz-bin/asu-tsv', params=params, timeout=timeout, retries=2)
        except Exception as e:
            err = e
    raise err


def _table(tsv):
    lines = [ln for ln in tsv.splitlines() if ln and not ln.startswith('#')]
    if len(lines) < 3:
        return []
    head = [h.strip() for h in lines[0].split('\t')]
    return [dict(zip(head, [v.strip() for v in ln.split('\t')])) for ln in lines[3:] if not ln.startswith('-')]


def _f(x):
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


import re
JUNK = re.compile(r'^(g\d{6,7}[+-]\d{5,6}|A\d{4}[\d.]*[+-]\d{4}[\d.]*|\d{8}[+-]\d{6,7}|\d{4}[+-]\d{3,4}\S*)$')
SESAME = 'https://vizier.cfa.harvard.edu/viz-bin/nph-sesame/-oI/S'


def clean_name(cat):
    """'NGC_0253' -> 'NGC 253'; 'MESSIER_031' -> 'NGC 224'; 2MASS-style, coordinate-only or empty names -> ''."""
    c = (cat or '').strip().replace('_', ' ')
    if not c or c.upper().startswith(('2MASX', '2MASS', 'XSC', 'LGA')) or JUNK.match(c):
        return ''
    parts = c.split()
    if parts[0].upper() == 'MESSIER' and len(parts) > 1 and parts[1].isdigit():
        n = int(parts[1])
        return f'NGC {MESSIER[n]}' if n in MESSIER else f'M{n}'
    if len(parts) == 2 and parts[1].isdigit():
        return f'{parts[0].upper()} {int(parts[1])}'
    return ' '.join(parts)


def morph(code):
    """ZCAT / de Vaucouleurs T type -> 0 unknown, 1 elliptical, 2 lenticular, 3 early spiral, 4 late spiral, 5 irregular."""
    import re
    m = re.match(r'\s*(-?\d{1,2})', str(code or ''))
    if not m:
        return 0
    t = int(m.group(1))
    if t > 20:
        return 0
    return 1 if t <= -4 else 2 if t <= 0 else 3 if t <= 3 else 4 if t <= 8 else 5


def cmb_velocity(cz, l_deg, b_deg):
    """Heliocentric velocity -> CMB frame (dipole: 369.8 km/s towards l=264.14, b=48.26)."""
    l, b = np.radians(l_deg), np.radians(b_deg)
    la, ba = math.radians(264.14), math.radians(48.26)
    cosang = np.sin(b) * math.sin(ba) + np.cos(b) * math.cos(ba) * np.cos(l - la)
    return np.asarray(cz, float) + 369.8 * cosang


def unit(ra, dec):
    r, d = np.radians(ra), np.radians(dec)
    return np.column_stack([np.cos(d) * np.cos(r), np.cos(d) * np.sin(r), np.sin(d)])


# ------------------------------------------------------------------ from redshift to distance
def lg_velocity(cz, l_deg, b_deg):
    """Heliocentric velocity -> Local Group frame (Mould et al. 2000, eq. A1)."""
    l, b = np.radians(l_deg), np.radians(b_deg)
    return np.asarray(cz, float) - 79.0 * np.cos(l) * np.cos(b) + 296.0 * np.sin(l) * np.cos(b) - 36.0 * np.sin(b)


def flow_velocity(v_lg, ra, dec):
    """Local Group velocity -> velocity of the expansion alone, with the infall model of Mould et al. 2000 (eq. 1, density
    r^-2): for each attractor, add our own infall along the line of sight and take out the object's.

    One change from the paper: its infall speed grows without limit towards an attractor's centre, which the paper masks
    with a cone. Here it is softened inside the attractor's turn-around radius (where infall equals expansion) and scaled
    to stay exact at the Local Group, so an object at home still shares our motion and gets no correction."""
    n = unit(np.atleast_1d(ra), np.atleast_1d(dec))
    v_o = np.atleast_1d(np.asarray(v_lg, float))
    out = v_o.copy()
    for _, a_ra, a_dec, v_a, v_fid in ATTRACTORS:
        cos_t = n @ unit([a_ra], [a_dec])[0]
        r2 = v_o ** 2 + v_a ** 2 - 2.0 * v_o * v_a * cos_t           # (distance object - attractor)^2, as velocities
        rc2 = v_fid * v_a                                            # turn-around radius^2
        out += v_fid * cos_t + v_fid * (v_o - v_a * cos_t) * (v_a ** 2 + rc2) / (v_a * (r2 + rc2))
    return out


def cosmic_velocity(cz, l_deg, b_deg, ra, dec):
    """Heliocentric velocity -> the part due to the expansion: the flow model near home, the CMB frame far away."""
    v_lg = lg_velocity(cz, l_deg, b_deg)
    w = np.clip((FLOW_NONE - v_lg) / (FLOW_NONE - FLOW_FULL), 0.0, 1.0)
    w = w * w * (3.0 - 2.0 * w)                                      # smooth step
    return w * flow_velocity(v_lg, ra, dec) + (1.0 - w) * cmb_velocity(cz, l_deg, b_deg)


def comoving_mpc(v):
    """Comoving distance (Mpc) for a cosmic velocity cz, flat universe with H0 and OMEGA_M."""
    zz = np.linspace(0.0, 0.6, 6001)
    e = 1.0 / np.sqrt(OMEGA_M * (1.0 + zz) ** 3 + 1.0 - OMEGA_M)
    d = np.concatenate([[0.0], np.cumsum(0.5 * (e[1:] + e[:-1]) * np.diff(zz))]) * C_KMS / H0
    return np.interp(np.asarray(v, float) / C_KMS, zz, d)


def eq_to_gal(ra, dec):
    """(l, b) in degrees for J2000 positions."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    g = SkyCoord(ra=np.atleast_1d(ra) * u.deg, dec=np.atleast_1d(dec) * u.deg).galactic
    return np.asarray(g.l.deg), np.asarray(g.b.deg)


def match_sky(ra1, dec1, ra2, dec2, tol_arcsec=6.0):
    """Index into catalogue 2 for each object of catalogue 1 (or -1), by position."""
    from scipy.spatial import cKDTree
    t = cKDTree(unit(ra2, dec2))
    d, i = t.query(unit(ra1, dec1), distance_upper_bound=2 * math.sin(math.radians(tol_arcsec / 3600) / 2))
    return np.where(np.isfinite(d), i, -1)


def parse_sesame(text):
    """(2MASS XSC id or None, (ra, dec) or None) from a Sesame -oI answer."""
    m = re.search(r'^%I 2MASX J(\d{8}[+-]\d{7})', text or '', re.M)
    p = re.search(r'^%J\s+([\d.]+)\s+([+-]?[\d.]+)', text or '', re.M)
    return (m.group(1) if m else None), ((float(p.group(1)), float(p.group(2))) if p else None)


def resolve_famous(log, cache_days=180):
    """2MASS id / position of each famous galaxy, by name (SIMBAD through the Harvard Sesame mirror), cached."""
    path = config.CACHE / 'universe_famous.json'
    try:
        old = json.loads(path.read_text()) if path.is_file() and time.time() - path.stat().st_mtime < cache_days * 86400 else {}
    except Exception:
        old = {}
    out = {}
    for name in FAMOUS:
        if name in old:
            out[name] = old[name]
            continue
        try:
            i, p = parse_sesame(net.fetch_text(SESAME + '?' + name.replace(' ', '%20'), timeout=30, retries=2, min_gap=0.5))
            out[name] = {'id': i, 'pos': p}
        except Exception as e:
            log(f'  name lookup {name}: {e}')
    if out:
        config.CACHE.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(out))
    return out


# ------------------------------------------------------------------ cosmic web (T-web)
def selection_weights(r, rmin=10.0, rmax=230.0):
    """1 / (relative number density of this flux-limited survey at distance r), from the data itself, capped."""
    edges = np.linspace(0, rmax + 40, 60)
    cnt, _ = np.histogram(r, edges)
    mid = 0.5 * (edges[1:] + edges[:-1])
    vol = 4 / 3 * np.pi * (edges[1:] ** 3 - edges[:-1] ** 3)
    n = cnt / vol
    ok = (mid > rmin) & (mid < rmax) & (cnt > 20)
    if ok.sum() < 5:
        return np.ones_like(r)
    coef = np.polyfit(mid[ok], np.log(n[ok]), 3)
    ref = np.exp(np.polyval(coef, 30.0))
    w = ref / np.exp(np.polyval(coef, np.clip(r, rmin, rmax)))
    return np.clip(w, 0.2, 40.0)


def tweb(xyz, weights, half=260.0, n=128, smooth=5.0, lam=0.7):
    """Environment of each point: number of tidal-tensor eigenvalues above lam (0 void, 1 sheet, 2 filament, 3 knot);
    4 when outside the grid. Also returns the smoothed density contrast at each point.
    lam = 0.7 on the galaxy density is about 0.45 on the matter density, as 2MRS galaxies cluster ~1.5x more strongly
    than matter; on the real catalogue it gives ~7% void, 25% sheet, 44% filament, 25% knot (within 230 Mpc)."""
    cell = 2 * half / n
    idx = np.floor((xyz + half) / cell).astype(int)
    inside = np.all((idx >= 0) & (idx < n), axis=1)
    rho = np.zeros((n, n, n))
    np.add.at(rho, tuple(idx[inside].T), weights[inside])
    rr = np.linalg.norm((np.indices((n, n, n)).reshape(3, -1).T + 0.5) * cell - half, axis=1).reshape(n, n, n)
    shell = (rr > 15) & (rr < 200)
    mean = rho[shell].mean() if shell.any() else rho.mean()
    delta = rho / mean - 1.0
    k = np.fft.fftfreq(n, d=cell) * 2 * np.pi
    kz = np.fft.rfftfreq(n, d=cell) * 2 * np.pi
    KX, KY, KZ = np.meshgrid(k, k, kz, indexing='ij')
    k2 = KX ** 2 + KY ** 2 + KZ ** 2
    dk = np.fft.rfftn(delta) * np.exp(-0.5 * k2 * smooth ** 2)
    k2[0, 0, 0] = 1.0
    base = dk / k2
    base[0, 0, 0] = 0
    comps = {}
    K = (KX, KY, KZ)
    for a in range(3):
        for b in range(a, 3):
            comps[a, b] = np.fft.irfftn(base * K[a] * K[b], s=(n, n, n), axes=(0, 1, 2))
    dsm = np.fft.irfftn(dk, s=(n, n, n), axes=(0, 1, 2))
    env = np.full(len(xyz), 4, np.uint8)
    dens = np.full(len(xyz), np.nan)
    ii = tuple(idx[inside].T)
    T = np.empty((inside.sum(), 3, 3))
    for (a, b), arr in comps.items():
        T[:, a, b] = T[:, b, a] = arr[ii]
    ev = np.linalg.eigvalsh(T)
    env[inside] = (ev > lam).sum(1).astype(np.uint8)
    dens[inside] = dsm[ii]
    return env, dens


# ------------------------------------------------------------------ landmarks
def place_landmarks(groups, log=print):
    """Landmark positions in Mpc (equatorial x, y, z). Clusters snap to the richest nearby 2MASS group."""
    from astropy.coordinates import SkyCoord
    import astropy.units as u
    out = []
    g_ra, g_de = groups['ra'], groups['dec']
    for row in LANDMARKS:
        name, kind, ra, dec, v, note = row[:6]
        if ra is None:
            c = SkyCoord(l=row[6][0] * u.deg, b=row[6][1] * u.deg, frame='galactic').icrs
            ra, dec = float(c.ra.deg), float(c.dec.deg)
        snapped = False
        if kind == 'cluster' and len(g_ra):
            sep = np.degrees(np.arccos(np.clip(unit([ra], [dec]) @ unit(g_ra, g_de).T, -1, 1)))[0]
            ok = (sep < 2.0) & (np.abs(groups['v'] - v) < 1200) & (groups['n'] >= 5)
            if ok.any():
                j = np.flatnonzero(ok)[np.argmax(groups['n'][ok])]
                ra, dec, d = float(g_ra[j]), float(g_de[j]), float(groups['dist'][j])
                snapped = True
                note = f"{note} {int(groups['n'][j])} galaxies of this catalogue are members."
        if not snapped:
            d = float(comoving_mpc(v))
        out.append({'name': name, 'kind': kind, 'xyz': [round(float(x), 2) for x in unit([ra], [dec])[0] * d],
                    'ra': round(ra, 3), 'dec': round(dec, 3), 'dist': round(d, 1), 'note': note, 'measured': snapped})
        if kind == 'cluster' and not snapped:
            log(f'  landmark {name}: no matching group found; placed at the literature velocity')
    return out


# ------------------------------------------------------------------ the job
def measured_distances(cf4, pgc, ra, dec):
    """Per galaxy: its measured distance in Mpc (nan when there is none, or the redshift does better) and the bit mask of
    the methods behind it (METHODS). cf4: rows of the Cosmicflows-4 table of individual galaxies."""
    d = np.full(len(ra), np.nan)
    meth = np.zeros(len(ra), np.uint8)
    if not cf4:
        return d, meth
    c_pgc = np.array([int(_f(r.get('PGC')) or 0) for r in cf4])
    c_dm = np.array([_f(r.get('DM')) or np.nan for r in cf4], float)
    c_e = np.array([_f(r.get('e_DM')) or np.nan for r in cf4], float)
    c_ra = np.array([_f(r.get('RAJ2000')) if _f(r.get('RAJ2000')) is not None else np.nan for r in cf4], float)
    c_de = np.array([_f(r.get('DEJ2000')) if _f(r.get('DEJ2000')) is not None else np.nan for r in cf4], float)
    c_m = np.array([sum(1 << k for k, m in enumerate(METHODS) if _f(r.get(m)) is not None) for r in cf4], np.uint8)
    c_d = 10 ** ((c_dm - 25.0) / 5.0)
    with np.errstate(invalid='ignore'):
        good = np.isfinite(c_d) & (c_e > 0) & (c_e <= MEAS_MAX_ERR) & (c_d <= MEAS_MAX_DIST) & (0.4605 * c_e * c_d < SIGMA_Z)
    by = {int(q): k for k, q in enumerate(c_pgc) if q and good[k]}
    idx = np.array([by.get(int(q), -1) if q else -2 for q in pgc])       # -2: no PGC number known, try the position
    need = np.flatnonzero(idx == -2)
    g = np.flatnonzero(good & np.isfinite(c_ra) & np.isfinite(c_de))
    if len(need) and len(g):
        j = match_sky(ra[need], dec[need], c_ra[g], c_de[g], 20.0)
        idx[need] = np.where(j >= 0, g[np.clip(j, 0, len(g) - 1)], -1)
    has = idx >= 0
    d[has], meth[has] = c_d[idx[has]], c_m[idx[has]]
    return d, meth


def build(log, sources=None, famous_lookup=None):
    """Download, combine and write the files. sources / famous_lookup: optional pre-loaded tables (for tests)."""
    if sources is None:
        log('  downloading the 2MASS Redshift Survey (43,500 galaxies)...')
        twomrs = _table(_asu({'-source': 'J/ApJS/199/26/table3', '-out': 'ID,RAJ2000,DEJ2000,GLON,GLAT,Ktmag,type,cz,CAT',
                              '-out.max': '60000'}, timeout=600))
        log(f'    {len(twomrs)} rows; now the 2MASS group catalogue...')
        tully = _table(_asu({'-source': 'J/AJ/149/171/table5', '-out': 'PGC,GLON,GLAT,MType,Vcmba,Nest,Nmb,Dist,_RA.icrs,_DE.icrs',
                             '-out.max': '60000'}, timeout=600))
        log(f'    {len(tully)} galaxies with group tags; now the measured distances (Cosmicflows-4)...')
        try:
            cf4 = _table(_asu({'-source': 'J/ApJ/944/94/table2', '-out': 'PGC,DM,e_DM,' + ','.join(METHODS) + ',RAJ2000,DEJ2000',
                               '-out.max': '80000'}, timeout=600))
            log(f'    {len(cf4)} galaxies with a measured distance')
        except Exception as e:
            cf4 = []
            log(f'    measured distances not available this time ({e}); every galaxy is placed by redshift')
    else:
        twomrs, tully, cf4 = sources
    ra = np.array([_f(r.get('RAJ2000')) for r in twomrs], float)
    dec = np.array([_f(r.get('DEJ2000')) for r in twomrs], float)
    l = np.array([_f(r.get('GLON')) for r in twomrs], float)
    b = np.array([_f(r.get('GLAT')) for r in twomrs], float)
    cz = np.array([_f(r.get('cz')) if _f(r.get('cz')) is not None else np.nan for r in twomrs], float)
    kt = np.array([_f(r.get('Ktmag')) or 11.75 for r in twomrs], float)
    names = [clean_name(r.get('CAT')) for r in twomrs]
    ids = [r.get('ID', '').strip() for r in twomrs]
    t_ra = np.array([_f(r.get('_RA.icrs')) for r in tully], float)
    t_de = np.array([_f(r.get('_DE.icrs')) for r in tully], float)
    j = match_sky(ra, dec, t_ra, t_de) if len(tully) else np.full(len(ra), -1)
    have = j >= 0
    log(f'  {have.sum()} of {len(ra)} galaxies matched to the group catalogue')
    nest = np.zeros(len(ra), np.uint32)
    nmb = np.zeros(len(ra), np.uint16)
    pgc = np.zeros(len(ra), np.int64)
    mt = np.array([morph(r.get('type')) for r in twomrs], np.uint8)
    for i in np.flatnonzero(have):
        t = tully[j[i]]
        nest[i] = int(_f(t.get('Nest')) or 0)
        nmb[i] = int(_f(t.get('Nmb')) or 0)
        pgc[i] = int(_f(t.get('PGC')) or 0)
        if not mt[i]:
            mt[i] = morph(t.get('MType'))
    ok = np.isfinite(cz)
    u3 = unit(ra, dec)
    # --- redshift distances. Every member of a group takes the group's mean velocity and direction (no Fingers of God)
    v_cos = np.full(len(ra), np.nan)
    v_cos[ok] = cosmic_velocity(cz[ok], l[ok], b[ok], ra[ok], dec[ok])
    sel = np.flatnonzero((nest > 0) & ok)
    keys, inv, cnt = (np.unique(nest[sel], return_inverse=True, return_counts=True) if len(sel)
                      else (np.zeros(0, np.uint32), np.zeros(0, int), np.zeros(0, int)))
    g_cz = np.bincount(inv, cz[sel], len(keys)) / np.maximum(cnt, 1)
    c = np.column_stack([np.bincount(inv, u3[sel, k], len(keys)) for k in range(3)]) if len(keys) else np.zeros((0, 3))
    c = c / np.maximum(np.linalg.norm(c, axis=1), 1e-12)[:, None]
    g_ra = np.degrees(np.arctan2(c[:, 1], c[:, 0])) % 360.0
    g_de = np.degrees(np.arcsin(np.clip(c[:, 2], -1, 1)))
    g_l, g_b = eq_to_gal(g_ra, g_de) if len(keys) else (np.zeros(0), np.zeros(0))
    g_v = cosmic_velocity(g_cz, g_l, g_b, g_ra, g_de) if len(keys) else np.zeros(0)
    # the Virgo Cluster is the flow model's first attractor: its members sit at the model's own Virgo distance (the paper's mask)
    v_ra, v_de, v_a = ATTRACTORS[0][1:4]
    if len(keys):
        sep = np.degrees(np.arccos(np.clip(c @ unit([v_ra], [v_de])[0], -1, 1)))
        g_lg = lg_velocity(g_cz, g_l, g_b)
        cand = (sep < VIRGO_CONE[0]) & (g_lg > VIRGO_CONE[1]) & (g_lg < VIRGO_CONE[2]) & (cnt >= 20)
        if cand.any():
            k = np.flatnonzero(cand)[np.argmax(cnt[cand])]
            g_v[k] = flow_velocity([v_a], [v_ra], [v_de])[0]
    multi = cnt[inv] >= 2 if len(sel) else np.zeros(0, bool)
    v_cos[sel[multi]] = g_v[inv[multi]]
    how = np.zeros(len(ra), np.uint8)
    how[sel[multi]] = 1
    with np.errstate(invalid='ignore'):
        dist = np.where(np.isfinite(v_cos) & (v_cos >= V_MIN), comoving_mpc(np.nan_to_num(v_cos)), np.nan)
    g_dist = comoving_mpc(g_v) if len(keys) else np.zeros(0)
    # --- measured distances, where they do better than the redshift
    d_meas, meth = measured_distances(cf4, pgc, ra, dec)
    own = np.isfinite(d_meas)
    n_grp = 0
    if own.any() and len(sel):
        m_in = own[sel]
        n_meas = np.bincount(inv[m_in], minlength=len(keys))
        for k in np.flatnonzero((n_meas >= 5) | ((n_meas >= 1) & (2 * n_meas >= cnt) & (cnt >= 2))):
            mem = sel[inv == k]
            med = float(np.median(d_meas[mem][own[mem]]))
            g_dist[k] = med
            rest = mem[~own[mem]]
            dist[rest], how[rest] = med, 3
            n_grp += len(rest)
    dist[own], how[own] = d_meas[own], 2
    meth[~own] = 0
    placed = np.isfinite(dist) & (dist > 0)
    left = ok & ~placed
    log(f'  {placed.sum()} galaxies placed: {int(np.sum(placed & (how == 2)))} at their own measured distance, {n_grp} at their '
        f"group's measured distance, the rest by redshift; {int(left.sum())} too close to home for a redshift distance are left out")
    xyz = u3 * np.nan_to_num(dist)[:, None]
    w = selection_weights(dist[placed])
    env = np.full(len(ra), 4, np.uint8)
    dens = np.full(len(ra), np.nan)
    env[placed], dens[placed] = tweb(xyz[placed], w)
    far = placed & (dist > 230)
    env[far] = 4                                             # too few galaxies that far to tell
    counts = {ENV[k]: int(np.sum(env[placed] == k)) for k in range(5)}
    log(f"  cosmic web: {counts}")
    gr = {'ra': g_ra, 'dec': g_de, 'v': cmb_velocity(g_cz, g_l, g_b) if len(keys) else np.zeros(0), 'n': cnt.astype(float), 'dist': g_dist}
    marks = place_landmarks(gr, log)
    # files: binary arrays for the viewer and a text index for names and search
    keep = np.flatnonzero(placed)
    N = len(keep)
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    kbyte = np.clip(np.round((kt[keep] - 3.0) * 20), 0, 255).astype(np.uint8)
    blob = b'SLU1' + struct.pack('<I', N) + b'\0' * 8
    blob += xyz[keep].astype('<f4').tobytes()
    blob += env[keep].astype(np.uint8).tobytes() + kbyte.tobytes() + mt[keep].tobytes() + np.minimum(nmb[keep], 255).astype(np.uint8).tobytes()
    blob += np.nan_to_num(cz[keep]).astype('<i4').tobytes() + nest[keep].astype('<u4').tobytes()
    blob += how[keep].tobytes() + meth[keep].tobytes()       # added later, at the end: older pages simply do not read them
    (SITE_DIR / 'galaxies.bin').write_bytes(blob)
    lines = [f'{ids[i]}\t{names[i]}' for i in keep]
    (SITE_DIR / 'galaxies.txt').write_text('\n'.join(lines) + '\n')
    pos = {ids[i]: k for k, i in enumerate(keep)}
    by_name = {}
    for k, i in enumerate(keep):
        if names[i]:
            by_name.setdefault(names[i], k)
    look = famous_lookup if famous_lookup is not None else (resolve_famous(log) if sources is None else {})
    famous, not_placed = [], []
    kra, kde = ra[keep], dec[keep]
    for n, (nm, note) in FAMOUS.items():
        k = by_name.get(n)
        hit = look.get(n) or {}
        if k is None and hit.get('id') in pos:
            k = pos[hit['id']]
        if k is None and hit.get('pos'):                     # a big galaxy's catalogue centre can sit well off the survey's
            j2 = match_sky([hit['pos'][0]], [hit['pos'][1]], kra, kde, 60.0)[0]
            k = int(j2) if j2 >= 0 else None
        if k is not None:
            prev = next((f for f in famous if f['i'] == int(k)), None)
            if prev:                                         # two catalogue names, one survey entry (the Antennae)
                prev.setdefault('also', []).append(n)
            else:
                famous.append({'i': int(k), 'name': nm, 'cat': n, 'note': note})
            continue
        i0 = ids.index(hit['id']) if hit.get('id') in ids else next((i for i, x in enumerate(names) if x == n), None)
        if i0 is not None and not placed[i0]:
            not_placed.append({'name': nm, 'cat': n, 'id': ids[i0], 'cz': int(np.nan_to_num(cz[i0])),
                               'why': 'blue' if np.nan_to_num(cz[i0], nan=1) <= 0 else 'near'})
    seen = {x['id'] for x in not_placed}
    blue = not_placed + [{'name': names[i] or ids[i], 'cat': names[i], 'id': ids[i], 'cz': int(cz[i]), 'why': 'blue' if cz[i] <= 0 else 'near'}
                         for i in np.flatnonzero(left) if ids[i] not in seen]
    n_names = len(famous) + sum(len(f.get('also', [])) for f in famous)
    log(f'  {n_names} of {len(FAMOUS)} famous galaxies found; {len(not_placed)} famous ones cannot be placed')
    for want in ('NGC 224', 'NGC 3031', 'NGC 5128', 'NGC 4594', 'NGC 4258', 'NGC 4486', 'NGC 1399', 'NGC 4993', 'NGC 1275', 'NGC 4889'):
        f = next((x for x in famous if x['cat'] == want), None)
        if f:
            i = keep[f['i']]
            log(f"    check {f['name']}: {dist[i]:.1f} Mpc ({HOW[how[i]]}); its redshift alone gives "
                f"{float(comoving_mpc(max(v_cos[i], 0))) if np.isfinite(v_cos[i]) else float('nan'):.1f} Mpc")
    from astropy.coordinates import SkyCoord, Supergalactic
    sg = SkyCoord(x=np.eye(3)[:, 0], y=np.eye(3)[:, 1], z=np.eye(3)[:, 2], representation_type='cartesian', frame='icrs')
    m = np.array(sg.transform_to(Supergalactic()).cartesian.xyz).T          # rows: equatorial axes in supergalactic coords
    virgo = next((x for x in marks if x['name'] == 'Virgo Cluster'), None)
    meta = {'n': N, 'made': time.time(), 'h0': H0, 'omega_m': OMEGA_M, 'bin': 'plots/universe/galaxies.bin', 'txt': 'plots/universe/galaxies.txt',
            'env_counts': counts, 'landmarks': marks, 'famous': famous, 'blueshift': blue[:60], 'blueshift_note': BLUESHIFT_NOTE,
            'near_note': NEAR_NOTE,
            'messier': {f'M{k}': f'NGC {v}' for k, v in MESSIER.items()}, 'eq_to_sg': [[round(float(x), 6) for x in r] for r in m.T],
            'max_dist': round(float(np.percentile(dist[placed], 99.5)), 0), 'n_in_groups': int(np.sum(nmb[keep] >= 2)),
            'n_measured': int(np.sum(placed & (how == 2))), 'n_group_measured': int(n_grp), 'n_left_out': int(left.sum()),
            'virgo_dist': virgo['dist'] if virgo and virgo.get('measured') else None,
            'method': 'Redshift corrected for our own motion and for infall towards Virgo, the Great Attractor and Shapley '
                      f'(Mould et al. 2000) below {FLOW_FULL:g} km/s, CMB frame above {FLOW_NONE:g} km/s, blended between; group '
                      f'members share the group mean velocity (Tully 2015); comoving distance with H0 = {H0:g}, matter {OMEGA_M:g}. '
                      f'Cosmicflows-4 measured distances replace it within {MEAS_MAX_DIST:g} Mpc when more precise. '
                      'Environment: T-web of the selection-weighted density, 5 Mpc smoothing, eigenvalue threshold 0.7 on '
                      'galaxy density (about 0.45 on matter).'}
    return meta, pos


def run(ctx):
    con, log = ctx.con, ctx.log
    meta, _ = build(log)
    db.kv_set(con, 'universe', meta)
    c = meta['env_counts']
    return (f"3D universe: {meta['n']:,} galaxies placed (99.5% within {meta['max_dist']:.0f} Mpc); "
            f"{c['knot']:,} in knots, {c['filament']:,} in filaments, {c['sheet']:,} on sheets, {c['void']:,} in voids")
