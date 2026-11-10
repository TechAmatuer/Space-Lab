"""The solar system, live: where the planets, some small worlds and the spacecraft out there are right now, where they have
been and where they are going. For the public page and Meridian (web/solar.js).

Every position comes from NASA/JPL's Horizons service (the same one mission teams use), asked once a day:
  * each object's position and velocity around the Sun from 30 days ago to 400 days ahead, close enough together that the
    page can work out the position for any second in between;
  * for spacecraft, the path since launch (coarser, cached for a month), for the trails.
The page then needs nothing but these two files. An object Horizons does not know, or answers for with a different name, is
left out and logged; nothing is guessed. What each spacecraft is for is written here by hand; the lists of spacecraft at
each planet say when they were last checked.
"""
import json
import math
import re
import struct
import time

import numpy as np

from . import config, db, net

API = 'https://ssd.jpl.nasa.gov/api/horizons.api'
SITE_DIR = config.PLOTS / 'solar'
CACHE = config.CACHE / 'solar_history.json'
BACK, AHEAD = 30.0, 400.0            # days of exact positions around today
HISTORY_N = 120                      # points of each spacecraft's path since launch
LISTS_CHECKED = '2026-11-10'         # when the hand-written lists below were last checked

# key, Horizons id, name, kind, group, step (days) of the exact part, colour
#   kind: planet | world (dwarf planets, asteroids, comets) | craft
P, W, C = 'planet', 'world', 'craft'
OBJECTS = [
    ('mercury', '199', 'Mercury', P, 'planets', 1, '#b9b3a8'), ('venus', '299', 'Venus', P, 'planets', 2, '#e8cf9a'),
    ('earth', '399', 'Earth', P, 'planets', 2, '#6fa8ff'), ('mars', '499', 'Mars', P, 'planets', 2, '#e0704a'),
    ('jupiter', '5', 'Jupiter', P, 'planets', 10, '#d9b48a'), ('saturn', '6', 'Saturn', P, 'planets', 10, '#e6d39a'),
    ('uranus', '7', 'Uranus', P, 'planets', 10, '#9fe0e0'), ('neptune', '8', 'Neptune', P, 'planets', 10, '#6f8fff'),
    ('ceres', '1;', 'Ceres', W, 'worlds', 5, '#c9c9c9'), ('vesta', '4;', 'Vesta', W, 'worlds', 5, '#c9c9c9'),
    ('psyche16', '16;', 'Psyche (asteroid)', W, 'worlds', 5, '#c9c9c9'),
    ('apophis', '99942;', 'Apophis', W, 'worlds', 2, '#c9c9c9'), ('didymos', '65803;', 'Didymos', W, 'worlds', 2, '#c9c9c9'),
    ('pluto', '9', 'Pluto', W, 'worlds', 10, '#d8c7b0'), ('eris', '136199;', 'Eris', W, 'worlds', 10, '#d0d0d0'),
    ('haumea', '136108;', 'Haumea', W, 'worlds', 10, '#d0d0d0'), ('makemake', '136472;', 'Makemake', W, 'worlds', 10, '#d0d0d0'),
    ('sedna', '90377;', 'Sedna', W, 'worlds', 10, '#d08c7a'), ('ed138', 'DES=2026 ED138;', '2026 ED138', W, 'worlds', 10, '#7ef0cf'),
    ('halley', 'DES=1P; CAP;', "Halley's Comet", W, 'worlds', 10, '#9fd8ff'), ('atlas3i', 'DES=3I;', '3I/ATLAS', W, 'worlds', 5, '#9fd8ff'),
    ('voyager1', '-31', 'Voyager 1', C, 'leaving', 10, '#ffd24a'), ('voyager2', '-32', 'Voyager 2', C, 'leaving', 10, '#ffd24a'),
    ('pioneer10', '-23', 'Pioneer 10', C, 'leaving', 10, '#c9a84a'), ('pioneer11', '-24', 'Pioneer 11', C, 'leaving', 10, '#c9a84a'),
    ('newhorizons', '-98', 'New Horizons', C, 'leaving', 10, '#ffd24a'),
    ('parker', '-96', 'Parker Solar Probe', C, 'sun', 0.25, '#ff8a3d'), ('solo', '-144', 'Solar Orbiter', C, 'sun', 1, '#ff8a3d'),
    ('stereoa', '-234', 'STEREO-A', C, 'sun', 5, '#ff8a3d'), ('soho', '-21', 'SOHO', C, 'sun', 5, '#ff8a3d'),
    ('bepi', '-121', 'BepiColombo', C, 'travelling', 0.5, '#ff6bd6'), ('juice', '-28', 'JUICE', C, 'travelling', 2, '#ff6bd6'),
    ('clipper', '-159', 'Europa Clipper', C, 'travelling', 2, '#ff6bd6'), ('lucy', '-49', 'Lucy', C, 'travelling', 5, '#ff6bd6'),
    ('psyche', '-255', 'Psyche (spacecraft)', C, 'travelling', 5, '#ff6bd6'), ('hera', '-91', 'Hera', C, 'travelling', 2, '#ff6bd6'),
    ('apex', '-64', 'OSIRIS-APEX', C, 'travelling', 2, '#ff6bd6'), ('hayabusa2', '-37', 'Hayabusa2', C, 'travelling', 2, '#ff6bd6'),
    ('juno', '-61', 'Juno', C, 'travelling', 10, '#ff6bd6'),
    ('jwst', '-170', 'James Webb Space Telescope', C, 'telescopes', 5, '#57e3e3'), ('euclid', '-680', 'Euclid', C, 'telescopes', 5, '#57e3e3'),
    ('tess', '-95', 'TESS', C, 'telescopes', 5, '#57e3e3'), ('gaia', '-139479', 'Gaia', C, 'telescopes', 5, '#57e3e3'),
    ('spitzer', '-79', 'Spitzer', C, 'drifting', 5, '#8a94a6'), ('kepler', '-227', 'Kepler', C, 'drifting', 5, '#8a94a6'),
    ('roadster', '-143205', 'Tesla Roadster', C, 'drifting', 5, '#ff5e5e'),
]
# a word that must be in the name Horizons answers with (a wrong id must never put a wrong object on the map)
EXPECT = {'psyche16': 'Psyche', 'ed138': 'ED138', 'halley': 'Halley', 'atlas3i': 'ATLAS', 'newhorizons': 'New Horizons', 'parker': 'Parker',
          'solo': 'Solar Orbiter', 'stereoa': 'STEREO', 'bepi': 'BepiColombo', 'clipper': 'Clipper', 'psyche': 'Psyche', 'apex': 'OSIRIS',
          'hayabusa2': 'Hayabusa', 'jwst': 'Webb', 'roadster': 'Roadster', 'voyager1': 'Voyager 1', 'voyager2': 'Voyager 2',
          'pioneer10': 'Pioneer 10', 'pioneer11': 'Pioneer 11', 'pluto': 'Pluto'}
