"""Paths and settings for Space Lab. User settings live in data/settings.json (edited from the Setup tab)."""
import copy
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ['SPACELAB_DATA']) if os.environ.get('SPACELAB_DATA') else ROOT / 'data'   # tests use a temp folder
LOGS = ROOT / 'logs'
INBOX = ROOT / 'inbox'
PLOTS = DATA / 'plots'
CACHE = DATA / 'cache'
DB_PATH = DATA / 'spacelab.db'
SETTINGS_PATH = DATA / 'settings.json'
EXO_INBOX = INBOX / 'exoplanet-watch'
EXO_WORK = DATA / 'exowatch'
EXOTIC_BIN = ROOT / '.venv-exotic' / 'bin' / 'exotic'

DEFAULTS = {
    'port': 8790,
    'observer_name': '',          # your name, for credit on AAVSO / MPC reports
    'aavso_code': '',             # AAVSO observer code (free account)
    'contact_email': '',          # used only in the MPC report header you send yourself
    'sarc_verified': False,       # set True once the MPC's archival committee has verified you
    'discord_webhook': '',        # optional: alerts to a Discord channel
    'alerts': {'mac': True, 'ntfy_topic': ''},   # problems found by the hourly health check: a Mac notification, and a phone push if you
                                  # install the ntfy app and put a long, hard-to-guess topic name here (anyone who knows it can read it)
    'discord_alerts': {'tasks': True, 'brief': True, 'errors': True},
    'llm': {
        'host': 'http://127.0.0.1:11434',
        # Gemma 4 and bge-m3 are often already loaded by other apps, so they may cost no extra memory.
        'writer': ['gemma4:26b', 'qwen3.6:35b', 'gpt-oss:20b'],
        'json': ['gpt-oss:20b', 'gemma4:26b', 'qwen3.6:35b'],
        'vision': ['gemma4:26b'],   # looks at plots (Gemma 4 can read images)
        'embed': 'bge-m3',
    },
    'always_on': False,         # full-time mode: keep the Mac awake and run TESS + library back-to-back when idle
    'ai_when_idle': True,       # run the local AI (TESS first looks, news radar) whenever nobody has used the Mac for a while
    'idle_minutes': 5,
    'schedule': {               # local time, 24h. Jobs run one at a time.
        'tess': '01:30',
        'precovery': '03:30',
        'exowatch': '05:00',
        'arxiv': '06:00',
        'brief': '06:40',
        'targets': '07:00',
        'publish': 'every 3h',  # the public website (only what changed is uploaded)
        'neocp': 'every 2h',
        'library': 'every 3h',
        'beyond': 'every 3h',
        'radar': 'every 6h',    # the local AI reads and ranks everything new in space science
        'cheops': 'every 72h',  # answer-key test: recover known planets in public CHEOPS data (a few visits each time)
        'plates': 'every 24h',  # old photographic plates for newly found distant objects (the 2026 ED138 method)
        'universe': 'every 720h',  # the 3D galaxy map's files (the catalogues rarely change)
        'solar': 'every 24h',      # where the planets and spacecraft are (JPL Horizons), for the live solar system map
        'variables': 'every 12h',  # variable stars in the TESS light curves: catalogue checks, back-fill of earlier stars
        'catalogs': 'every 168h',  # local copies of the catalogues the jobs depend on (spacelab/localcat.py)
        'health': 'every 1h',      # are the outside services answering, has a job failed twice, is the website current
        'injection': 'every 720h', # fake planets hidden in real light curves: what the planet search can find
        'vsplan': '15:30',         # variable-star observatory: tonight's targets (runs only when vstars.enabled)
        'vsphot': 'every 1h',      # variable-star observatory: measure new images, make AAVSO files (only when vstars.enabled)
    },
    'arxiv': {
        'categories': ['astro-ph.EP', 'astro-ph.SR', 'astro-ph.IM', 'astro-ph.GA', 'astro-ph.HE', 'astro-ph.CO'],
        'max_fetch': 300,
        'top_n': 10,
        'interests': [
            'Finding and characterizing exoplanets, especially with TESS and JWST',
            'Exoplanet transit timing, ephemeris refinement and citizen-science observations',
            'Near-Earth asteroids, impact risk, planetary defense, orbit determination and precovery',
            'Space debris, satellites and orbital tracking',
            'New methods or machine learning for finding planets or asteroids in survey data',
            'The Sun, space weather and stellar flares',
        ],
        'keywords': ['exoplanet', 'tess', 'transit', 'near-earth', 'asteroid', 'precovery', 'impact',
                     'planetary defense', 'debris', 'citizen', 'jwst', 'hot jupiter', 'ephemeris'],
    },
    'tess': {
        'time_budget_min': 150,
        'max_targets': 200,
        'workers': 0,             # stars at a time (0 = automatic, from the number of CPU cores)
        'centroid': True,         # pixel-level check on signals that pass the light-curve checks
        'fpp': True,              # TRICERATOPS false-positive probability for signals that pass everything
        'teff_max': 4000,
        'tmag_max': 13.5,         # full-frame-image light curves reach fainter stars
        'dist_max_pc': 150,
        'min_period': 0.5,
        'max_period': 30.0,
        'sde_min': 9.0,
        'snr_min': 7.1,
    },
    'variables': {               # variable stars in TESS light curves (spacelab/variables.py); never submitted from here
        'backfill_min': 25, 'checks_per_run': 40, 'time_budget_min': 45,
    },
    'vstars': {                  # a variable-star observatory's own targets, photometry and AAVSO files (VARIABLE_STARS.md).
        'enabled': False,        # Private: location and key stay on this computer. Nothing is ever sent to the AAVSO from here.
        'site': {'lat': None, 'lon': None, 'elev_m': 0},   # blank = the telescope location in Setup
        'obscode': '',           # AAVSO observer code (blank = the one in Setup)
        'targettool_key': '',    # your AAVSO Target Tool API key (Target Tool > Settings); or export a CSV instead:
        'targettool_csv': '',    # path of a CSV exported from the Target Tool (blank = inbox/vstars/targettool.csv)
        'sections': ['ac'],      # Target Tool sections: ac (alerts/campaigns), ep, cv, eb, spp, lpv, yso, het, misc
        'watchlist': [],         # your own stars, e.g. [{"name": "SS Cyg", "cadence_days": 1}, {"name": "V Sge", "series_min": 120}]
        'sun_alt': -12, 'min_alt': 30, 'moon_sep_min': 15, 'bright_limit': 8.0, 'faint_limit': 16.0,
        'filters': [             # fits = the FILTER name in your images; aavso = the AAVSO code; chart = the band on AAVSO charts
            {'fits': 'B', 'aavso': 'B', 'chart': 'B', 'exp_at_mag12': 40, 'n': 1},
            {'fits': 'V', 'aavso': 'V', 'chart': 'V', 'exp_at_mag12': 20, 'n': 1},
            {'fits': 'R', 'aavso': 'R', 'chart': 'Rc', 'exp_at_mag12': 15, 'n': 1},
            {'fits': 'I', 'aavso': 'I', 'chart': 'Ic', 'exp_at_mag12': 20, 'n': 1},
        ],
        'exp_min': 3, 'exp_max': 300, 'readout_s': 5, 'filter_change_s': 10, 'overhead_s': 120,
        'max_targets': 60, 'series_min': 60,
        'classes': [[15, 'VS short'], [60, 'VS medium'], [100000, 'VS long']],   # N.I.N.A. Target Scheduler Types
        'nina_import_dir': '',   # also copy tonight's CSV here (e.g. a shared folder the N.I.N.A. computer sees)
        'images_dir': '',        # where N.I.N.A. saves the light frames (a shared folder is fine)
        'calib_dir': '',         # master or single bias/dark/flat frames (blank = images are used as they are)
        'lookback_days': 30, 'pixel_scale': None, 'saturation_adu': 60000, 'gain_e_adu': None,
        'aperture_fwhm': 1.6, 'snr_min': 20, 'snr_comp': 50, 'check_tol': 0.1, 'comp_clip': 0.05, 'airmass_max': 2.5,
        'group_gap_min': 20,
        'transform': {},         # your transformation coefficients, e.g. {"V": ["B", "V", 0.031]} (TRANS=YES when applied)
        'review_all': False,     # True = every measurement waits for you, not only the ones with a warning
        'obstype': 'CCD', 'time_budget_min': 50,
        'workers': 0,            # images measured at a time (0 = automatic from the CPU cores; the same results as one at a time)
        'ai': True,              # the local AI reads campaign pages, looks at borderline frames and words the pre-upload summary
        # AAVSO campaigns (apps.aavso.org/v2/campaigns) go into the plan with their filters, cadence and end date
        'campaigns': True, 'campaign_cadence_days': 1.0,
        # outbursts and fades: compared with the last 30 days of AAVSO data; the star then gets priority for boost_days
        'outburst_days': 30, 'outburst_min_points': 2, 'boost_days': 7,
        # frame check before measuring (fractions of the field's usual star count, star elongation), clouds from the zero points
        'qc_stars_bad': 0.15, 'qc_stars_low': 0.45, 'qc_elong_max': 1.6, 'qc_elong_bad': 2.2,
        'qc_cloud_mag': 0.25, 'qc_cloud_bad': 0.75, 'qc_photometric_scatter': 0.08,
        'qc_ai': True, 'qc_ai_max': 30, 'qc_ai_confidence': 0.6,
        'linear_adu': None,      # top of the camera's linear range in ADU (from a linearity test); blank = 85% of saturation
        'blend_max': 0.05, 'comp_blend_max': 0.03,     # neighbour light inside the aperture: flag the variable / drop a comparison star
        'catalog_check': True,   # look up comparison stars in AAVSO VSX and Gaia DR3 (through VizieR), once per chart
        'comp_var_max': 0.04, 'comp_offset_max': 0.08,  # comparison-star health: night-to-night variation, offset from the chart value
        # colour transformation from standard fields: null = measured unless you have your own "transform"; true = always; false = never
        'transform_auto': None, 'transform_every_days': 30, 'standard_fields': [], 'standard_frames': 3, 'standard_score': 5.0,
        'group_fields': True, 'fov_arcmin': None, 'group_dmag': 2.5,   # stars that share a field become one pointing
        'auto_overhead': True,   # plan with the download, filter-change and slew times measured from your own images
        'measure_all_program_stars': True,    # measure every planned or watched star that falls in a frame
        'share_coverage': False, # put the stars you measured lately (names, filters, nights) on the public page, for other observers
        'peers': [],             # other Space Lab public pages (https://...): their coverage lowers the priority of the same stars here
    },
    'injection': {'trials_per_run': 400, 'time_budget_min': 60},   # fake-planet test of the planet search (spacelab/injection.py)
    'plates': {                  # old-plate precovery of distant objects (spacelab/plates.py)
        'manual': ['2026 ED138'],    # always searched first; objects already measured by others become accuracy tests
        'objects_per_run': 2, 'sigma': 3, 'max_ellipse_arcsec': 150, 'depth_margin_min': 0.0, 'plates_per_object': 8,
        'h_max': 8.0, 'plate_limit': 21.0, 'first_after': 1995, 'research_days': 120, 'step_days': 2,
        'time_budget_min': 60,
    },
    'precovery': {
        'time_budget_min': 100,
        'max_targets': 6,
        'h_max': 24.5,            # brighter than this (smaller H = bigger asteroid)
        'arc_max_days': 400,      # only objects whose observed arc is still short
        'sigma_max_arcsec': 240,  # skip old images where the 3-sigma search area is bigger than this
        'research_days': 21,      # re-search a target after this many days
    },
    'neocp': {
        'time_budget_min': 30,
        'alert_pha_score': 50,    # Discord alert when JPL Scout rates a new object at least this likely to be hazardous
        'new_days': 10,           # newly announced near-Earth asteroids from the last N days get a fast precovery check
        'max_searches': 3,
    },
    'library': {'time_budget_min': 45, 'min_year': 1976},   # 50 years: arXiv from 1992, OpenAlex before that
    'targets': {'time_budget_min': 15, 'days_ahead': 7, 'mobs_vmax': 12.5, 'mobs_depth_ppt': 5.0},
    'telescope': {                # your own smart telescope (private: location is never shared)
        'model': '',              # 'Seestar S50', 'Dwarf 3' or ''
        'lat': '', 'lon': '', 'elev': 50,
    },
    'share': {                    # everything here is optional
        'public_name': '',        # name shown on the shared page and Discord posts (blank = "a Space Lab volunteer")
        'github_repo': '',        # e.g. yourname/spacelab  (free GitHub Pages site)
        'github_token': '',       # fine-grained token with Contents + Pages write on that one repo
        'site_url': '',           # your own web address once you have one (e.g. https://www.example.com/); blank = GitHub Pages
        'goatcounter': '',        # visitor counter code (the "xyz" in xyz.goatcounter.com): no cookies, no personal data
        'goatcounter_token': '',  # GoatCounter API key, so this Mac can read visitor numbers and daily votes (never published)
        'sponsors': '',           # GitHub Sponsors page (https://github.com/sponsors/yourname or just yourname): a Support card on the public page
        'kofi': '',               # Ko-fi page (https://ko-fi.com/yourpage or just yourpage)
        'discord_webhook': '',    # webhook of the PUBLIC science channel (different from your private alerts)
        'post_summary': True,     # short nightly summary in the science channel
        'publish': True,          # update the web page every morning
    },
    'exowatch': {
        'lat': '+31.68', 'long': '-110.88', 'elev': 2616,   # MicroObservatory (Exoplanet Watch telescopes)
        'filter': 'MObs CV', 'camera': 'CCD', 'binning': '1x1',
        'timeout_min': 150,
    },
}


