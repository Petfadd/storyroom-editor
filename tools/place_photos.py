"""Place person photographs into a documentary script, driven only by the narration.

The rule this enforces is the production's own: a picture of a person appears while
the narrator names them, disappears when the next sentence names somebody else, and
comes back the next time their name comes back. Nothing here invents a mention, moves
a cue, or decides who is looking at the camera. It reads the uploaded SRT and the
production script, finds each time a name is spoken, and writes the Beats that show
that person's photograph for exactly those words.

Why this is not a one-line script edit
  A documentary Beat covers a span of narration, and a name is usually mentioned in
  the middle of it. Showing a photograph therefore means splitting that span into up
  to three Beats - the footage before the name, the photograph over the name, the
  footage after it - without moving a single frame of the narration or the footage.
  A split inherits the footage window it came from, advancing the source IN by the
  same amount the narration advanced, so the picture a Beat shows is unmoved.

  Two mentions of one person that sit close together would otherwise produce a
  photograph, a sliver of footage, and the photograph again - a flash. Mentions are
  therefore visited in narration order and merged when the footage between them is
  shorter than MIN_GAP_MS, which is the "do not jump in when it is not needed" rule.

    python tools/place_photos.py --project projects/<id> --plan
    python tools/place_photos.py --project projects/<id> --write script.txt \
        --photo "Maliyah Bass=Maliyah_Bass_hold.mp4" ...

`--plan` prints every photograph and the words it would cover, without writing
anything. Read that list before `--write`; the plan is the review.
"""
import argparse
import json
import re
import sys
from pathlib import Path

CLOCK = r'\d{2}:\d{2}:\d{2}[,.]\d{3}'

# A person is matched by every spelling the script and the SRT actually use. The
# production guide records that the SRT misspells three of these names, and a
# misspelling that goes unmatched is a name the narrator says with no picture.
PEOPLE = {
    'Maliyah Bass': (r'\b(?:Maliyah|Malia|Malaya|Maliya)\b', 'victim'),
    'Sahara Ervin': (r'\bSahara\b|\bErvin\b|\bIrvin\b|\bIrving\b', 'mother'),
    'Travion Thompson': (r'\b(?:Travion|Traevion|Travyon|Travi\s*on)\b|\bThompson\b', 'suspect'),
    'Rosalie Jimerson': (r'\bRosalie\b|\bJimerson\b', 'grandmother'),
    'Octavius Bass': (r'\bOctavi(?:ous|us)\b', 'father'),
    'Angel Harris': (r'\bAngel\s+Harris\b', 'mother of the suspect'),
}

# Footage between two mentions of one person shorter than this is not worth a cut;
# the photograph simply stays up. Below about half a second the picture would flash.
MIN_GAP_MS = 700
# A still is a held beat, not a slide. Past this it stops reading as a portrait and
# starts looking like the picture has frozen, so the coverage goes back to moving
# footage and the name is still spoken over the person's own clip.
MAX_PHOTO_MS = 9000
# A photograph is held at least this long so a face can be read. A mention shorter
# than this is not lengthened past the end of its Beat, because the picture must not
# stay on screen after the narrator has moved on to somebody else.
MIN_HOLD_MS = 1200
# Empty footage between two windows of one hold clip. Windows may not touch, because
# two Beats that share one source interval are an unapproved overlap.
WINDOW_GAP_MS = 1000


def clock_ms(value):
    match = re.fullmatch(CLOCK, value.strip())
    if not match:
        raise ValueError(f'Expected HH:MM:SS,mmm, got {value!r}')
    h, m, rest = value.split(':')
    s, ms = re.split(r'[,.]', rest)
    return ((int(h) * 60 + int(m)) * 60 + int(s)) * 1000 + int(ms)


def ms_clock(value):
    h, rem = divmod(int(round(value)), 3600000)
    m, rem = divmod(rem, 60000)
    s, ms = divmod(rem, 1000)
    return f'{h:02}:{m:02}:{s:02},{ms:03}'


def parse_beats(text):
    heads = list(re.finditer(r'^Beat:\s*(\d+)\s*$', text, re.M))
    blocks = []
    for i, head in enumerate(heads):
        body = text[head.end():heads[i + 1].start() if i + 1 < len(heads) else len(text)]
        body = re.sub(r'\n-{10,}\s*$', '', body).strip('\n')
        blocks.append({'number': int(head.group(1)), 'body': body})
    return blocks