GROUPS = [('planets', 'Planets'), ('leaving', 'Leaving the solar system'), ('sun', 'Watching the Sun'),
          ('travelling', 'On the way, or at work at a planet'), ('telescopes', 'Space telescopes near Earth'),
          ('drifting', 'Switched off, still circling the Sun'), ('worlds', 'Dwarf planets, asteroids and comets')]

# what it is and what it does: [who, launched (spacecraft), text]
ABOUT = {
    'mercury': ['', '', 'The smallest planet and the closest to the Sun: a year lasts 88 days. No air to speak of, 430 °C by day and -180 °C at night.'],
    'venus': ['', '', 'Earth\'s size, but under a crushing carbon-dioxide atmosphere at 465 °C: the hottest planet. No spacecraft is working there right now.'],
    'earth': ['', '', 'Home. Everything else on this map was launched from here.'],
    'mars': ['', '', 'A cold desert world half Earth\'s width, with the busiest fleet of robots anywhere beyond Earth.'],
    'jupiter': ['', '', 'The giant: more than twice the mass of all other planets together, with nearly a hundred known moons. Four of them are worlds of their own.'],
    'saturn': ['', '', 'The ringed giant. Its moon Titan has rivers and seas of liquid methane; its moon Enceladus sprays water into space.'],
    'uranus': ['', '', 'An ice giant tipped on its side. Visited once, by Voyager 2 in 1986.'],
    'neptune': ['', '', 'The farthest planet: a year lasts 165 Earth years. Visited once, by Voyager 2 in 1989.'],
    'ceres': ['', '', 'The largest object in the asteroid belt and the nearest dwarf planet. NASA\'s Dawn orbited it from 2015 to 2018 and is still circling it, switched off.'],
    'vesta': ['', '', 'The second-largest asteroid, bright enough to see without a telescope at times. Dawn orbited it in 2011-2012.'],
    'psyche16': ['', '', 'A metal-rich asteroid 220 km wide, perhaps the stripped core of a world that never finished forming. The Psyche spacecraft arrives in 2029.'],
    'apophis': ['', '', 'A 340-metre asteroid that will pass closer than our weather satellites on 13 April 2029, visible to the naked eye. It will not hit. OSIRIS-APEX will be there.'],
    'didymos': ['', '', 'A small asteroid with a moon, Dimorphos. In 2022 NASA\'s DART hit the moon on purpose and changed its orbit: the first planetary-defence test. Hera is on its way to inspect the result.'],
    'pluto': ['', '', 'The best-known dwarf planet, with a heart-shaped glacier of nitrogen ice. New Horizons flew past in 2015.'],
    'eris': ['', '', 'A dwarf planet as big as Pluto, right now nearly three times farther away. Finding it in 2005 led to the new definition of "planet".'],
    'haumea': ['', '', 'An egg-shaped dwarf planet that spins once every four hours, with rings and two moons.'],
    'makemake': ['', '', 'A reddish dwarf planet beyond Pluto, found in 2005.'],
    'sedna': ['', '', 'A red world on an 11,000-year orbit that never comes near the planets. Nobody knows for sure how it got there.'],
    'ed138': ['', '', 'A large distant object found in 2026 with the Vera C. Rubin Observatory, and a dwarf-planet candidate. Space Lab measured it on photographic plates from 1954 to 1991 (Asteroids tab).'],
    'halley': ['', '', 'The most famous comet, back every 76 years. It passed its farthest point from the Sun in 2023, beyond Neptune, and returns in 2061.'],
    'atlas3i': ['', '', 'The third known visitor from another star, found in July 2025. It passed the Sun in October 2025 and is leaving for good.'],
    'voyager1': ['NASA', '1977-09-05', 'The farthest thing humans have made. It flew past Jupiter and Saturn, crossed into the space between the stars in 2012, and still phones home with a 23-watt radio. It carries a golden record of sounds and pictures from Earth.'],
    'voyager2': ['NASA', '1977-08-20', 'The only spacecraft to visit Uranus and Neptune. In interstellar space since 2018 and still sending data, with instruments being switched off one by one to save power.'],
    'pioneer10': ['NASA', '1972-03-03', 'The first spacecraft through the asteroid belt and past Jupiter (1973). Silent since 2003, coasting towards the star Aldebaran. Its position here is worked out, not tracked.'],
    'pioneer11': ['NASA', '1973-04-06', 'The first spacecraft to fly past Saturn (1979). Silent since 1995. Its position here is worked out, not tracked.'],
    'newhorizons': ['NASA', '2006-01-19', 'Flew past Pluto in 2015 and the small world Arrokoth in 2019, and keeps going through the Kuiper belt, measuring dust and the Sun\'s thinning wind.'],
    'parker': ['NASA', '2018-08-12', 'Flies through the Sun\'s outer atmosphere, closer than anything before it: 6 million km from the surface, at up to 690,000 km/h, the fastest object ever built. It studies why the corona is so hot and where the solar wind starts.'],
    'solo': ['ESA / NASA', '2020-02-10', 'Takes the closest pictures of the Sun, and is tilting its orbit to look down on the Sun\'s poles, which had never been seen.'],
    'stereoa': ['NASA', '2006-10-26', 'Circles the Sun slightly faster than Earth, so it sees the Sun from the side: a second viewpoint for solar storms heading our way.'],
    'soho': ['ESA / NASA', '1995-12-02', 'Has watched the Sun without a break since 1996 from a spot 1.5 million km sunward of Earth. Also the most successful comet finder ever: over 5,000, many spotted by volunteers.'],
    'bepi': ['ESA / JAXA', '2018-10-20', 'Two orbiters travelling together to Mercury: Europe\'s to map the surface and interior, Japan\'s to study its magnetic field. After eight years and nine planet flybys they enter orbit around Mercury on 21 November 2026.'],
    'juice': ['ESA', '2023-04-14', 'On its way to Jupiter\'s icy moons Ganymede, Callisto and Europa, which hide oceans under their ice. Arrives in 2031 and will be the first spacecraft to orbit a moon of another planet (Ganymede).'],
    'clipper': ['NASA', '2024-10-14', 'Will fly past Jupiter\'s moon Europa about 50 times from 2030 to find out whether the ocean under its ice could support life. The largest planetary spacecraft NASA has built.'],
    'lucy': ['NASA', '2021-10-16', 'Touring the Trojan asteroids that share Jupiter\'s orbit, leftovers from when the planets formed. It reaches the first group in 2027.'],
    'psyche': ['NASA', '2023-10-13', 'Travelling to the metal-rich asteroid Psyche with solar-electric engines; it arrives in 2029.'],
    'hera': ['ESA', '2024-10-07', 'Going back to the asteroid pair Didymos and Dimorphos to measure exactly what NASA\'s DART impact did in 2022, so the method can be trusted if an asteroid ever needs deflecting. It arrives at the end of 2026.'],
    'apex': ['NASA', '2016-09-08', 'As OSIRIS-REx it brought pieces of asteroid Bennu to Earth in 2023. Renamed, it now heads for asteroid Apophis and will arrive just after its close pass of Earth in April 2029.'],
    'hayabusa2': ['JAXA', '2014-12-03', 'Brought samples of asteroid Ryugu to Earth in 2020 and kept going: it is on its way to a tiny, fast-spinning asteroid it will reach in 2031.'],
    'juno': ['NASA', '2011-08-05', 'In orbit around Jupiter since 2016, flying over its poles. It showed that Jupiter\'s core is fuzzy rather than solid and photographed the moons Io, Europa and Ganymede up close.'],
    'jwst': ['NASA / ESA / CSA', '2021-12-25', 'The largest telescope in space, with a 6.5-metre gold mirror. It sees infrared light: the first galaxies, stars being born inside dust clouds, and the air of planets around other stars. It sits 1.5 million km from Earth, on the side away from the Sun.'],
    'euclid': ['ESA', '2023-07-01', 'Maps billions of galaxies across a third of the sky to work out what dark matter and dark energy are doing to the universe.'],
    'tess': ['NASA', '2018-04-18', 'Watches patch after patch of sky for the tiny dips when a planet crosses its star. Space Lab searches its data every night (TESS hunter tab). It loops around Earth every 13.7 days.'],
    'gaia': ['ESA', '2013-12-19', 'Measured the positions and motions of nearly two billion stars, the map most of astronomy now rests on. Retired in March 2025 and parked in an orbit around the Sun. Space Lab uses its catalogue daily.'],
    'spitzer': ['NASA', '2003-08-25', 'An infrared telescope that worked until 2020 and found, among much else, most of the seven planets of TRAPPIST-1. It trails behind Earth, drifting slowly away.'],
    'kepler': ['NASA', '2009-03-07', 'Stared at one patch of sky and showed that planets are everywhere: over 2,700 confirmed. Out of fuel since 2018, it follows Earth around the Sun.'],
    'roadster': ['SpaceX', '2018-02-06', 'A car. It was the dummy load of the first Falcon Heavy rocket and has been circling the Sun ever since, out past Mars and back.'],
}
# spacecraft that sit at a planet (they would all fall on one dot at this scale): [name, who, what]
AT = {
    'mercury': [['BepiColombo', 'ESA / JAXA', 'two orbiters, entering orbit on 21 November 2026']],
    'earth': [['More than 14,000 working satellites', '', 'most of them for internet, navigation, weather and Earth pictures (Sky watch tab shows some passing over you)'],
              ['International Space Station', 'USA, Russia, Europe, Japan, Canada', 'people have lived aboard without a break since 2000'],
              ['Tiangong', 'China', 'China\'s space station, crewed since 2021'],
              ['Hubble Space Telescope', 'NASA / ESA', 'in orbit since 1990, still working'],
              ['At the Moon', '', 'NASA\'s Lunar Reconnaissance Orbiter, India\'s Chandrayaan-2, Korea\'s Danuri and China\'s relay satellites; on the surface, six Apollo sites and landers from five countries'],
              ['1.5 million km towards the Sun', '', 'the Sun watchers SOHO, ACE, DSCOVR, Wind, Aditya-L1 and IMAP'],
              ['1.5 million km away from the Sun', '', 'the telescopes James Webb, Euclid and, since 2026, Nancy Grace Roman']],
    'mars': [['Mars Reconnaissance Orbiter', 'NASA', 'the sharpest camera at Mars, since 2006'], ['Mars Odyssey', 'NASA', 'the longest-working spacecraft at another planet, since 2001'],
             ['MAVEN', 'NASA', 'showed how Mars lost its air; it fell silent in December 2025 and the mission was ended in 2026. It still circles Mars'], ['Mars Express', 'ESA', 'since 2003'], ['ExoMars Trace Gas Orbiter', 'ESA', 'sniffs for methane'],
             ['Tianwen-1', 'China', 'orbiter, since 2021'], ['Hope', 'United Arab Emirates', 'weather satellite for Mars'],
             ['Curiosity', 'NASA', 'rover in Gale crater since 2012'], ['Perseverance', 'NASA', 'rover in Jezero crater since 2021, collecting samples']],
    'jupiter': [['Juno', 'NASA', 'in orbit since 2016 (it has its own dot on this map)']],
    'ceres': [['Dawn', 'NASA', 'switched off in 2018, still in orbit']],
}
MON = {m: k + 1 for k, m in enumerate(('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'))}


