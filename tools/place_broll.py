"""Match b-roll to the narration, so the picture follows what is being said.

Three kinds of picture feed this production, and the narration decides which one is
on screen at any moment:

  photograph  a named person appears while their name is spoken   (tools/place_photos.py)
  footage     a person speaking, or the scene the sentence is about
  b-roll      a place, an object or an atmosphere - police cars, the bayou, the
              memorial, a courthouse, an apartment stairway - taken from the footage
              already uploaded

This tool reads the SRT, works out what each narration cue is about, and picks a
b-roll window whose subject matches. It never invents a source: every window comes
from data/broll_catalog.json, which was built by extracting frames from the real
files and looking at them.

What it will not do
  * cut to b-roll while a person is speaking, or while a person is being named.
    A sound bite shows its speaker; a name shows that person's photograph.
  * use the same frames twice. A long shot is spent from its head forward, so three
    consecutive cues can share one 20-second take and still never overlap.
  * use a window marked `verified: false`. An unchecked guess about what is on
    screen is worse than leaving footage up.
  * use a shot whose `kind` is `speaker`, `people` or `graphic`. A press
    conference, a suspect walking past cars, and a news map are not b-roll: the
    first two are people who must only appear when they are the subject, and the
    third is a graphic, not a place.

    python tools/place_broll.py --project projects/<id> --plan
    python tools/place_broll.py --project projects/<id> --plan --min-score 3
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from place_photos import PEOPLE  # one definition of who is named, shared with the photo pass

CATALOG = Path(__file__).resolve().parent.parent / 'data' / 'broll_catalog.json'

# A cue is matched to a b-roll tag by the words the narrator actually uses. These
# patterns are written against this production's own transcript, not a generic
# keyword list: "bayou" is said eight times and is the spine of the geography.
#
# The second number is the weight. A weight of 3 means the word alone decides the
# picture ("bayou", "court", "memorial"). A weight of 1 means the word merely
# suggests it and must not win on its own.
TOPICS = {
    'bayou':      (r'\bbayou\b|\bbra[ie]s\b|\bstorm drain\b|\bfloated\b|\bfloat', 3),
    'water':      (r'\bwater\b|\bwater levels\b|\bdrained\b|\bmurky\b|\bpool\b', 2),
    'search':     (r'\bsearch(?:ed|ing)?\b|\bsearchers?\b|\bvolunteers?\b|'
                   r'\bequu\s*search\b|\bequu\b|\bjogger\b', 2),
    'apartments': (r'\bapartments?\b|\bcomplex\b|\bsunset crossing\b|\bbeech\s*nut\b|'
                   r'\bplayground\b|\bwalkway\b|\bneighbou?rs?\b', 2),
    'police':     (r'\bpolice\b|\bofficers?\b|\bHPD\b|\bprecinct\b|\bdetectives?\b', 2),
    'memorial':   (r'\bmemorial\b|\bvigil\b|\bcandles?\b|\bpray(?:er|ed)?\b|'
                   r'\bflowers?\b', 3),
    'teddy':      (r'\bteddy bears?\b|\bballoons?\b', 3),
    'court':      (r'\bcourt(?:room|house)?\b|\bjudge\b|\bhearing\b|\bplea deal\b|'
                   r'\bpleaded\b|\bsentenc|\bprosecutors?\b|\battorney\b|\bdefense\b', 3),
    'jail':       (r'\barrested\b|\bjail\b|\bbond\b|\bcustody\b|'
                   r'\bconvictions?\b|\bprobation\b|\bparole\b', 2),
    'bridge':     (r'\bbridge\b|\bspillway\b|\bculvert\b', 2),
    'street':     (r'\bstreets?\b|\broads?\b|\bdusk\b', 1),
    'crowd':      (r'\bgathered\b|\bneighbou?rs\b|\bcrowd\b|\bcommunity\b', 2),
}

# Footage and photographs are never replaced by b-roll on these cues.
# "here is", "listen to", "is about to tell" introduce a sound bite: the speaker
# must be on screen for those words, not a place.
KEEP_FOOTAGE = re.compile(r'\b(?:says|said|told|tell|tells|speaks?|spoke|describ|remembers?|'
                          r'asks?|asked|testif|confess|admit|listen to|hear from|here is|here are|'
                          r'about to (?:tell|relay|explain)|explain)', re.I)

# A b-roll cut shorter than this would flash. The cue stays as footage instead.
MIN_SHOT_S = 0.9

# Cues that mention a subject but are not about the place. "phones across Houston
# Police / Precinct 5" is a caption fragment that happens to contain "police": the
# picture there is the amber alert, not an arrest. These stay as footage.
#
# The list lives in the catalog, per project, because cue numbers mean nothing
# outside the SRT they were read from. Hard-coding them in this module silently
# dropped cue 3 of any other production.
EXCLUSIONS = 'never_broll'
DEFAULT_EXCLUSIONS = ()

# Kinds that are never b-roll, whatever the tags say.
NOT_BROLL = {'speaker', 'people', 'graphic'}


def load_catalog(path=CATALOG):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def score_cue(text):
    """Which b-roll subjects a narration cue is about, strongest first."""
    scores = {}
    for tag, (pattern, weight) in TOPICS.items():
        hits = len(re.findall(pattern, text, re.I))
        if hits:
            scores[tag] = hits * weight
    return sorted(scores.items(), key=lambda item: -item[1])


def named(text):
    """The people this cue names. A cue that names somebody is never b-roll."""
    return [name for name, (pattern, _) in PEOPLE.items()
            if re.search(pattern, text, re.I)]


def spoken(blocks):
    """Every cue the narrator actually speaks, with its clock."""
    out = []
    for cue in blocks:
        text = (cue.get('text') or '').strip()
        if not text:
            continue
        out.append({'number': int(cue['number']), 'start': int(cue['start_ms']),
                    'end': int(cue['end_ms']), 'text': text,
                    'topics': score_cue(text), 'named': named(text)})
    return out


# Words that make a description actually about a subject, rather than merely
# adjacent to it.
SUBJECT_WORDS = {
    'court': ('court', 'judge', 'bench', 'gallery'),
    'search': ('search', 'searcher', 'vest'),
    'searcher': ('search', 'searcher', 'vest'),
    'water': ('water', 'bayou', 'flood', 'bank'),
    'bayou': ('bayou', 'water', 'bank'),
    'memorial': ('memorial', 'balloon', 'teddy', 'stuffed', 'portrait', 'frame'),
    'vigil': ('vigil', 'candle', 'crowd', 'memorial'),
    'arrest': ('arrest', 'detain', 'handcuff'),
    'jail': ('jail', 'cell', 'orange', 'inmate', 'custody'),
    'apartments': ('apartment', 'block', 'complex', 'walkway'),
    'police': ('police', 'patrol', 'officer', 'hpd', 'deputy', 'deputies'),
    'crowd': ('crowd', 'people', 'shirt', 'sign'),
    'playground': ('playground', 'swing', 'slide'),
    'pool': ('pool', 'water'),
    'dog': ('dog', 'k9'),
}


RECORD = re.compile(r'\b(?:conviction|convictions|probation|record|records|'
                    r'parole|plea|pleaded|indict\w*|charge[sd]?)\b', re.I)


def _described(shot, tag):
    """True when the shot's own description is about `tag`."""
    words = SUBJECT_WORDS.get(tag)
    if not words:
        return True
    text = (shot.get('what') or '').lower()
    return any(w in text for w in words)