def _merge(base, extra):
    out = copy.deepcopy(base)
    for k, v in (extra or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load():
    user = {}
    if SETTINGS_PATH.exists():
        try:
            user = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            user = {}
    return _merge(DEFAULTS, user)


def save_user(changes):
    """Merge `changes` into data/settings.json (only keys the user changed are stored)."""
    ensure_dirs()
    user = {}
    if SETTINGS_PATH.exists():
        try:
            user = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            user = {}
    user = _merge(user, changes)
    tmp = SETTINGS_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(user, indent=2))
    tmp.replace(SETTINGS_PATH)
    return load()


def replace_user(key, value):
    """Replace one whole block of data/settings.json (so keys removed in an editor are really removed)."""
    ensure_dirs()
    user = {}
    if SETTINGS_PATH.exists():
        try:
            user = json.loads(SETTINGS_PATH.read_text())
        except Exception:
            user = {}
    user[key] = value
    tmp = SETTINGS_PATH.with_suffix('.tmp')
    tmp.write_text(json.dumps(user, indent=2))
    tmp.replace(SETTINGS_PATH)
    return load()


def ensure_dirs():
    for p in (DATA, LOGS, INBOX, PLOTS, CACHE, EXO_INBOX, EXO_WORK):
        p.mkdir(parents=True, exist_ok=True)