def jd_now():
    return time.time() / 86400.0 + 2440587.5


def cal(jd):
    """Calendar date and time for Horizons ('2026-11-10 12:00')."""
    return time.strftime('%Y-%m-%d %H:%M', time.gmtime((jd - 2440587.5) * 86400.0))


def cal_jd(text):
    """'1977-SEP-05 12:59' (as in Horizons' messages) -> JD."""
    m = re.match(r'(\d{4})-([A-Za-z]{3})-(\d{2}) (\d{2}):(\d{2})', text)
    y, mo, d, h, mi = int(m.group(1)), MON[m.group(2).upper()], int(m.group(3)), int(m.group(4)), int(m.group(5))
    import calendar
    return calendar.timegm((y, mo, d, h, mi, 0)) / 86400.0 + 2440587.5


def parse_vectors(text):
    """(name Horizons gave, [[jd, x, y, z, vx, vy, vz], ...]) from a Horizons vector table (AU, AU/day)."""
    name = (re.search(r'Target body name:\s*(.*?)\s*\{', text) or re.search(r'Target body name:\s*([^\n]*)', text))
    rows = []
    if '$$SOE' in text and '$$EOE' in text:
        for ln in text.split('$$SOE')[1].split('$$EOE')[0].strip().splitlines():
            p = [x.strip() for x in ln.split(',')]
            try:
                rows.append([float(p[0])] + [float(x) for x in p[2:8]])
            except (ValueError, IndexError):
                continue
    return (name.group(1).strip() if name else ''), rows