def plan_broll(catalog, cues, min_score=2, keep=None, kinds=NOT_BROLL,
               exclude=DEFAULT_EXCLUSIONS):
    """Assign b-roll to narration cues, spending each shot from its head forward.

    A long take is not a single opportunity. `S10` is 97 seconds of one flood with
    thirteen shots in it, and a 2.7-second cue only needs part of one. Each shot
    therefore keeps a cursor: the next cue that wants it takes the next slice. No
    frame is ever used twice.
    """
    exclude = set(exclude) or set(catalog.get(EXCLUSIONS) or ())
    shots = [s for s in catalog['shots']
             if s.get('verified', True) and s.get('kind', 'place') not in kinds]
    keep = keep or KEEP_FOOTAGE
    cursor = {s['id']: s['in'] for s in shots}
    plan, skipped = [], []
    for cue in cues:
        if cue['number'] in exclude:
            skipped.append((cue['number'], 'cue is not about the place'))
            continue
        if not cue['topics'] or cue['topics'][0][1] < min_score:
            continue
        if cue['named']:
            # The narrator says a name: that person's photograph belongs here, and a
            # place would be a picture of somewhere while somebody is being named.
            skipped.append((cue['number'], 'cue names ' + ', '.join(cue['named'])))
            continue
        # A record search is not a jail. "five convictions since 2017 and a
        # probation never revoked" is a paper trail; cutting to a cell says he was
        # locked up, which is a different fact. Those lines stay on footage.
        if RECORD.search(cue['text']):
            skipped.append((cue['number'], 'cue is about a record, not a place'))
            continue
        if keep.search(cue['text']):
            skipped.append((cue['number'], 'cue is somebody speaking'))
            continue
        if len(cue['topics']) > 1 and abs(cue['topics'][0][1] - cue['topics'][1][1]) <= 1:
            # Two subjects score the same, so the sentence does not say which picture
            # belongs on it. A coin toss here puts the wrong picture behind the words.
            skipped.append((cue['number'], 'cue mixes two subjects equally'))
            continue
        wanted = [tag for tag, score in cue['topics'] if score >= min_score]
        duration = (cue['end'] - cue['start']) / 1000
        if duration < MIN_SHOT_S:
            skipped.append((cue['number'], 'cue too short to cut away from'))
            continue
        need = min(duration, 8.0)   # a long cue gets a long shot, but not an endless one
        pick = None
        for tag in wanted:
            # The shot must be about what the cue is about. Every shot carries a
            # plain description, and that sentence is the real test: a "path and
            # grass verge" cannot illustrate a drained pool however it was tagged,
            # and a jail cell cannot illustrate a probation record. Requiring the
            # description to name the subject is what keeps a near-miss shot off a
            # specific line instead of merely reducing the count of b-roll cuts.
            options = [s for s in shots
                       if tag in s['tags'] and s['out'] - cursor[s['id']] >= need
                       and _described(s, tag)]
            if not options:
                # No shot carries enough of what this cue is about. A near-miss puts
                # the wrong picture behind a specific line - a jail cell under a
                # probation record, a footpath under a drained pool. The narration
                # stays on footage instead.
                continue
            if options:
                # Prefer the shot that matches the most of what the cue is about,
                # then the one with the least left to spare so the long ambient
                # takes stay in reserve for the cues that need them.
                pick = min(options, key=lambda s: (-sum(1 for w in wanted
                                                        if w in s['tags']),
                                                    s['out'] - cursor[s['id']]))
                break
        if not pick:
            skipped.append((cue['number'], 'no unused b-roll for ' + '/'.join(wanted)))
            continue
        start = cursor[pick['id']]
        end = start + need
        cursor[pick['id']] = end
        window = dict(pick)
        window['in'], window['out'] = round(start, 3), round(end, 3)
        plan.append({'cue': cue['number'], 'start': cue['start'], 'end': cue['end'],
                     'text': cue['text'], 'shot': window, 'topics': wanted})
    return plan, skipped