def job_env():
    """Environment for job subprocesses: keep every cache inside the Space Lab folder."""
    env = dict(os.environ)
    env['ASTROPY_CACHE_DIR'] = str(CACHE / 'astropy')
    env['ASTROPY_CONFIG_DIR'] = str(CACHE / 'astropy-config')
    env['XDG_CACHE_HOME'] = str(CACHE / 'xdg')
    env['MPLCONFIGDIR'] = str(CACHE / 'matplotlib')
    env['NUMBA_CACHE_DIR'] = str(CACHE / 'numba')
    env['LDTK_ROOT'] = str(CACHE / 'ldtk')
    env['MPLBACKEND'] = 'Agg'
    env['PYTHONUNBUFFERED'] = '1'
    env['PYTHONPYCACHEPREFIX'] = str(CACHE / 'pycache')
    for k in ('ASTROPY_CACHE_DIR', 'ASTROPY_CONFIG_DIR', 'XDG_CACHE_HOME', 'MPLCONFIGDIR', 'NUMBA_CACHE_DIR', 'LDTK_ROOT'):
        Path(env[k]).mkdir(parents=True, exist_ok=True)
    try:
        import certifi
        env.setdefault('SSL_CERT_FILE', certifi.where())
        env.setdefault('REQUESTS_CA_BUNDLE', certifi.where())
    except Exception:
        pass
    env['PYTHONPATH'] = str(ROOT) + os.pathsep + env.get('PYTHONPATH', '')
    return env