def limit_from_error(text):
    """Horizons says when its path of an object starts or ends: ('prior to' | 'after', JD), or None."""
    m = re.search(r'(prior to|after) A\.D\. (\d{4}-[A-Za-z]{3}-\d{2} \d{2}:\d{2})', text or '')
    return (m.group(1), cal_jd(m.group(2))) if m else None


def vectors(hid, jd0, jd1, step, fetch=None):
    """Positions and velocities of one object around the Sun (ecliptic J2000). step: '5 d', '6 h', or a number of intervals.
    If Horizons' path starts later or ends earlier than asked, the span is cut to what exists."""
    fetch = fetch or (lambda params: net.fetch_text(API, params=params, timeout=90, retries=2, min_gap=0.4))
    name, rows = '', []
    for _ in range(3):
        if jd1 - jd0 < 0.5:
            break
        text = fetch({'format': 'text', 'COMMAND': f"'{hid}'", 'OBJ_DATA': 'NO', 'MAKE_EPHEM': 'YES', 'EPHEM_TYPE': 'VECTORS',
                      'CENTER': "'500@10'", 'REF_PLANE': 'ECLIPTIC', 'START_TIME': f"'{cal(jd0)}'", 'STOP_TIME': f"'{cal(jd1)}'",
                      'STEP_SIZE': f"'{step}'", 'VEC_TABLE': '2', 'CSV_FORMAT': 'YES', 'OUT_UNITS': "'AU-D'", 'VEC_CORR': 'NONE'})
        name, rows = parse_vectors(text)
        if rows:
            break
        lim = limit_from_error(text)
        if not lim:
            break
        if lim[0] == 'prior to':
            jd0 = lim[1] + 0.05
        else:
            jd1 = lim[1] - 0.05
    return name, rows