def field(body, name, required=True):
    found = re.findall(r'^' + re.escape(name) + r':\s*([^\r\n]*)$', body, re.I | re.M)
    if len(found) > 1 or (required and (not found or not found[0].strip())):
        raise ValueError(f'Beat has {len(found)} {name} fields; exactly one is required.')
    return found[0].strip() if found else ''


def span_of(value):
    # The supplied production script writes an optional "(Duration: N sec)" after
    # the span; it is a restatement of the same two timecodes, not part of them.
    match = re.match(r'\s*(' + CLOCK + r')\s*(?:-->|→|->)\s*(' + CLOCK + r')', value)
    return (clock_ms(match.group(1)), clock_ms(match.group(2))) if match else None


def beat_span(body):
    """Narration clock and master clock a Beat covers, or None for a sound bite."""
    if field(body, 'Type').upper() not in ('VOICE OVER', 'NARRATION (VOICE OVER)'):
        return None
    clock = field(body, 'Narration Clock') if field(body, 'Narration Clock', False) else field(body, 'VO Clock')
    clock = re.sub(r'^\s*(?:RESUMES|RESUMING|PLAYING|CONTINUES)\s+(?:at\s+)?', '', clock, flags=re.I)
    narration = span_of(clock)
    if not narration:
        raise ValueError(f'Narration Clock is not a span: {clock!r}')
    master = span_of(field(body, 'Master timeline', False))
    if not master:
        raise ValueError('A narration Beat needs a Master timeline span.')
    return {'narration': narration, 'master': master}


POSSESSIVE = re.compile(r"^\s*(?:'s|\u2019s|s')\b")


def _subject(hits, text):
    """Who a sentence naming several people is about.

    The first name is the subject unless it is only there as a possessive - "Thompson's
    mother Angel Harris" is Angel's line, and showing Thompson would put one person
    under another person's words.
    """
    def at(name):
        m = re.search(PEOPLE[name][0], text, re.I)
        return m.start() if m else 10 ** 9
    ordered = sorted(hits, key=at)
    head = ordered[0]
    m = re.search(PEOPLE[head][0], text, re.I)
    # "Thompson's mother" - the name is a possessive pointing at somebody else.
    if m and POSSESSIVE.search(text[m.end():]):
        return ordered[1] if len(ordered) > 1 else None
    return head


def mentions(blocks, people):
    """Every cue that names exactly one person from PEOPLE, in script order.

    A cue that names two people cannot become a photograph: the picture would have to
    show one of them while the other is spoken, and the rule is that the person named
    is the person seen. Those cues are reported and left as footage.
    """
    found, ambiguous = [], []
    previous_name = None
    for cue in blocks:
        hits = [name for name, (pattern, _) in people.items()
                if re.search(pattern, cue.get('text', ''), re.I)]
        if len(hits) == 1:
            previous_name = hits[0]
            found.append({'name': hits[0], 'cue': int(cue['number']),
                          'start': int(cue['start_ms']), 'end': int(cue['end_ms']),
                          'text': cue.get('text', '')})
        elif len(hits) > 1:
            # "her boyfriend Travion Thompson is about to tell cameras... Thompson
            # says Malia knew her ABCs" names two people. A still cannot show both.
            # Carrying the name from the previous cue put Maliyah's photograph under
            # a sentence whose subject is Travion - the one thing the picture must
            # never do. The subject of these sentences is the person named FIRST, so
            # that is who the still shows. A cue that leads with neither stays as
            # footage and is reported.
            text = cue.get('text', '')
            # The subject is whoever the sentence opens on, not whoever is named
            # most often. "Thompson's mother Angel Harris is about to tell you..."
            # opens on Thompson but is Angel's story: the possessive is a pointer to
            # a different person, so that cue takes the still of HER.
            subject = _subject(hits, text)
            if subject is None:
                ambiguous.append((cue.get('number'), hits, text))
                continue
            previous_name = subject
            found.append({'name': subject, 'cue': int(cue['number']),
                          'start': int(cue['start_ms']), 'end': int(cue['end_ms']),
                          'text': text, 'carried': True})
        elif not cue.get('text', '').strip():
            continue
    return found, ambiguous


def merge(found, min_gap_ms=MIN_GAP_MS, max_ms=MAX_PHOTO_MS):
    """Join mentions of one person that are separated by almost no footage."""
    out = []
    for item in sorted(found, key=lambda m: m['start']):
        previous = out[-1] if out else None
        if previous and previous['name'] == item['name'] and item['start'] - previous['end'] <= min_gap_ms:
            previous['end'] = max(previous['end'], item['end'])
            previous['cues'].append(item['cue'])
            continue
        out.append({'name': item['name'], 'start': item['start'],
                    'end': min(item['end'], item['start'] + max_ms),
                    'cues': [item['cue']]})
    # A window that ran long is cut back to the held beat; the rest of the passage
    # plays on footage.
    for w in out:
        w['end'] = min(w['end'], w['start'] + max_ms)
    return out


