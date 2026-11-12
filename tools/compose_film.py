"""Compose a full narration-length film out of the three picture tiers.

The picture in this production is three things and nothing else:

  photograph  a named person, shown while the narrator says their name
  footage     a place from the footage already uploaded
  b-roll      a matching place or object, cut on the words that describe it

This tool takes the uploaded narration clock and SRT, works out which tier owns
every frame of the narration, and writes the Beat script that puts that picture on
screen. It decides nothing about identity or rights: names come from
place_photos.PEOPLE and windows come from data/broll_catalog.json, which was built
by looking at the actual frames.

UNIT. Everything inside this module is measured in whole output frames at FPS.
Milliseconds appear only on the way out, in render_script(). Mixing the two is how
a Beat ends up with 3466 ms of narration against 3467 ms of source, which the Beat
reader rejects for a one-millisecond rounding fault.

How the narration is divided
  Every claim (a photograph, a b-roll cut) marks the narration timeline. The
  timeline is cut at those marks, and each piece is given a tier:

      photograph  >  b-roll  >  footage

  A photograph wins because a name is being spoken. B-roll wins over footage because
  the narration is describing a place the film can actually show. Everything else is
  footage, drawn from the places in the catalog that b-roll did not spend, walking
  each source once so no frame is ever used twice.

    python tools/compose_film.py --project projects/<id> --out script.txt
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from place_broll import load_catalog, plan_broll, spoken
from place_photos import PEOPLE, mentions, merge

FPS = 30
MIN_SEGMENT = 12        # frames; anything shorter is folded into a neighbour
SOURCE_TAIL = 0.05     # s kept clear at the end of every file: no frame past the last
MIN_BEAT = 3            # frames; anything under this is a flash, not a shot
HOLD_GAP = 30           # frames of gap between two windows of one hold clip


def fr(ms):
    """Whole output frames for a millisecond mark."""
    return int(round(ms * FPS / 1000.0))


def ms(frames):
    """Milliseconds for a frame index, on the grid the renderer uses."""
    return int(round(frames * 1000.0 / FPS))


def sec(frames):
    return frames / float(FPS)


def clock(ms_value):
    ms_value = int(round(ms_value))
    h, rem = divmod(ms_value, 3600000)
    m, rem = divmod(rem, 60000)
    s, rem = divmod(rem, 1000)
    return f'{h:02d}:{m:02d}:{s:02d},{rem:03d}'


def claims_for(blocks, catalog, min_score=2, available=None):
    """Every claim on the narration timeline, in frames: photographs, then b-roll.

    `available` is the set of people the production actually has a hold clip for. A
    name with no picture is left as footage rather than pointed at a missing file -
    the narrator still says the name, the film just does not cut to a person it
    cannot show.
    """
    found, ambiguous = mentions(blocks, PEOPLE)
    if available is not None:
        found = [m for m in found if m['name'] in available]
    claims = []
    for win in merge(found):
        claims.append({'tier': 'photograph', 'start': fr(int(win['start'])),
                       'end': fr(int(win['end'])), 'name': win['name']})
    plan, skipped = plan_broll(catalog, spoken(blocks), min_score)
    for item in plan:
        got = int(round((item['shot']['out'] - item['shot']['in']) * 1000))
        claims.append({'tier': 'b-roll', 'start': fr(item['start']),
                       'end': fr(min(item['end'], item['start'] + got)),
                       'shot': item['shot']})
    return claims, ambiguous, plan, skipped


def split(claims, total_frames):
    """Cut the narration at every claim boundary and give each piece a tier."""
    marks = {0, total_frames}
    for c in claims:
        marks.add(max(0, min(total_frames, c['start'])))
        marks.add(max(0, min(total_frames, c['end'])))
    marks = sorted(marks)
    pieces = []
    for a, b in zip(marks, marks[1:]):
        if b <= a:
            continue
        live = [c for c in claims if c['start'] < b and c['end'] > a]
        tier, owner = 'footage', None
        for name in ('photograph', 'b-roll'):
            pick = [c for c in live if c['tier'] == name]
            if pick:
                tier = name
                owner = max(pick, key=lambda c: (min(c['end'], b) - max(c['start'], a),
                                                 -c['start']))
                break
        pieces.append({'start': a, 'end': b, 'tier': tier, 'owner': owner})
    return fold(pieces)


def same_picture(a, b):
    """Do two neighbouring pieces want the same picture on screen?"""
    if a['tier'] != b['tier'] or a['tier'] == 'footage':
        return False        # footage comes off a tape; each span is its own picture
    oa, ob = a['owner'], b['owner']
    if oa is None or ob is None:
        return False
    if a['tier'] == 'photograph':
        return oa.get('name') == ob.get('name')
    return oa.get('shot', {}).get('id') == ob.get('shot', {}).get('id')


def fold(pieces):
    """Join neighbours that want the same picture; never leave a one-frame cut."""
    out = []
    for p in pieces:
        if out and same_picture(out[-1], p):
            out[-1]['end'] = p['end']
            continue
        if p['end'] - p['start'] < MIN_SEGMENT and out:
            # Join the sliver to a neighbour BEFORE tiers are assigned, so the
            # neighbours stay adjacent on the narration clock. Creating a separate
            # piece here is what left a two-frame hole between two Beats later.
            out[-1]['end'] = p['end']
            # The merged piece may now span two owners; the longer claim keeps it.
            a, b = out[-1]['owner'], p['owner']
            if a is not None and b is not None:
                out[-1]['tier'] = a['tier']
            continue
        out.append(dict(p))
    return out


def footage_tape(catalog, spent, everything=False):
    """The frames of each shot that b-roll did not spend, in catalog order.

    With `everything`, every kind of shot is fair game. That is the last resort when
    the place shots run out: a speaker or a graphic is not b-roll, but it is real
    footage the production owns, and a stretch of narration has to show something.
    Either way every spent window is subtracted, whatever kind its shot is. Missing
    that subtraction is what let a b-roll cut and a footage span share the same
    seconds of one file.
    """
    tape = []
    by_id = {}
    for sid, a, b in spent:
        by_id.setdefault(sid, []).append((a, b))
    for shot in catalog['shots']:
        if not shot.get('verified', True):
            continue
        if not everything and shot.get('kind', 'place') != 'place':
            continue
        lo = int(round(shot['in'] * FPS))
        hi = int(shot['out'] * FPS)          # floor: never past the shot's OUT
        cursor = lo
        for a, b in sorted(by_id.get(shot['id'], [])):
            if a > cursor:
                tape.append({'shot': shot, 'start': cursor, 'end': a})
            cursor = max(cursor, b)
        if cursor < hi:
            tape.append({'shot': shot, 'start': cursor, 'end': hi})
    # Sorted by source and then by position, so drawing walks each file exactly once
    # from head to tail. Catalog order is shot order, not source order, and drawing it
    # unsorted doubled back and handed out frames that were already on screen.
    # A sliver is a rounding seam, not a shot. Anything under half a second on screen
    # is a flash, and the frames either side of it already cover the words.
    tape = [w for w in tape if w['end'] - w['start'] >= FPS // 2]
    tape.sort(key=lambda w: (w['shot']['source'], w['start']))
    return tape


def free_window(taken, length, limit):
    """The first gap of `length` frames in this source that no Beat has claimed."""
    cursor = 0
    for a, z in sorted(taken):
        if a - cursor >= length:
            return cursor
        cursor = max(cursor, z)
    if limit - cursor >= length:
        return cursor
    return None


def draw(tape, index, need):
    """Take exactly `need` frames, crossing tape windows when one cannot cover it."""
    taken = []
    while need > 0 and index < len(tape):
        piece = tape[index]
        if piece['end'] - piece['start'] <= 0:
            index += 1
            continue
        take = min(need, piece['end'] - piece['start'])
        taken.append({'shot': piece['shot'], 'start': piece['start'],
                      'end': piece['start'] + take})
        piece['start'] += take
        need -= take
    return taken, index


def apportion(total, weights):
    """Split `total` frames across spans by largest remainder. Sums to exactly total."""
    if not weights:
        return []
    if len(weights) == 1:
        return [total]
    pool = sum(weights)
    base = [total * w // pool for w in weights]
    left = total - sum(base)
    order = sorted(range(len(weights)), key=lambda i: -(total * weights[i] % pool))
    for i in order[:left]:
        base[i] += 1
    return base


def compose(project, catalog, min_score=2, hook=(), available=None):
    """Build the Beat script. `hook` is a list of (source, in_s, out_s, speaker, at)."""
    blocks = (project.get('srt') or {}).get('blocks') or []
    if not blocks:
        raise SystemExit('No uploaded SRT.')
    if available is None:
        # Whichever people have a hold clip among the uploaded assets.
        names = {(a.get('original_name') or a.get('name') or '') for a in project.get('assets', [])}
        available = {person for person in PEOPLE
                     if f"{person.replace(' ', '_')}_hold.mp4" in names}
    total = fr(max(int(b['end_ms']) for b in blocks))
    # Trim the catalog to what each file actually decodes. A container's declared
    # duration runs a frame or two past the last decodable frame, and a source window
    # built on the declared number names frames that do not exist.
    limits = {a.get('original_name') or a.get('name'): float(a['duration'])
              for a in project.get('assets', [])
              if a.get('kind') == 'video' and a.get('duration')}
    for shot in catalog['shots']:
        cap = limits.get(shot['source'])
        if cap:
            shot['out'] = min(float(shot['out']), cap - SOURCE_TAIL)
            if shot['out'] - float(shot['in']) <= 0:
                shot['verified'] = False
    claims, ambiguous, plan, skipped = claims_for(blocks, catalog, min_score, available)
    pieces = split(claims, total)

    # --- photographs and b-roll claim their source windows, in source frames ---
    offsets = {name: 0 for name in PEOPLE}
    hold_frames = {a['original_name'].replace('_hold.mp4', '').replace('_', ' '): fr(float(a['duration']) * 1000)
                   for a in project.get('assets', [])
                   if a.get('kind') == 'video' and (a.get('original_name') or '').endswith('_hold.mp4')}
    cursors = {c['shot']['id']: fr(c['shot']['in'] * 1000)
               for c in claims if c['tier'] == 'b-roll'}
    placed, spent = [], []
    for piece in pieces:
        length = piece['end'] - piece['start']
        item_end = piece['end']
        if piece['tier'] == 'photograph':
            name = piece['owner']['name']
            begin = offsets[name]
            # A hold clip is finite too. When it runs out, wrap to its head with a
            # fresh gap rather than naming frames past the file.
            cap = hold_frames.get(name, 1 << 30)
            if begin + length > cap:
                begin = 0
            offsets[name] = begin + length + HOLD_GAP
            placed.append(dict(piece, spans=[{'name': name, 'start': begin,
                                              'end': begin + length}]))
        elif piece['tier'] == 'b-roll':
            shot = piece['owner']['shot']
            begin = cursors[shot['id']]
            limit = int(shot['out'] * FPS)
            take = min(length, limit - begin)

            if take <= 0:
                # This shot has no frames left. The narration still has to show
                # something, so the piece becomes footage rather than borrowing
                # frames from the next shot, which is how the picture doubled back.
                placed.append(dict(piece, tier='footage', spans=[]))
                continue
            cursors[shot['id']] = begin + take
            spent.append((shot['id'], begin, begin + take))
            placed.append(dict(piece, end=piece['start'] + take,
                               spans=[{'shot': shot, 'start': begin,
                                       'end': begin + take}]))
            if take < length:
                # The window ended before the words did: the rest of the cue is
                # footage, starting exactly where the b-roll stopped. The original
                # end is read before `piece` is narrowed.
                rest = item_end - (piece['start'] + take)
                if rest >= MIN_BEAT:
                    placed.append({'start': piece['start'] + take, 'end': item_end,
                                   'tier': 'footage', 'owner': None, 'spans': []})
                else:
                    # A frame or two is not a shot. Give it to the b-roll itself by
                    # widening its window, which the catalog has room for or the take
                    # would already have been cut short.
                    placed[-1]['end'] = item_end
                    placed[-1]['spans'][-1]['end'] += rest
                    cursors[shot['id']] += rest
                    spent[-1] = (shot['id'], spent[-1][1], spent[-1][2] + rest)
        else:
            placed.append(dict(piece, spans=[]))
    # A b-roll piece that was cut short must not leave a hole in the narration: the
    # footage piece added above starts exactly where it stopped.
    placed.sort(key=lambda i: (i['start'], i['end']))
    covered = sum(i['end'] - i['start'] for i in placed)
    if covered != total:
        raise SystemExit(f'The plan covers {covered} of {total} narration frames; '
                         f'{total-covered} frames have no picture.')

    # --- footage fills what is left, never reusing a frame ---
    # `spent` already holds every b-roll window actually allocated above, including
    # the truncated ones, so the footage tape cannot hand those frames back out.
    tape = footage_tape(catalog, spent)
    index, drawn, short = 0, [], []
    for item in placed:
        if item['tier'] != 'footage':
            continue
        before = item['end'] - item['start']
        spans, index = draw(tape, index, before)
        got = sum(s['end'] - s['start'] for s in spans)
        if got < before:
            # draw() ran off the end of this tape. Record the shortfall so the
            # fallback below can fill exactly those frames, not redo the whole item.
            short.append((item, before - got))
        if spans:
            item['spans'] = spans
            drawn.extend((s['shot']['id'], s['start'], s['end']) for s in spans)
    if short:
        # The place shots are exhausted; fill the shortfall from every remaining
        # window. The first pass's draws are part of the ledger too, or this tape
        # would hand back frames that are already on screen earlier in the film.
        tape = footage_tape(catalog, spent + drawn, everything=True)
        index = 0
        for item, need in short:
            spans, index = draw(tape, index, need)
            if sum(s['end'] - s['start'] for s in spans) < need:
                raise SystemExit('No footage left for the last of the narration.')
            item['spans'] = item.get('spans', []) + spans
            drawn.extend((s['shot']['id'], s['start'], s['end']) for s in spans)

    # A piece shorter than MIN_SEGMENT would flash on screen. Give its words to the
    # picture before it: the narration still runs unbroken, and there is no two-frame
    # cut. Walking backwards means a short piece never leaves a hole behind it.
    # A piece shorter than one Beat is a flash, and the reader rejects anything under
    # a frame. Give its frames to the piece before it when that source window can
    # still grow; otherwise trim the b-roll before it by a frame or two, which makes
    # the b-roll simply shorter rather than dropping any narration.
    floor = max(MIN_SEGMENT, MIN_BEAT)
    changed = True
    while changed:
        changed = False
        i = len(placed) - 1
        while i > 0:
            item = placed[i]
            if item['end'] - item['start'] >= floor:
                i -= 1
                continue
            previous = placed[i - 1]
            grow = item['end'] - item['start']
            if previous['tier'] == 'footage' and previous['spans']:
                # Footage is drawn from a tape later, so its first span is not final.
                # Shifting its end is exact and needs no window check.
                prev_last = previous['spans'][-1]
                prev_last['end'] += grow
                previous['end'] += grow
                placed.pop(i)
            elif previous['spans'] and previous['tier'] in ('footage', 'b-roll'):
                last = previous['spans'][-1]
                room = int(last['shot']['out'] * FPS) - last['end']
                if room >= grow:
                    last['end'] += grow
                    previous['end'] += grow
                    placed.pop(i)
                elif last['end'] - last['start'] > floor:
                    cut = min(floor, last['end'] - last['start'] - floor)
                    last['end'] -= cut
                    previous['end'] -= cut
                else:
                    # Give the fragment to the following piece so the narration clock
                    # stays exact. Only drop when there is nothing after it.
                    if i + 1 < len(placed):
                        placed[i + 1]['start'] = item['start']
                    placed.pop(i)
            else:
                # No growable predecessor (a photograph, or a bare piece). Move the
                # fragment's frames into the previous piece's clock directly.
                previous['end'] += grow
                placed.pop(i)
            changed = True
            break
        if not changed:
            break

    # --- one Beat per span, then the sound bites are threaded in at their pause ---
    beats = []

    start_frames = 0
    for item in placed:
        spans = item['spans']
        shares = apportion(item['end'] - item['start'],
                           [s['end'] - s['start'] for s in spans])
        for span, frames in zip(spans, shares):
            if frames <= 0:
                continue
            if item['tier'] == 'photograph':
                source = f"{span['name'].replace(' ', '_')}_hold.mp4"
                caption = span['name']
                direction = f"PHOTOGRAPH: {span['name']}, held still."
            else:
                source = f"{span['shot']['source']}.mp4"
                caption = 'None'
                direction = f'{item["tier"].upper()}: {span["shot"]["what"]}'
            # A frame boundary in milliseconds. Both sides use the same conversion,
            # so source span and narration span are equal by construction, and the
            # running clock keeps the exact sum instead of accumulating rounding.
            start_ms = ms(start_frames)
            length_ms = ms(start_frames + frames) - start_ms
            if length_ms <= 0 or ms(span['start']) + length_ms <= ms(span['start']):
                continue                # a zero-frame seam is not a cut
            beats.append({'kind': 'narration', 'source': source,
                          'src_in_ms': ms(span['start']),
                          'src_out_ms': ms(span['start']) + length_ms,
                          'narr_in_ms': start_ms, 'narr_out_ms': start_ms + length_ms,
                          'caption': caption, 'direction': direction,
                          'tier': item['tier']})
            start_frames += frames
    # A sound bite pauses the narration: it sits at the narration mark named by its
    # `at_ms`, and the master clock continues after it. Beats are numbered in master
    # order because the Beat reader requires them in order.
    # The reader requires a bite's pause to equal the narration OUT of the Beat that
    # immediately precedes it. The composer trims a frame or two out of the plan, so
    # a cue's nominal end can fall past the last Beat. Snap to the nearest boundary
    # that no earlier bite has already taken.
    ends = sorted({x['narr_out_ms'] for x in beats if x['kind'] == 'narration'})
    used = set()
    for source, a, b, speaker, at in hook:
        free = [m for m in ends if m not in used and m > 0] or ends
        at = min(free, key=lambda m: abs(m - at))
        used.add(at)
        beats.append({'kind': 'sound_bite', 'source': source,
                      'src_in_ms': int(round(a * 1000)),
                      'src_out_ms': int(round(b * 1000)),
                      'narr_in_ms': None, 'narr_out_ms': None, 'at_ms': at,
                      'caption': speaker, 'direction': f'{speaker} on camera.',
                      'speaker': speaker, 'tier': 'sound_bite'})
    # Hard clamp: whatever the tape arithmetic did, no Beat may name a source range
    # that runs past the end of its file. Trimming here keeps every other guarantee
    # intact, because the narration length is what the renderer matches against.
    # `limits` are milliseconds; every Beat window already comes from a source that
    # was capped in frames, so this only catches arithmetic that escaped the tape.
    limits = {a.get('original_name') or a.get('name'):
              ms(fr((float(a['duration']) - SOURCE_TAIL) * 1000))
              for a in project.get('assets', []) if a.get('kind') == 'video'}
    for b in beats:
        limit = limits.get(b['source'])
        if limit and b['src_out_ms'] > limit:
            raise SystemExit(f"Beat {b.get('label','?')} runs past the end of {b['source']}: "
                             f"{b['src_out_ms']} ms > {limit} ms.")
    # A bite sits AFTER the Beat whose narration it pauses. That Beat both ends and
    # (via the next Beat) starts at the same mark, so the sort needs a tie-break:
    # narration first at every mark, the bite immediately after it.
    # `sound_bite` sorts AFTER narration at the same mark (False < True), so a bite
    # follows the Beat that ends on its pause and precedes the Beat that starts there.
    beats.sort(key=lambda x: (x['at_ms'] if x['kind'] == 'sound_bite' else x['narr_in_ms'],
                              x['kind'] != 'sound_bite'))

    # Last line of defence: no two Beats may name the same frames of one file. The
    # tape is meant to make that impossible. When a seam survives the arithmetic
    # above, a footage Beat is simply given a different window from the same file -
    # every unspent frame is still on the tape, so there is always somewhere else to
    # look. Trimming the window instead shortened the picture under the words.
    seen = {}
    for b in beats:
        taken = seen.setdefault(b['source'], [])
        clash = any(b['src_in_ms'] < z and b['src_out_ms'] > a for a, z in taken)
        if clash and b['kind'] == 'narration':
            length = b['src_out_ms'] - b['src_in_ms']
            limit = limits.get(b['source'], 1 << 40)
            free = free_window(taken, length, limit)
            if free is not None:
                b['src_in_ms'], b['src_out_ms'] = free, free + length
                taken.append((free, free + length))
                continue
        if clash:
            for a, z in taken:
                if b['src_in_ms'] < z and b['src_out_ms'] > a:
                    b['src_in_ms'] = z
            if b['src_out_ms'] <= b['src_in_ms']:
                b['src_in_ms'] = max(0, b['src_out_ms'] - 1)
        taken.append((b['src_in_ms'], b['src_out_ms']))

    master = 0
    for b in beats:
        b['master'] = master
        master += (b['src_out_ms'] - b['src_in_ms'])
    # The narration must end exactly where the uploaded audio ends. Any difference
    # left at this point is sub-frame rounding; give it to the final narration Beat
    # rather than leaving a silent tail the renderer refuses.
    narr = [b for b in beats if b['kind'] == 'narration']
    if narr and narr[-1]['narr_out_ms'] != ms(total):
        narr[-1]['narr_out_ms'] = ms(total)
        narr[-1]['src_out_ms'] = narr[-1]['src_in_ms'] + (
            ms(total) - narr[-1]['narr_in_ms'])

    master = 0
    for b in beats:
        b['master'] = master
        master += b['src_out_ms'] - b['src_in_ms']
    for i, b in enumerate(beats, 1):
        b['label'] = f'{i:03d}'
    return beats, {'total_ms': master, 'narration_ms': ms(total),
                   'plan': plan, 'skipped': skipped, 'ambiguous': ambiguous}


def render_script(beats):
    out = []
    for b in beats:
        length = b['src_out_ms'] - b['src_in_ms']
        end = b['master'] + length
        if b['kind'] == 'sound_bite':
            out.append(f"""Beat: {b['label']}