def step_text(days):
    return f'{int(round(days * 24))} h' if days < 1 else f'{int(days)} d'


def pack(objs, t0):
    """Binary file for the page: per object, samples of [days from t0, x, y, z, vx, vy, vz] as 32-bit floats."""
    blob = b'SLS1' + struct.pack('<I', len(objs)) + struct.pack('<d', t0)
    off = 0
    for o in objs:
        a = np.asarray(o.pop('rows'), float)
        a[:, 0] -= t0
        o['off'], o['n'] = off, len(a)
        off += len(a)
        blob += a.astype('<f4').tobytes()
    return blob


def read(bin_path, meta):
    """The tracks back from the file (for tests): {key: array of [jd, x, y, z, vx, vy, vz]}."""
    from pathlib import Path
    b = Path(bin_path).read_bytes()
    t0 = struct.unpack('<d', b[8:16])[0]
    out = {}
    for o in meta['objects']:
        a = np.frombuffer(b, '<f4', o['n'] * 7, 16 + o['off'] * 28).reshape(-1, 7).astype(float)
        a[:, 0] += t0
        out[o['key']] = a
    return out


def build(log, fetch=None, now=None, history=None):
    """Ask Horizons for everything and write the two files. Returns (meta for the database, history cache)."""
    t0 = now or jd_now()
    history = history if history is not None else {}
    objs, skipped = [], []
    for key, hid, name, kind, group, step, color in OBJECTS:
        try:
            got, rows = vectors(hid, t0 - BACK, t0 + AHEAD, step_text(step), fetch)
        except Exception as e:
            got, rows = '', []
            log(f'  {name}: Horizons did not answer ({str(e)[:80]})')
        want = EXPECT.get(key, name.split(' (')[0])
        if len(rows) < 4 or want.lower() not in got.lower():
            skipped.append(name)
            log(f"  {name}: left out ({'Horizons answered for ' + repr(got) if rows else 'no positions from Horizons'})")
            continue
        about = ABOUT.get(key, ['', '', ''])
        o = {'key': key, 'name': name, 'kind': kind, 'group': group, 'color': color, 'by': about[0], 'launched': about[1], 'what': about[2],
             'horizons': got, 'nh': 0}
        if kind == C and about[1]:                           # the path since launch, for the trail
            h = history.get(key)
            if not h or t0 - h['made'] > 30 or h['rows'][-1][0] < t0 - BACK - 45:
                try:
                    import calendar
                    launch = calendar.timegm(time.strptime(about[1], '%Y-%m-%d')) / 86400.0 + 2440587.5
                    _, hr = vectors(hid, launch + 0.5, t0 - BACK - 1.0, str(HISTORY_N), fetch)
                    if len(hr) >= 4:
                        h = {'made': t0, 'rows': hr}
                        history[key] = h
                except Exception as e:
                    log(f'  {name}: no path since launch this time ({str(e)[:60]})')
            if h:
                past = [r for r in h['rows'] if r[0] < rows[0][0] - 0.5]
                o['nh'] = len(past)
                rows = past + rows
        o['rows'] = rows
        objs.append(o)
    if not any(o['key'] == 'earth' for o in objs) or len(objs) < 10:
        raise RuntimeError(f'Horizons gave too little this time ({len(objs)} objects); the solar system map was left as it was')
    blob = pack(objs, t0)
    full = {'t0': t0, 'made': time.time(), 'back': BACK, 'ahead': AHEAD, 'objects': objs, 'groups': GROUPS, 'at': AT,
            'lists_checked': LISTS_CHECKED, 'skipped': skipped,
            'source': 'Positions: NASA/JPL Horizons, asked once a day. Sizes are not to scale.'}
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    import os
    (SITE_DIR / 'tracks.bin.tmp').write_bytes(blob)
    (SITE_DIR / 'solar.json.tmp').write_text(json.dumps(full, separators=(',', ':')))
    os.replace(SITE_DIR / 'tracks.bin.tmp', SITE_DIR / 'tracks.bin')
    os.replace(SITE_DIR / 'solar.json.tmp', SITE_DIR / 'solar.json')
    meta = {'made': full['made'], 't0': t0, 'n': len(objs), 'n_craft': sum(1 for o in objs if o['kind'] == C), 'skipped': skipped,
            'json': 'plots/solar/solar.json', 'bin': 'plots/solar/tracks.bin'}
    return meta, history