def words_between(blocks, t0, t1):
    """The narration actually spoken between two clock marks, from the SRT itself."""
    parts = [b.get('text', '').strip() for b in blocks
             if int(b['start_ms']) >= t0 and int(b['end_ms']) <= t1]
    return ' '.join(p for p in parts if p)


def build_plan(beat_blocks, photos, narration_blocks, people=PEOPLE):
    """Which Beat splits, and where, so no mention is ever dropped.

    A mention can straddle two Beats. Dropping it would leave the narrator saying a
    name over footage of somebody else, which is the one thing this must not do, so a
    straddling mention is assigned to the Beat that contains its start and clipped to
    that Beat's end. The photograph therefore shows for the part of the name spoken
    inside the Beat it belongs to, and the neighbouring Beat is untouched.
    """
    found, ambiguous = mentions(narration_blocks, people)
    windows = merge(found)
    missing = sorted({w['name'] for w in windows} - set(photos))
    plan, unplaced = [], []
    for block in beat_blocks:
        span = beat_span(block['body'])
        if span is None:
            continue
        start, end = span['narration']
        inside = []
        for w in windows:
            if not (w['start'] < end and w['end'] > start):
                continue
            clipped = dict(w, start=max(w['start'], start), end=min(w['end'], end))
            if clipped['end'] <= clipped['start']:
                unplaced.append({'name': w['name'], 'beat': block['number']})
                continue
            inside.append(clipped)
        if inside:
            plan.append({'block': block, 'span': span, 'photos': inside})
    return plan, ambiguous, missing, unplaced


def render(plan, beat_blocks, photos, people=PEOPLE, narration_blocks=None):
    """Emit the whole script with each photograph Beat inserted in place."""
    by_number = {p['block']['number']: p for p in plan}
    narration_blocks = narration_blocks or []
    pieces, used = [], {}
    for block in beat_blocks:
        entry = by_number.get(block['number'])
        if not entry:
            pieces.append({'body': block['body'], 'kind': 'unchanged'})
            continue
        start, end = entry['span']['narration']
        master_in = entry['span']['master'][0]
        source = field(block['body'], 'Full source')
        source_in = clock_ms(field(block['body'], 'Source IN'))
        notes = {key: field(block['body'], key, False) for key in
                 ('Cut action', 'Visual direction', 'On-screen caption', 'Continuity note')}
        cursor = start
        for photo in entry['photos']:
            if photo['name'] not in photos:
                continue
            if photo['start'] > cursor:
                pieces.append(make_footage(cursor, photo['start'], start, master_in, source,
                                           source_in, notes, narration_blocks))
            hold_end = min(max(photo['end'], photo['start'] + MIN_HOLD_MS), end)
            pieces.append(make_photo(photo['start'], hold_end, master_in, start, photo,
                                     photos, people, used, narration_blocks))
            cursor = hold_end
        if cursor < end:
            pieces.append(make_footage(cursor, end, start, master_in, source,
                                       source_in, notes, narration_blocks))
    lines = []
    for i, piece in enumerate(pieces, 1):
        if piece['kind'] == 'unchanged':
            lines.append(f"Beat: {i:03d}\n\n{piece['body']}\n")
            continue
        body = piece['body'].replace('{{n}}', f'{i:03d}')
        lines.append(f"Beat: {i:03d}\n\n{body}\n")
    return '\n'.join(lines), used


def make_footage(t0, t1, beat_start, master_in, source, source_in, notes,
                 narration_blocks):
    offset = t0 - beat_start
    return {'kind': 'footage', 'body': f"""Type: VOICE OVER

Master timeline: {ms_clock(master_in + offset)} --> {ms_clock(master_in + offset + (t1 - t0))}

Narration clock: RESUMES at {ms_clock(t0)} --> {ms_clock(t1)}

Narration state: RESUMING

Spoken narration: "{words_between(narration_blocks, t0, t1)}"

Full source: {source}

Source in: {ms_clock(source_in + offset)}

Source out: {ms_clock(source_in + offset + (t1 - t0))}

Footage audio: MUTED (0%)

Cut action: {notes['Cut action'] or 'CUT TO B-ROLL'}

Visual direction: {notes['Visual direction'] or 'Live footage under the narration.'}

On-screen caption: {notes['On-screen caption'] or 'None'}

Continuity note: {notes['Continuity note'] or 'Narration continues over muted footage.'}
"""}