Type: DOCUMENTARY SOUND BITE

Master timeline: {clock(b['master'])} --> {clock(end)} (Duration: {clock(length)})

Narration state: PAUSED (Suspended at {clock(b['at_ms'])})

Full source: {b['source']}

Source in: {clock(b['src_in_ms'])}

Source out: {clock(b['src_out_ms'])}

Footage audio: UNMUTED (100%)

Cut action: CUT

Visual direction: {b['direction']}

On-screen caption: {b['caption']}

Speaker: {b['speaker']}

Continuity note: Narration drives the picture.
""")
        else:
            out.append(f"""Beat: {b['label']}

Type: VOICE OVER

Master timeline: {clock(b['master'])} --> {clock(end)}

Narration clock: RESUMES at {clock(b['narr_in_ms'])} --> {clock(b['narr_out_ms'])}

Narration state: RESUMING

Spoken narration: "narration {b['label']}"

Full source: {b['source']}

Source in: {clock(b['src_in_ms'])}

Source out: {clock(b['src_out_ms'])}

Footage audio: MUTED (0%)

Cut action: CUT

Visual direction: {b['direction']}

On-screen caption: {b['caption']}

Continuity note: Narration drives the picture.
""")
    return ''.join(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--project', required=True)
    ap.add_argument('--catalog', default=None)
    ap.add_argument('--out', required=True)
    ap.add_argument('--min-score', type=int, default=2)
    args = ap.parse_args()
    project = json.loads((Path(args.project) / 'project.json').read_text())
    catalog = load_catalog(args.catalog) if args.catalog else load_catalog()
    beats, report = compose(project, catalog, args.min_score)
    Path(args.out).write_text(render_script(beats))
    from collections import Counter
    tiers = Counter(b['tier'] for b in beats)
    print(f'{len(beats)} beats -> {args.out}')
    print(f'  narration {report["total_ms"]/1000:.2f}s')
    for tier, n in tiers.most_common():
        print(f'  {tier:11} {n:4} beats')
    return 0


if __name__ == '__main__':
    sys.exit(main())