def summary_now(objs_meta, tracks, t0):
    """Distance from the Sun (AU) of each object at t0, for the log (a quick check against what is publicly known)."""
    out = {}
    for o in objs_meta:
        a = tracks[o['key']]
        k = int(np.argmin(np.abs(a[:, 0] - t0)))
        out[o['key']] = float(np.linalg.norm(a[k, 1:4]))
    return out


def run(ctx):
    con, log = ctx.con, ctx.log
    try:
        history = json.loads(CACHE.read_text()) if CACHE.is_file() else {}
    except Exception:
        history = {}
    meta, history = build(log, history=history)
    try:
        config.CACHE.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(history))
    except Exception:
        pass
    db.kv_set(con, 'solar', meta)
    full = json.loads((SITE_DIR / 'solar.json').read_text())
    r = summary_now(full['objects'], read(SITE_DIR / 'tracks.bin', full), meta['t0'])
    show = ', '.join(f"{k} {r[k]:.2f} AU" for k in ('voyager1', 'voyager2', 'newhorizons', 'parker', 'jwst', 'bepi', 'mercury') if k in r)
    log(f'  from the Sun right now: {show}')
    return (f"solar system: {meta['n']} objects placed ({meta['n_craft']} spacecraft)"
            + (f"; left out: {', '.join(meta['skipped'])}" if meta['skipped'] else ''))
