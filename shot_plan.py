"""Planning aids that never place a picture by themselves.

Three questions this module answers, each strictly from decoded facts and the
user's own documents:

1. Shot index - which continuous shots exist in each uploaded file, with their
   exact IN/OUT. A shot is a decoded span between detected changes; nothing here
   says WHO or WHAT it shows.
2. Candidates - which shot can physically hold a given beat (same length to the
   frame, after any transition handle). Blanks stay blank: a beat with no exact
   shot is reported as a gap, never paired with something merely close.
3. Feasibility - per source, seconds of picture the plan demands against seconds
   the file actually has, so a dead-end plan is visible before a long render.

`prefill` writes a block-map CSV from the SRT with every row's clocks filled and
every picture column left empty for the editor to decide. The empty rows are the
point: they carry the right timing and block references and cannot be mistaken
for an approved choice, because a row with no source file is a named gap.
"""
import csv, io, json, math
from pathlib import Path

FPS = 30
def _clock(ms):
    h, rem = divmod(int(ms), 3600000)
    m, rem = divmod(rem, 60000)
    s, f = divmod(rem, 1000)
    return '%02d:%02d:%02d.%03d' % (h, m, s, f)


MIN_SHOT = 0.4  # shorter than this is detector noise, not a usable picture


def _analysis(root):
    path = Path(root) / 'reviews/Full_source_analysis.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {s.get('file'): s for s in data.get('sources', []) if s.get('complete_decode_success')}


def _black_overlap(source, start, end):
    for interval in source.get('black_intervals') or []:
        if start < float(interval['end']) - 0.02 and end > float(interval['start']) + 0.02:
            return True
    return False


def shots(root, assets=None):
    """Every usable continuous shot per decoded file, in source order."""
    index = []
    for name, source in sorted(_analysis(root).items()):
        duration = float(source.get('duration_s') or 0)
        cuts = sorted({float(x) for x in source.get('scene_change_times_s') or []})
        cuts = [c for c in cuts if 0.25 < c < duration - 0.25]
        edges = [0.0] + cuts + [duration]
        entries = []
        for start, end in zip(edges, edges[1:]):
            if end - start < MIN_SHOT:
                continue
            entries.append({
                'file': name,
                'in_s': round(start, 3),
                'out_s': round(end, 3),
                'in_clock': _clock(round(start * 1000)),
                'out_clock': _clock(round(end * 1000)),
                'duration_s': round(end - start, 3),
                'frames': int(math.floor((end - start) * FPS + 1e-6)),
                'black': _black_overlap(source, start, end),
            })
        index.append({
            'file': name,
            'duration_s': duration,
            'shots': entries,
            'coverage_s': round(sum(e['duration_s'] for e in entries), 3),
        })
    if assets:
        uploaded = {a.get('original_name') for a in assets if a.get('kind') == 'video'}
        known = {entry['file'] for entry in index}
        for name in sorted(uploaded - known):
            index.append({'file': name, 'duration_s': 0, 'shots': [], 'coverage_s': 0, 'not_decoded': True})
    return index


def _plan_rows(p):
    """The rows the plan is actually built from, or [] when there is no plan."""
    m = p.get('srt_block_map') or {}
    return m.get('beats') or []


def _beat_lengths(p):
    """Timeline length of each planned beat, in seconds, from the plan itself."""
    return [(r.get('timeline_out_ms', 0) - r.get('timeline_in_ms', 0)) / 1000 for r in _plan_rows(p)]


def feasibility(p, root):
    """Seconds required against seconds available, per source. Never guesses."""
    index = {entry['file']: entry for entry in shots(root, p.get('assets'))}
    rows = _plan_rows(p)
    demand, unfilled = {}, []
    for row in rows:
        name = row.get('source_file') or ''
        length = (row.get('timeline_out_ms', 0) - row.get('timeline_in_ms', 0)) / 1000
        handle = (row.get('transition_handle_frames') or 0) / FPS if row.get('transition_out') == 'dissolve' else 0
        if not name:
            unfilled.append({'beat_id': row.get('beat_id'), 'needed_s': round(length, 3), 'reason': row.get('gap_note') or 'no source selected'})
            continue
        record = demand.setdefault(name, {'file': name, 'required_s': 0.0, 'beats': 0, 'declared_s': 0.0, 'longest_beat_s': 0.0})
        record['required_s'] += length + handle
        record['beats'] += 1
        record['longest_beat_s'] = max(record['longest_beat_s'], length + handle)
        if row.get('source_in_ms') is not None and row.get('source_out_ms') is not None:
            record['declared_s'] += (row['source_out_ms'] - row['source_in_ms']) / 1000
    uploaded = {a.get('original_name'): a for a in p.get('assets') or [] if a.get('kind') == 'video'}
    sources = []
    for name, record in sorted(demand.items()):
        entry = index.get(name) or {}
        decoded = bool(entry.get('shots'))
        # Before a file is decoded, its own stored duration is still a real figure: it is
        # what the upload reported. Never borrow another file's numbers.
        available = float(entry.get('duration_s') or (uploaded.get(name) or {}).get('duration') or 0)
        usable = float(entry.get('coverage_s') or 0)
        longest_shot = max([e['duration_s'] for e in entry.get('shots') or []], default=0.0)
        problems = []
        if not entry:
            problems.append('No uploaded file matches this name, so nothing can be placed from it.')
        elif entry.get('not_decoded'):
            problems.append('The file is uploading but has not been decoded yet.')
        elif not decoded:
            problems.append('Only the reported file length is known so far (' + str(round(available, 3)) + 's). Run the build once to decode its shots and check the plan against them.')
        else:
            if record['required_s'] > available + 1e-6:
                problems.append('Needs ' + str(round(record['required_s'], 3)) + 's of picture but the file is only ' + str(round(available, 3)) + 's long.')
            if record['longest_beat_s'] > longest_shot + 1e-6:
                problems.append('The longest beat from this file is ' + str(round(record['longest_beat_s'], 3)) + 's but its longest single shot is ' + str(round(longest_shot, 3)) + 's; a beat cannot straddle a shot change.')
            if record['required_s'] > usable + 1e-6:
                problems.append('Needs ' + str(round(record['required_s'], 3)) + 's and the decoded shots total ' + str(round(usable, 3)) + 's, so the plan reuses footage or overlaps intervals.')
        sources.append({**record,
                        'decoded': decoded,
                        'required_s': round(record['required_s'], 3),
                        'declared_s': round(record['declared_s'], 3),
                        'longest_beat_s': round(record['longest_beat_s'], 3),
                        'available_s': round(available, 3),
                        'usable_s': round(usable, 3),
                        'longest_shot_s': round(longest_shot, 3),
                        'slack_s': round(available - record['required_s'], 3),
                        'problems': problems})
    return {
        'has_plan': bool(rows),
        'beat_count': len(rows),
        'unfilled_beats': unfilled,
        'sources': sources,
        'verdict': 'no_plan' if not rows else ('blocked' if (unfilled or any(s['problems'] for s in sources)) else 'feasible'),
        'scope': 'Arithmetic on decoded durations and the plan as written. Says nothing about what any shot shows; that stays a human review.',
    }


