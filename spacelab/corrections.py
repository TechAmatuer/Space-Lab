"""What we got wrong (idea 39 of the plan): a public record of mistakes found in Space Lab's own work and how they were fixed.

A result is only as trustworthy as the way its mistakes are handled. Each entry says when the mistake was found, what was
wrong, what it affected (and whether anything wrong reached the public page or a science archive), how it was found, what
changed, and the code change that fixed it. Newest first. Add an entry whenever a fix changes something a visitor could
have seen or relied on; keep it plain and specific, and never remove one.
"""

# date found, area, what was wrong, what it affected, how it was found, what changed, commit
CORRECTIONS = [
    ('2026-11-10', 'Solar system',
     'The new list of spacecraft around Earth described the Swift observatory as working. It re-entered the atmosphere on 4 November 2026, six days earlier.',
     'The Earth card of the public Solar System tab, for a few hours.',
     'Swift had no current orbit data when the Earth-orbit view was being built.',
     'The entry now says what happened; spacecraft lists are checked against current orbit data.', '0928a00'),
    ('2026-11-10', '3D universe',
     'In the new high-quality mode the map drew no galaxies, only its rings and labels.',
     'The public 3D map showed no galaxies for about 40 minutes.',
     'Looking at the page after the update; the automatic tests have no graphics card, so they could not see it.',
     'Fixed, and the page test now reads the drawing code for this kind of mistake.', 'dd84172'),
    ('2026-11-10', 'Solar system',
     'The first daily run placed no planet or spacecraft: NASA/JPL Horizons had stopped putting the numbers in the format the job asked for.',
     'Nothing public: the job refused to publish an empty map.',
     'The job\'s own check (fewer than 10 objects means something is wrong).',
     'It reads Horizons\' other format; all 46 objects were checked one by one against the live service.', '901235b'),
    ('2026-11-10', 'Catalogue checks',
     'When the VizieR catalogue service at Harvard was down, it sent tidy empty pages, and the software read them as "no known variable star here" and "no galaxies".',
     '17 stars were briefly listed (privately) as possible new variable stars without a real catalogue check; the public 3D map was replaced by an empty file for a few minutes.',
     'Suspiciously many candidates appeared, and the map stopped loading.',
     'An answer must now name the catalogue it came from, and a download must be nearly complete before it replaces anything. Local copies of the catalogues and a second working mirror were added, and the 17 stars were checked again.', '4a10cce'),
    ('2026-11-10', 'Variable stars',
     'Two neighbouring stars showed the same 6.4422-day period. TESS pixels are 21 arcseconds wide (an arcsecond is a tiny angle on the sky), so one star\'s light shows up in both.',
     'Nothing public: both were unreviewed candidates.',
     'Reading the candidate list.',
     'A signal shared with a nearby star is now flagged on both; at most one of them is the real variable.', 'f00803d'),
    ('2026-11-10', 'Old plates (2026 ED138)',
     'Plate times were 236 seconds late: when a plate\'s date was corrected by one day, a solar day was added where a sidereal day was needed (one turn of the Earth measured against the stars, which is 236 seconds shorter).',
     'The measured positions of 2026 ED138 on four plates. Nothing had been sent to the Minor Planet Center.',
     'A timing check that compares where the asteroid should be with where it was measured.',
     'Plate times follow the stars. The rerun will confirm it.', 'b58a07c'),
    ('2026-11-10', 'Old plates (2026 ED138)',
     'Plates reported as new finds were already at the Minor Planet Center, under the code of the same observatory (La Silla, which has two codes).',
     'Nothing was sent. The candidates were withdrawn and used as accuracy tests instead: they came within 0.10-0.24 arcseconds of the published positions.',
     'Comparing with the Minor Planet Center\'s list before anything was sent.',
     'Both codes of each observatory are treated as one; waiting candidates are re-checked on every run.', 'd9d4d94'),
    ('2026-11-10', 'Old plates',
     'When ESA\'s Gaia star catalogue service was down (error 503), the software read it as "no stars found".',
     'Plate measurements that day would have had no reference stars.',
     'The plate job\'s log.',
     'A copy of Gaia at NASA/IPAC (IRSA) is used instead, and an empty star list now counts as a failure.', '7cb402b'),
    ('2026-11-10', 'Old plates',
     'A faint detection on a 1984 plate (only 5 times stronger than the background noise) was probably noise. The local AI model called it real.',
     'Nothing sent; the candidate waits for a person.',
     'Measuring empty sky elsewhere on the same plate, to see how strong noise alone gets there (a noise control).',
     'Every plate detection now gets a noise control and is labelled weak when it does not stand out from it. AI verdicts were never used to decide, and still are not.', '01344ad'),
    ('2026-11-10', 'Space radar',
     'The same news story appeared several times: only 1 of 8 repeats was being caught.',
     'The public news list.',
     'Reading the list.',
     'The rule for "same story" was tuned again on the real list, and 10 repeats were merged.', 'd9d4d94'),
    ('2026-11-10', '3D universe',
     'Galaxies near the Milky Way were placed two to three times too far away: their distances came from redshifts measured against the cosmic microwave background, and in that frame our own 600 km/s motion is mixed into every nearby galaxy\'s speed.',
     'The public 3D map, for galaxies within about 50 million light-years (for example M81 and the Sombrero Galaxy).',
     'Comparing famous galaxies with their published distances.',
     'The map now uses a model of how galaxies flow near home, and measured distances (from the Cosmicflows-4 and Local Volume catalogues) wherever they exist.', 'd407860'),
]


def public(repo=''):
    """The list for the public page: newest first, with a link to the change that fixed each mistake when the repository is known."""
    out = []
    for d, area, wrong, effect, found, fix, sha in CORRECTIONS:
        out.append({'date': d, 'area': area, 'wrong': wrong, 'effect': effect, 'found': found, 'fix': fix, 'sha': sha,
                    'url': f'https://github.com/{repo}/commit/{sha}' if repo and '/' in repo else None})
    return sorted(out, key=lambda x: x['date'], reverse=True)