def clock(ms):
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{ms:03d}'


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', required=True)
    parser.add_argument('--catalog', default=str(CATALOG))
    parser.add_argument('--min-score', type=int, default=2,
                        help='How strong a subject match must be (default 2).')
    parser.add_argument('--plan', action='store_true')
    args = parser.parse_args()
    project = json.loads((Path(args.project) / 'project.json').read_text())
    blocks = (project.get('srt') or {}).get('blocks') or []
    if not blocks:
        raise SystemExit('No uploaded SRT; the narration is what drives the match.')
    catalog = load_catalog(args.catalog)
    cues = spoken(blocks)
    plan, skipped = plan_broll(catalog, cues, args.min_score)
    total = sum((p['shot']['out'] - p['shot']['in']) for p in plan)
    print(f'{len(plan)} of {len(cues)} narration cue(s) matched to b-roll '
          f'(min score {args.min_score}).\n')
    for item in plan:
        shot = item['shot']
        length = shot['out'] - shot['in']
        print(f"  cue {item['cue']:3} {clock(item['start'])}  {shot['id']} {shot['source']} "
              f"{shot['in']:6.1f}-{shot['out']:6.1f}  {length:4.1f}s  [{','.join(item['topics'])}]")
        print(f"      shot: {shot['what']}")
        print(f"      said: \"{item['text'][:88]}\"")
    print(f'\n  total b-roll: {total:.1f}s of {sum((c["end"]-c["start"])/1000 for c in cues):.1f}s narration')
    if skipped:
        print('\nLeft as footage:')
        for number, why in skipped[:30]:
            print(f'  cue {number}: {why}')
        if len(skipped) > 30:
            print(f'  ... and {len(skipped)-30} more')
    return 0


if __name__ == '__main__':
    sys.exit(main())
