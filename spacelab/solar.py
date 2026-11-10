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
# Spacecraft at each world: what is there now, and what was left there. Every one links to its Wikipedia article
# (titles checked against Wikipedia's own API on LISTS_CHECKED; a title that stops resolving is simply shown without a link).
# n: name, by: who built or runs it, note: one line, wiki: Wikipedia article title, links: several articles [label, title],
# sec: the heading it is listed under.
def _c(n, by, note, wiki=None, sec='', links=None):
    d = {'n': n, 'by': by, 'note': note, 'sec': sec}
    if wiki:
        d['wiki'] = wiki
    if links:
        d['links'] = links
    return d


AT = {
    'mercury': [_c('BepiColombo', 'ESA / JAXA', 'two orbiters, entering orbit on 21 November 2026 (it has its own dot on this map)', 'BepiColombo'),
                _c('MESSENGER', 'NASA', 'the first Mercury orbiter (2011-2015); it ended by crashing into the surface', 'MESSENGER', 'Left there')],
    'venus': [_c('Akatsuki', 'JAXA', 'Venus weather orbiter from 2015; contact was lost in 2024 and it still circles Venus', 'Akatsuki (spacecraft)'),
              _c('Venus Express', 'ESA', 'orbiter 2006-2014, then burned up in Venus\'s air', 'Venus Express', 'Left there'),
              _c('Magellan', 'NASA', 'mapped the hidden surface by radar, 1990-1994', 'Magellan (spacecraft)', 'Left there'),
              _c('Venera and Vega landers', 'Soviet Union', 'the only craft to have sent pictures from the surface, which is hot enough to melt lead', 'Venera program', 'Left there',
                 [['Venera program', 'Venera program'], ['Vega program', 'Vega program']])],
    'earth': [_c('More than 14,000 working satellites', '', 'most of them for internet, navigation, weather and Earth pictures (Sky watch tab shows some passing over you)', 'Satellite', 'Around Earth'),
              _c('International Space Station', 'USA, Russia, Europe, Japan, Canada', 'people have lived aboard without a break since 2000', 'International Space Station', 'Around Earth'),
              _c('Tiangong', 'China', 'China\'s space station, crewed since 2021', 'Tiangong space station', 'Around Earth'),
              _c('Hubble Space Telescope', 'NASA / ESA', 'in orbit since 1990', 'Hubble Space Telescope', 'Around Earth'),
              _c('Chandra X-ray Observatory', 'NASA', 'X-ray telescope, launched 1999', 'Chandra X-ray Observatory', 'Around Earth'),
              _c('XMM-Newton', 'ESA', 'X-ray telescope, launched 1999', 'XMM-Newton', 'Around Earth'),
              _c('Fermi', 'NASA', 'gamma-ray telescope, launched 2008', 'Fermi Gamma-ray Space Telescope', 'Around Earth'),
              _c('Swift', 'NASA', 'caught gamma-ray bursts within minutes for 22 years; after a rescue mission failed, it re-entered the atmosphere over Indonesia on 4 November 2026', 'Neil Gehrels Swift Observatory', 'Back in the atmosphere'),
              _c('NuSTAR', 'NASA', 'high-energy X-ray telescope, launched 2012', 'NuSTAR', 'Around Earth'),
              _c('IXPE', 'NASA / ASI', 'measures how X-rays are polarised, launched 2021', 'IXPE', 'Around Earth'),
              _c('XRISM', 'JAXA / NASA', 'X-ray spectroscopy, launched 2023', 'X-Ray Imaging and Spectroscopy Mission', 'Around Earth'),
              _c('Einstein Probe', 'China / ESA', 'wide-field X-ray sky watcher, launched 2024', 'Einstein Probe', 'Around Earth'),
              _c('SPHEREx', 'NASA', 'maps the whole sky in 102 infrared colours, launched 2025', 'SPHEREx', 'Around Earth'),
              _c('GPS and the other navigation fleets', 'USA, Europe, China, Russia', 'GPS, Galileo, BeiDou and GLONASS', 'Satellite navigation', 'Around Earth',
                 [['GPS', 'Global Positioning System'], ['Galileo', 'Galileo (satellite navigation system)'], ['BeiDou', 'BeiDou'], ['GLONASS', 'GLONASS']]),
              _c('Landsat', 'NASA / USGS', 'pictures of Earth\'s land since 1972', 'Landsat program', 'Around Earth'),
              _c('Lunar Reconnaissance Orbiter', 'NASA', 'maps the Moon, since 2009', 'Lunar Reconnaissance Orbiter', 'At the Moon'),
              _c('Chandrayaan-2 orbiter', 'India', 'since 2019', 'Chandrayaan-2', 'At the Moon'),
              _c('Danuri', 'South Korea', 'Korea\'s first Moon orbiter, since 2022', 'Danuri', 'At the Moon'),
              _c('Queqiao-2', 'China', 'relay satellite for landers on the far side, since 2024', 'Queqiao-2', 'At the Moon'),
              _c('CAPSTONE', 'NASA', 'tested the orbit planned for the Gateway station, launched 2022', 'CAPSTONE', 'At the Moon'),
              _c('Six Apollo landing sites', 'NASA', 'twelve people walked on the Moon, 1969-1972; their instruments and rovers are still there', 'Apollo program', 'On the Moon',
                 [['Apollo 11', 'Apollo 11'], ['Apollo 12', 'Apollo 12'], ['Apollo 14', 'Apollo 14'], ['Apollo 15', 'Apollo 15'], ['Apollo 16', 'Apollo 16'], ['Apollo 17', 'Apollo 17']]),
              _c('Luna and Lunokhod', 'Soviet Union', 'robot landers, sample returns and the first rovers, 1966-1976', 'Luna programme', 'On the Moon',
                 [['Luna programme', 'Luna programme'], ['Lunokhod 1', 'Lunokhod 1'], ['Lunokhod 2', 'Lunokhod 2']]),
              _c('Chang\'e landers and Yutu rovers', 'China', 'including the first landing on the far side (2019) and the first far-side samples (2024)', 'Chinese Lunar Exploration Program', 'On the Moon',
                 [['Chang\'e 3', "Chang'e 3"], ['Chang\'e 4', "Chang'e 4"], ['Chang\'e 5', "Chang'e 5"], ['Chang\'e 6', "Chang'e 6"]]),
              _c('Chandrayaan-3', 'India', 'Vikram lander and Pragyan rover, near the south pole since 2023', 'Chandrayaan-3', 'On the Moon'),
              _c('SLIM', 'JAXA', 'Japan\'s pinpoint lander, 2024', 'Smart Lander for Investigating Moon', 'On the Moon'),
              _c('Commercial landers', 'USA', 'Odysseus (2024), Blue Ghost and Athena (2025)', 'Commercial Lunar Payload Services', 'On the Moon',
                 [['Odysseus', 'IM-1'], ['Blue Ghost', 'Blue Ghost Mission 1'], ['Athena', 'IM-2']]),
              _c('Sun watchers 1.5 million km towards the Sun', '', 'SOHO is on this map; with it work ACE, DSCOVR, Wind, Aditya-L1 and IMAP', 'Lagrange point', '1.5 million km from Earth',
                 [['ACE', 'Advanced Composition Explorer'], ['DSCOVR', 'Deep Space Climate Observatory'], ['Wind', 'Wind (spacecraft)'], ['Aditya-L1', 'Aditya-L1'], ['IMAP', 'Interstellar Mapping and Acceleration Probe']]),
              _c('Telescopes 1.5 million km away from the Sun', '', 'Webb and Euclid are on this map; with them work Spektr-RG and, since 2026, Nancy Grace Roman', 'Lagrange point', '1.5 million km from Earth',
                 [['Spektr-RG', 'Spektr-RG'], ['Nancy Grace Roman', 'Nancy Grace Roman Space Telescope']])],
    'mars': [_c('Mars Reconnaissance Orbiter', 'NASA', 'the sharpest camera at Mars, since 2006', 'Mars Reconnaissance Orbiter', 'Orbiting'),
             _c('Mars Odyssey', 'NASA', 'the longest-working spacecraft at another planet, since 2001', '2001 Mars Odyssey', 'Orbiting'),
             _c('MAVEN', 'NASA', 'showed how Mars lost its air; it fell silent in December 2025 and the mission was ended in 2026. It still circles Mars', 'MAVEN', 'Orbiting'),
             _c('Mars Express', 'ESA', 'since 2003', 'Mars Express', 'Orbiting'),
             _c('ExoMars Trace Gas Orbiter', 'ESA / Roscosmos', 'sniffs for methane, since 2016', 'Trace Gas Orbiter', 'Orbiting'),
             _c('Tianwen-1', 'China', 'orbiter, since 2021', 'Tianwen-1', 'Orbiting'),
             _c('Hope', 'United Arab Emirates', 'weather satellite for Mars, since 2021', 'Emirates Mars Mission', 'Orbiting'),
             _c('Curiosity', 'NASA', 'rover in Gale crater since 2012', 'Curiosity (rover)', 'On the ground'),
             _c('Perseverance', 'NASA', 'rover in Jezero crater since 2021, collecting samples', 'Perseverance (rover)', 'On the ground'),
             _c('Ingenuity', 'NASA', 'the first aircraft on another world: 72 flights, grounded in 2024', 'Ingenuity (helicopter)', 'On the ground'),
             _c('Zhurong', 'China', 'rover, landed 2021', 'Zhurong (rover)', 'On the ground'),
             _c('Earlier landers and rovers', 'NASA', 'Viking 1 and 2, Sojourner, Spirit, Opportunity, Phoenix and InSight', 'Exploration of Mars', 'On the ground',
                [['Viking 1', 'Viking 1'], ['Viking 2', 'Viking 2'], ['Sojourner', 'Sojourner (rover)'], ['Spirit', 'Spirit (rover)'], ['Opportunity', 'Opportunity (rover)'], ['Phoenix', 'Phoenix (spacecraft)'], ['InSight', 'InSight']])],
    'jupiter': [_c('Juno', 'NASA', 'in orbit since 2016 (it has its own dot on this map)', 'Juno (spacecraft)'),
                _c('Galileo', 'NASA', 'orbiter 1995-2003, then sent into Jupiter so it could never reach Europa', 'Galileo (spacecraft)', 'Left there')],
    'saturn': [_c('Cassini', 'NASA / ESA / ASI', 'orbiter 2004-2017, then sent into Saturn', 'Cassini–Huygens', 'Left there'),
               _c('Huygens', 'ESA', 'landed on the moon Titan in 2005: the farthest landing ever made', 'Huygens (spacecraft)', 'Left there')],
    'ceres': [_c('Dawn', 'NASA', 'switched off in 2018, still in orbit', 'Dawn (spacecraft)')],
    'vesta': [_c('Dawn', 'NASA', 'orbited Vesta in 2011-2012 before moving on to Ceres', 'Dawn (spacecraft)', 'Visited by')],
    'didymos': [_c('DART', 'NASA', 'crashed into the small moon Dimorphos in 2022 on purpose, and changed its orbit', 'Double Asteroid Redirection Test', 'Visited by'),
                _c('Hera', 'ESA', 'on its way to inspect the crash site (it has its own dot on this map)', 'Hera (space mission)')],
    'apophis': [_c('OSIRIS-APEX', 'NASA', 'on its way, to arrive in 2029 just after Apophis passes close to Earth', 'OSIRIS-REx')],
    'pluto': [_c('New Horizons', 'NASA', 'flew past in 2015 (it has its own dot on this map)', 'New Horizons', 'Visited by')],
    'halley': [_c('The 1986 armada', 'ESA, Soviet Union, Japan', 'Giotto, Vega 1 and 2, Suisei and Sakigake flew past Halley in 1986', 'Halley Armada', 'Visited by',
                  [['Giotto', 'Giotto (spacecraft)'], ['Vega 1', 'Vega 1'], ['Vega 2', 'Vega 2'], ['Suisei', 'Suisei (spacecraft)'], ['Sakigake', 'Sakigake']])],
}
# Wikipedia articles of the objects on the map (same check as above)
WIKI = {
    'mercury': 'Mercury (planet)', 'venus': 'Venus', 'earth': 'Earth', 'mars': 'Mars', 'jupiter': 'Jupiter', 'saturn': 'Saturn', 'uranus': 'Uranus',
    'neptune': 'Neptune', 'ceres': 'Ceres (dwarf planet)', 'vesta': '4 Vesta', 'psyche16': '16 Psyche', 'apophis': '99942 Apophis',
    'didymos': '65803 Didymos', 'pluto': 'Pluto', 'eris': 'Eris (dwarf planet)', 'haumea': 'Haumea', 'makemake': 'Makemake', 'sedna': 'Sedna (dwarf planet)',
    'halley': "Halley's Comet", 'atlas3i': '3I/ATLAS',
    'voyager1': 'Voyager 1', 'voyager2': 'Voyager 2', 'pioneer10': 'Pioneer 10', 'pioneer11': 'Pioneer 11', 'newhorizons': 'New Horizons',
    'parker': 'Parker Solar Probe', 'solo': 'Solar Orbiter', 'stereoa': 'STEREO', 'soho': 'Solar and Heliospheric Observatory', 'bepi': 'BepiColombo',
    'juice': 'Jupiter Icy Moons Explorer', 'clipper': 'Europa Clipper', 'lucy': 'Lucy (spacecraft)', 'psyche': 'Psyche (spacecraft)',
    'hera': 'Hera (space mission)', 'apex': 'OSIRIS-REx', 'hayabusa2': 'Hayabusa2', 'juno': 'Juno (spacecraft)', 'jwst': 'James Webb Space Telescope',
    'euclid': 'Euclid (space telescope)', 'tess': 'Transiting Exoplanet Survey Satellite', 'gaia': 'Gaia (spacecraft)', 'spitzer': 'Spitzer Space Telescope',
    'kepler': 'Kepler space telescope', 'roadster': "Elon Musk's Tesla Roadster",
}
# words to search NASA's photo library with, where the name on the map would find the wrong things
PHOTO_Q = {'psyche16': 'asteroid Psyche', 'psyche': 'Psyche spacecraft', 'hera': 'Hera mission Didymos', 'apex': 'OSIRIS-REx',
           'lucy': 'Lucy spacecraft', 'juno': 'Juno spacecraft Jupiter', 'gaia': 'Gaia spacecraft', 'euclid': 'Euclid telescope',
           'tess': 'TESS satellite', 'roadster': 'Starman Tesla Roadster', 'atlas3i': '3I/ATLAS', 'stereoa': 'STEREO spacecraft',
           'hayabusa2': 'Hayabusa2', 'sedna': 'Sedna dwarf planet', 'eris': 'Eris dwarf planet', 'ceres': 'Ceres Dawn', 'vesta': 'Vesta Dawn'}


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