def _candidates(entry, length, handle, limit):
    fits = []
    for shot in entry.get('shots') or []:
        need = length + handle
        if shot['duration_s'] + 1e-6 < need:
            continue
        if shot['black']:
            continue
        fits.append({**shot, 'slack_s': round(shot['duration_s'] - need, 3)})
    fits.sort(key=lambda s: (s['slack_s'], s['in_s']))
    return fits[:limit]


def shots_of_length(root, p, length, handle=0.0, limit=8):
    """Every shot able to hold a picture of this length, across all decoded files."""
    found = []
    for entry in shots(root, p.get('assets')):
        found.extend(_candidates(entry, length, handle, limit))
    found.sort(key=lambda s: (s['slack_s'], s['file'], s['in_s']))
    return found[:limit]


def candidates(p, root, beat_id=None, limit=8):
    """Exact-length shots per beat. A beat with no fit is reported, not filled."""
    beats = []
    for row in _plan_rows(p):
        if beat_id and str(row.get('beat_id')) != str(beat_id):
            continue
        length = (row.get('timeline_out_ms', 0) - row.get('timeline_in_ms', 0)) / 1000
        handle = (row.get('transition_handle_frames') or 0) / FPS if row.get('transition_out') == 'dissolve' else 0
        name = row.get('source_file') or ''
        entry = (shots(root, p.get('assets')) or [])
        entry = next((e for e in entry if e['file'] == name), None)
        options = _candidates(entry, length, handle, limit) if entry else []
        beats.append({
            'beat_id': row.get('beat_id'),
            'srt_blocks': row.get('srt_blocks'),
            'source_file': name,
            'needed_s': round(length + handle, 3),
            'candidates': options,
            'coverage': 'exact' if options else ('no_source' if not entry else 'no_matching_shot'),
        })
    return {'beats': beats, 'fps': FPS}


PREFILL_COLUMNS = ['block', 'start', 'end', 'beat', 'media_file', 'source_in', 'source_out', 'status', 'gap_note']


def prefill(p, parent_beat=1):
    """A block-map CSV with every clock filled and every picture column blank.

    One row per SRT block, so the map covers the narration exactly. `gap_note`
    explains the blank, which keeps the file importable as an honest list of
    decisions still to make rather than an approved plan.
    """
    blocks = (p.get('srt') or {}).get('blocks') or []
    if not blocks:
        raise ValueError('Upload the authored SRT first; the map is built from its blocks.')
    rows = []
    for block in blocks:
        rows.append({
            'block': str(block['number']),
            'start': _clock(block['start_ms']),
            'end': _clock(block['end_ms']),
            'beat': str(parent_beat),
            'media_file': '',
            'source_in': '',
            'source_out': '',
            'status': '',
            'gap_note': 'no source chosen yet for SRT block ' + str(block['number']) + ' (write source_in/source_out as HH:MM:SS.mmm; a row with no source stays a named gap)',
        })
    return rows


def prefill_csv(p, parent_beat=1):
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=PREFILL_COLUMNS, lineterminator='\n')
    writer.writeheader()
    for row in prefill(p, parent_beat):
        writer.writerow(row)
    return buffer.getvalue()


def guidance(p, root, parent_beat=1):
    """Prefill rows plus the shots that can serve each one."""
    rows = prefill(p, parent_beat)
    index = shots(root, p.get('assets'))
    seen = {}
    for block, row in zip((p.get('srt') or {}).get('blocks') or [], rows):
        length = (block['end_ms'] - block['start_ms']) / 1000
        for entry in index:
            fits = _candidates(entry, length, 0, 3)
            if fits:
                seen.setdefault(row['block'], []).append({
                    'file': entry['file'],
                    'options': [{'in_s': s['in_s'], 'out_s': s['out_s'], 'slack_s': s['slack_s']} for s in fits],
                })
        row['possible_sources'] = seen.get(row['block'], [])
    return {'rows': rows, 'columns': PREFILL_COLUMNS, 'note': 'Clocks and block references are filled. Picture columns are deliberately blank: a row with no source file is a named gap, not an approved choice.'}