def make_photo(t0, t1, master_in, beat_start, photo, photos, people, used,
               narration_blocks):
    name = photo['name']
    clip = photos[name]
    length = t1 - t0
    # Each mention takes its own window inside the one hold clip, laid end to end from
    # a running cursor so no two windows can ever overlap. A fixed stride is wrong:
    # one mention can be longer than the stride (a 4.0 s hold on a 3.8 s stride ran
    # into the next window and the build refused it as an unapproved overlap).
    clip_in = used.get(name, 0)
    used[name] = clip_in + length + WINDOW_GAP_MS
    offset = t0 - beat_start
    return {'kind': 'photo', 'body': f"""Type: VOICE OVER

Master timeline: {ms_clock(master_in + offset)} --> {ms_clock(master_in + offset + length)}

Narration clock: RESUMES at {ms_clock(t0)} --> {ms_clock(t1)}

Narration state: RESUMING

Spoken narration: "{words_between(narration_blocks, t0, t1)}"

Full source: {clip}

Source in: {ms_clock(clip_in)}

Source out: {ms_clock(clip_in + length)}

Footage audio: MUTED (0%)

Cut action: CUT TO THE PHOTOGRAPH

Visual direction: Photograph of {name}, held still, on screen only while the narrator says the name.

On-screen caption: {name} — {people[name][1]}

Continuity note: Narration drives this shot: {name} is named here, and the photograph returns whenever the name does.
"""}


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--project', required=True, help='Project folder, e.g. projects/<id>.')
    parser.add_argument('--photo', action='append', metavar='NAME=CLIP',
                        help='Person name (as in PEOPLE) mapped to their hold clip filename.')
    parser.add_argument('--plan', action='store_true', help='Print the plan; write nothing.')
    parser.add_argument('--write', metavar='PATH', help='Write the rewritten script here.')
    args = parser.parse_args()

    folder = Path(args.project)
    project = json.loads((folder / 'project.json').read_text())
    script = project['production_script']['text']
    blocks = (project.get('srt') or {}).get('blocks') or []
    if not blocks:
        raise SystemExit('This project has no uploaded SRT; the narration is what drives placement.')

    photos = {}
    for pair in args.photo or []:
        name, clip = pair.split('=', 1)
        if name.strip() not in PEOPLE:
            raise SystemExit(f'{name!r} is not one of the people this tool places: '
                             + ', '.join(PEOPLE))
        photos[name.strip()] = clip.strip()

    beat_blocks = parse_beats(script)
    plan, ambiguous, missing, unplaced = build_plan(beat_blocks, photos, blocks)

    total = 0
    print(f'{len(plan)} narration Beat(s) would be split for photographs.\n')
    for entry in plan:
        for photo in entry['photos']:
            length = min(max(photo['end'], photo['start'] + MIN_HOLD_MS),
                         entry['span']['narration'][1]) - photo['start']
            total += length
            print(f"  {photo['name']:18} {ms_clock(photo['start'])} -> {ms_clock(photo['end'])}"
                  f'  hold {length/1000:5.1f}s  cue(s) {photo["cues"]}')
            print(f"      \"{words_between(blocks, photo['start'], photo['end'])[:92]}\"")
    print(f'\n  total photograph time: {total/1000:.1f}s of {blocks[-1]["end_ms"]/1000:.1f}s narration')
    if ambiguous:
        print('\nCues naming two people stay as footage (one still cannot show both):')
        for number, names, text in ambiguous:
            print(f'  cue {number}: {", ".join(names)} — {text[:78]}')
    if missing:
        print('\nMentioned but no photograph supplied: ' + ', '.join(missing))
    if unplaced:
        print('\nMentions crossing a Beat boundary stay as footage:')
        for item in unplaced:
            print(f"  {item['name']} in Beat {item['beat']:03d}")
    if args.plan or not args.write:
        if not args.write:
            print('\nNothing written. Pass --write PATH to produce the new script.')
        return 0
    if not photos:
        raise SystemExit('No --photo mapping was given, so no photograph can be placed.')
    text, used = render(plan, beat_blocks, photos, narration_blocks=blocks)
    Path(args.write).write_text(text, encoding='utf-8')
    print(f'\nWrote {args.write}')
    for name, count in sorted(used.items()):
        print(f'  {name}: {count} mention window(s) inside {photos[name]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