_NUM = r'([-+]?\d+\.?\d*(?:[EeDd][-+]?\d+)?)'
_STATE = re.compile(r'(\d+\.\d+)\s*=\s*A\.D\.[^\n]*\n\s*X\s*=\s*' + _NUM + r'\s*Y\s*=\s*' + _NUM + r'\s*Z\s*=\s*' + _NUM
                    + r'\s*\n\s*VX\s*=\s*' + _NUM + r'\s*VY\s*=\s*' + _NUM + r'\s*VZ\s*=\s*' + _NUM)


def parse_vectors(text):
    """(name Horizons gave, [[jd, x, y, z, vx, vy, vz], ...]) from a Horizons vector table (AU, AU/day)."""
    name = (re.search(r'Target body name:\s*(.*?)\s*\{', text) or re.search(r'Target body name:\s*([^\n]*)', text))
    rows = []
    if '$$SOE' in text and '$$EOE' in text:
        body = text.split('$$SOE')[1].split('$$EOE')[0]
        # Horizons' own layout: a line "JD = A.D. date", then "X = .. Y = .. Z = ..", then "VX= .. VY= .. VZ= ..".
        # (Its comma-separated layout stopped carrying the numbers in 2026, so the labelled one is read.)
        for m in _STATE.finditer(body):
            rows.append([float(m.group(1))] + [float(m.group(k)) for k in range(2, 8)])
        if not rows:                                            # the comma-separated layout, if a reply ever comes that way
            for ln in body.strip().splitlines():
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
                      'STEP_SIZE': f"'{step}'", 'VEC_TABLE': '2', 'OUT_UNITS': "'AU-D'", 'VEC_CORR': 'NONE'})
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
             'wiki': WIKI.get(key, ''), 'q': PHOTO_Q.get(key, re.sub(r'\s*\(.*\)', '', name)),
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
