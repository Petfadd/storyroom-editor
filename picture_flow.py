"""Cut conformance and narration flow.

Two separate questions, never conflated:

1. Picture flow - does every picture beat sit inside ONE decoded shot of its
   source, and does it leave and return to a source without a visible jump cut?
2. Narration flow - does every picture change land where the narrator is not
   speaking, or at least where the SRT already breaks the line?

Frames are decoded to answer "is this interval playable and continuous". No
function here claims to know WHO or WHAT a frame shows; semantic identity stays
a human review question. Nothing is trimmed, stretched, looped or frozen: a beat
that cannot be made to fit inside one shot is reported, not faked.
"""
from pathlib import Path
import json, subprocess

FPS = 30
SHOT = 1 / FPS
# A detected shot change this close to a requested IN is treated as already aligned.
ALIGN_TOLERANCE = 0.12
# How far a declared moment may travel to stay inside one decoded shot.
CONFORM_WINDOW = 8.0
# How far a declared moment may travel backwards to continue a running take.
CONTINUITY_WINDOW = 2.0
# How far a cut may move to land in a breath while the narration keeps playing.
SNAP_WINDOW = 0.35
MIN_SNAP_SCENE = 1.2
# Prefer a free moment over one a later beat declared for itself.
COLLISION_PENALTY = 0.6


def _analysis(root):
    path = Path(root) / 'reviews/Full_source_analysis.json'
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return {s.get('file'): s for s in data.get('sources', []) if s.get('complete_decode_success')}


def shot_index(root):
    """Decoded shot boundaries per original filename, plus black intervals."""
    index = {}
    for name, source in _analysis(root).items():
        duration = float(source.get('duration_s') or 0)
        cuts = sorted({float(x) for x in source.get('scene_change_times_s') or []})
        # Ignore detector noise at the very head/tail of the file.
        cuts = [c for c in cuts if 0.25 < c < duration - 0.25]
        index[name] = {
            'duration': duration,
            'shots': [(a, b) for a, b in zip([0.0] + cuts, cuts + [duration]) if b - a > 0.2],
            'black': [(float(x['start']), float(x['end'])) for x in source.get('black_intervals') or []],
            'decoded_cuts': len(cuts),
        }
    return index


def _shots_for(index, filename):
    record = index.get(filename)
    if record:
        return record['shots']
    return None


def _black_overlap(index, filename, start, end):
    for a, b in (index.get(filename) or {}).get('black', []):
        if start < b - 0.02 and end > a + 0.02:
            return True
    return False


def crossing_cuts(shots, start, end):
    """Detected shot changes strictly inside a requested interval."""
    return [c for _, c in zip(shots, [s for s, _ in shots[1:]]) if start + 0.02 < c < end - 0.02]


def best_interval(shots, declared, duration):
    """Nearest playable interval of `duration` seconds inside one decoded shot."""
    for a, b in shots:
        if b - a < duration + 0.02:
            continue
        candidate = min(max(declared, a), b - duration)
        yield abs(candidate - declared), candidate, (a, b)


def _cooldown_window(p):
    policy = p.get('repetition_policy') or {}
    return int(policy.get('cooldown_slots') or 0)


def _recent(placed, filename, window):
    """Intervals of this source placed inside the project's repetition window."""
    taken = []
    for entry in placed[-window:] if window else []:
        if entry['asset'] == filename:
            taken.append((entry['start_s'], entry['end_s']))
    return taken


def _taken(taken, start, end):
    return any(start < stop - .05 and end > inside + .05 for inside, stop in taken)


def _shot_holding(shots, start, end):
    for a, b in shots:
        if a - SHOT <= start and end <= b + SHOT:
            return (a, b)
    return None


def conform_picture(p, root, window=CONFORM_WINDOW, continuity=CONTINUITY_WINDOW, apply=True):
    """Bend requested source IN points onto continuous decoded shots.

    Preference order, only as far as needed:
      1. the declared moment already sits inside one decoded shot - untouched;
      2. the declared moment returns to the shot the previous beat was running
         from - joined to that take instead of jump-cutting;
      3. the nearest decoded shot that can hold the whole beat;
      4. nothing fits - reported, never trimmed, stretched, looped or frozen.
    """
    index = shot_index(root)
    assets = {a['id']: a for a in p.get('assets', [])}
    scenes = p.get('scenes') or []
    report = {'scope': 'decoded shot continuity; not identity or content verification',
              'sources_indexed': len(index), 'beats': [], 'summary': {}}
    previous = None
    placed, window_slots = [], _cooldown_window(p)
    window = window_slots or window
    declared_by_asset = {}
    for number, beat in enumerate(scenes):
        beat_asset = (assets.get(beat.get('asset_id')) or {})
        beat_name = beat_asset.get('original_name') or beat_asset.get('name') or ''
        declared_by_asset.setdefault(beat_name, []).append(
            (number, float(beat.get('in_s') or 0),
             float(beat.get('in_s') or 0) + (beat['frame_out'] - beat['frame_in']) / FPS))
    counts = {'already_inside_one_shot': 0, 'continuous_with_previous': 0, 'continued_running_take': 0,
              'slid_inside_a_shot': 0, 'slid_far_inside_a_shot': 0,
              'jump_cut_within_shot': 0, 'repeats_recent_footage': 0, 'no_single_shot_fits': 0, 'not_decoded': 0}
    for i, scene in enumerate(scenes, 1):
        asset = assets.get(scene.get('asset_id')) or {}
        filename = asset.get('original_name') or asset.get('name') or ''
        duration = (scene['frame_out'] - scene['frame_in']) / FPS
        declared = float(scene.get('in_s') or 0)
        record = {'scene': i, 'asset': filename, 'srt_blocks': scene.get('srt_blocks'),
                  'duration_s': round(duration, 3), 'declared_in_s': round(declared, 3),
                  'conformed_in_s': round(declared, 3), 'shift_s': 0.0,
                  'shot': None, 'status': '', 'note': '', 'review': False}
        shots = _shots_for(index, filename)
        if not shots:
            record['status'] = 'not_decoded'
            counts['not_decoded'] += 1
            report['beats'].append(record)
            previous = None
            continue
        taken = _recent(placed, filename, window)
        previous_take = previous if previous and previous['asset'] == filename else None
        carry_on = None
        if previous_take and previous_take['exit_s'] + duration <= previous_take['shot'][1] + SHOT:
            carry_on = previous_take['exit_s']
        if carry_on is not None and _taken(taken, carry_on, carry_on + duration):
            carry_on = None
        # 1. the declared moment is already playable and whole inside one shot
        holding = None
        if not _black_overlap(index, filename, declared, declared + duration):
            holding = _shot_holding(shots, declared, declared + duration)
        if holding and _taken(taken, declared, declared + duration):
            # An earlier beat now runs through this exact moment; relocate instead of repeating it.
            holding = None
        if holding:
            shot = holding
            same_take = bool(previous_take) and previous_take['shot'] == shot
            if same_take and abs(previous_take['exit_s'] - declared) <= SHOT + .001:
                start = declared
                record['status'] = 'continuous_with_previous'
                record['note'] = 'Runs on from the previous beat with no jump.'
            elif same_take and carry_on is not None:
                start = carry_on
                record['status'] = 'continued_running_take'
                record['note'] = ('Stayed in the same take as the previous beat and continued it from %.2fs instead of '
                                  'jumping inside the shot. The declared moment was %.2fs earlier in the take.'
                                  % (start, max(0.0, start - declared)))
            elif same_take:
                start = declared
                record['status'] = 'jump_cut_within_shot'
                record['note'] = 'Re-enters the same shot at a moment the previous beat already ran past; treat as a deliberate jump cut or choose another take.'
                record['review'] = True
            else:
                start = declared
                record['status'] = 'already_inside_one_shot'
        elif carry_on is not None and not _taken(taken, carry_on, carry_on + duration):  # 2. keep the running take alive
            start, shot, record['status'] = carry_on, previous_take['shot'], 'continued_running_take'
            record['note'] = 'Continued the previous beat\'s take at %0.2fs because the declared moment crosses a shot change.' % start
        else:  # 3. the nearest decoded shot that can hold the beat whole
            def steals_another_beat(start_at, stop_at):
                return any(n != i - 1 and start_at < other_end - .05 and stop_at > other_start + .05
                           for n, other_start, other_end in declared_by_asset.get(filename, []))
            options = []
            for distance, start_at, shot_range in best_interval(shots, declared, duration):
                if _taken(taken, start_at, start_at + duration):
                    continue
                collision = steals_another_beat(start_at, start_at + duration)
                options.append((distance + (COLLISION_PENALTY if collision else 0.0), start_at, shot_range, collision))
            reused = False
            if not options:  # every usable interval already ran recently; report the nearest one
                options = list(best_interval(shots, declared, duration))
                reused = bool(options)
            if not options:
                record['status'] = 'no_single_shot_fits'
                record['note'] = ('No decoded shot of %s is long enough for this %.2fs beat. '
                                  'Shorten the beat in the SRT block map or use another take; nothing was trimmed or frozen.'
                                  % (filename, duration))
                record['review'] = True
                counts['no_single_shot_fits'] += 1
                report['beats'].append(record)
                previous = None
                continue
            score, start, shot = min(options, key=lambda o: (o[0], o[1]))[:3]
            collision = len(options[0]) > 3 and min(options, key=lambda o: (o[0], o[1]))[3]
            distance = score - (COLLISION_PENALTY if collision else 0.0)
            if reused:
                record['status'] = 'repeats_recent_footage'
                record['note'] = ('No unused moment of %s can hold this %.2fs beat. The nearest usable interval was already '
                                  'used within the last %d slots; treat it as a deliberate callback, shorten the beat or add another take.'
                                  % (filename, duration, window_slots or window))
                record['review'] = True
            elif distance > window:
                record['status'] = 'slid_far_inside_a_shot'
                record['note'] = ('Moved %.2fs from the declared moment: every playable moment closer to it is already used '
                                  'by another beat or crosses a shot change. The nearest free shot is %0.2f-%0.2f. Check this beat still shows the intended moment.'
                                  % (distance, shot[0], shot[1]))
                record['review'] = True
            else:
                record['status'] = 'slid_inside_a_shot'
                record['note'] = ('Moved %.2fs so the beat sits inside one decoded shot (%0.2f-%0.2f) instead of crossing a shot change.%s'
                                  % (distance, shot[0], shot[1],
                                     ' The nearer moment was already used by another beat.' if collision else ''))
        counts[record['status']] += 1
        if record['status'] in ('repeats_recent_footage',):
            placed.append({'asset': filename, 'start_s': scene['in_s'], 'end_s': scene['in_s'] + duration,
                           'repeat': True})
        scene['in_s'] = round(max(0.0, start), 3)
        scene['picture_flow'] = {'status': record['status'], 'declared_in_s': record['declared_in_s'],
                                 'conformed_in_s': scene['in_s'], 'shift_s': round(scene['in_s'] - declared, 3),
                                 'shot': [round(shot[0], 3), round(shot[1], 3)], 'note': record['note'],
                                 'review': record['review']}
        record['conformed_in_s'] = scene['in_s']
        record['shift_s'] = round(scene['in_s'] - declared, 3)
        record['shot'] = [round(shot[0], 3), round(shot[1], 3)]
        report['beats'].append(record)
        placed.append({'asset': filename, 'start_s': scene['in_s'], 'end_s': scene['in_s'] + duration})
        previous = {'asset': filename, 'shot': shot, 'exit_s': scene['in_s'] + duration}
    # Suggest where a beat that could not be placed cleanly could go instead.
    used_by_asset = {}
    for entry in placed:
        used_by_asset.setdefault(entry['asset'], []).append((entry['start_s'], entry['end_s']))
    for record in report['beats']:
        if not record['review']:
            continue
        duration = record['duration_s']
        suggestions = []
        for name in sorted(index):
            if name == record['asset']:
                continue
            shots = _shots_for(index, name) or []
            taken = _recent([{'asset': k, 'start_s': a, 'end_s': b} for k, v in used_by_asset.items() for a, b in v],
                            name, window)
            found = None
            for a, b in shots:
                if b - a < duration + 0.02:
                    continue
                for start_at in (a, b - duration):
                    if not _taken(taken, start_at, start_at + duration):
                        found = (round(start_at, 2), round(a, 2), round(b, 2))
                        break
                if found:
                    break
            if found:
                suggestions.append({'asset': name, 'start_s': found[0], 'shot': [found[1], found[2]]})
            if len(suggestions) >= 3:
                break
        record['suggestions'] = suggestions
    report['summary'] = {'beats': len(scenes), 'counts': counts,
                         'untouched': counts['already_inside_one_shot'] + counts['continuous_with_previous'],
                         'moved': sum(counts[k] for k in ('continued_running_take', 'slid_inside_a_shot', 'slid_far_inside_a_shot')),
                         'needs_review': sum(1 for b in report['beats'] if b['review'])}
    return report


def narration_pauses(root, min_pause=0.15, noise='-32dB', refresh=False):
    """Decoded silences in the uploaded narration, cached beside the project."""
    root = Path(root)
    source = next((f for f in sorted((root / 'inputs').glob('voiceover.*'))), None)
    if not source:
        return {'source': '', 'pauses': [], 'decoded': False, 'reason': 'No narration audio is uploaded.'}
    key = {'size': source.stat().st_size, 'mtime': source.stat().st_mtime_ns,
           'min_pause': min_pause, 'noise': noise}
    cache = root / 'reviews/Narration_pauses.json'
    if cache.is_file() and not refresh:
        try:
            old = json.loads(cache.read_text())
            if old.get('key') == key:
                return old
        except (OSError, ValueError):
            pass
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        result = subprocess.run([ffmpeg, '-hide_banner', '-nostats', '-i', str(source),
                                 '-af', 'silencedetect=noise=%s:d=%s' % (noise, min_pause),
                                 '-f', 'null', '-'], capture_output=True, text=True, timeout=900)
        text = result.stderr
    except Exception as error:  # ffmpeg missing or the audio will not decode
        return {'source': source.name, 'pauses': [], 'decoded': False, 'reason': str(error)[:300]}
    pauses, start = [], None
    for line in text.splitlines():
        if 'silence_start:' in line:
            try:
                start = float(line.split('silence_start:')[1].split()[0])
            except (IndexError, ValueError):
                start = None
        elif 'silence_end:' in line and start is not None:
            try:
                end = float(line.split('silence_end:')[1].split()[0])
            except (IndexError, ValueError):
                end = None
            if end is not None and end - start >= min_pause:
                pauses.append((round(start, 3), round(end, 3)))
            start = None
    record = {'key': key, 'source': source.name, 'min_pause': min_pause, 'noise': noise,
              'pauses': pauses, 'decoded': True,
              'note': 'Silences are measured from the uploaded narration, not assumed from SRT punctuation.'}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(record, indent=2))
    except OSError:
        pass
    return record


def _blocks(p):
    return (p.get('srt') or {}).get('blocks') or []


def classify_cut(seconds, pauses, blocks):
    """Where a picture change lands relative to the spoken narration."""
    for start, end in pauses:
        if start + 0.02 <= seconds <= end - 0.02:
            return 'in_a_breath', min(seconds - start, end - seconds)
    for block in blocks:
        if abs(seconds - block['start_ms'] / 1000) <= 0.02:
            return 'on_a_block_boundary', 0.0
    for block in blocks:
        if block['start_ms'] / 1000 < seconds < block['end_ms'] / 1000:
            return 'over_speech', 0.0
    return 'between_blocks', 0.0


def _nearest_pause(seconds, pauses, window):
    best = None
    for start, end in pauses:
        if end - start < 0.1:
            continue
        target = min(max(seconds, start + 0.06), end - 0.06)
        distance = abs(target - seconds)
        if distance <= window and (best is None or distance < best[0]):
            best = (distance, target, (start, end))
    return best


def snap_cuts(p, root, window=SNAP_WINDOW, min_scene=MIN_SNAP_SCENE, apply=True):
    """Move picture changes into the narrator's breaths without moving the narration.

    Scene lengths change by at most `window`; total duration, SRT wording and the
    narration audio are untouched. A block that ends up straddling a cut is owned
    by exactly one scene, so no line is spoken twice or dropped.
    """
    scenes = p.get('scenes') or []
    blocks = _blocks(p)
    pauses = narration_pauses(root)
    report = {'scope': 'cut placement against decoded narration silences',
              'decoded': bool(pauses.get('decoded')), 'source': pauses.get('source', ''),
              'min_pause': pauses.get('min_pause'), 'pauses': len(pauses.get('pauses') or []),
              'before': {}, 'after': {}, 'cuts': []}
    if not scenes or not blocks:
        report['reason'] = 'A storyboard and an SRT are both required.'
        return report
    pauses = pauses.get('pauses') or []
    if not pauses:
        report['reason'] = pauses.get('reason') if isinstance(pauses, dict) else 'No narration silences were decoded.'
    fps = FPS
    boundaries = [s['frame_in'] for s in scenes[1:]]
    report['before'] = _cut_counts([b / fps for b in boundaries], pauses, blocks)
    moved = 0
    for index, frame in enumerate(boundaries):
        seconds = frame / fps
        quality, _ = classify_cut(seconds, pauses, blocks)
        entry = {'cut': index + 1, 'before_s': round(seconds, 3), 'after_s': round(seconds, 3),
                 'quality_before': quality, 'quality_after': quality, 'moved': False, 'block': None}
        if quality != 'in_a_breath' and pauses:
            candidate = _nearest_pause(seconds, pauses, window)
            if candidate:
                _, target, pause = candidate
                new_frame = round(target * fps)
                left = new_frame - scenes[index]['frame_in']
                right = scenes[index + 1]['frame_out'] - new_frame
                if left >= min_scene * fps and right >= min_scene * fps:
                    scenes[index]['frame_out'] = new_frame
                    scenes[index + 1]['frame_in'] = new_frame
                    seconds = new_frame / fps
                    moved += 1
                    entry.update(after_s=round(seconds, 3), moved=True,
                                 quality_after=classify_cut(seconds, pauses, blocks)[0],
                                 pause=[round(pause[0], 3), round(pause[1], 3)])
        scene = scenes[index]
        scene['cut_sync'] = {'cut_at_s': round(seconds, 3), 'quality': entry['quality_after'],
                             'moved_from_s': entry['before_s'] if entry['moved'] else None}
        report['cuts'].append(entry)
    # Check that a straddling line still belongs to exactly one scene, WITHOUT rewriting
    # the storyboard. A cut may move by up to `window`, so a scene's own SRT reference can
    # legitimately no longer cover every block that overlaps it; rewriting the reference
    # here silently replaced the plan's own record of which line a beat carries, which is
    # how a beat ended up labelled with the next block's number. Report it instead.
    conflicts = []
    for index, scene in enumerate(scenes, 1):
        start, end = scene['frame_in'] / fps, scene['frame_out'] / fps
        touching = [b['number'] for b in blocks if b['start_ms'] / 1000 < end - 1e-6 and b['end_ms'] / 1000 > start + 1e-6]
        declared = set(scene.get('srt_blocks') or [])
        extra = [n for n in touching if n not in declared]
        if extra:
            conflicts.append({'scene': index, 'declared': sorted(declared), 'also_touches': extra})
    report['block_conflicts'] = conflicts
    if conflicts:
        report.setdefault('notes', []).append(
            'A cut moved into a breath, so %d scene(s) now overlap a neighbouring SRT block. '
            'The storyboard keeps the block references the plan gave it; nothing was re-labelled.' % len(conflicts))
    report['after'] = _cut_counts([s['frame_in'] / fps for s in scenes[1:]], pauses, blocks)
    report['summary'] = {'cuts': len(boundaries), 'moved': moved,
                         'in_a_breath_before': report['before'].get('in_a_breath', 0),
                         'in_a_breath_after': report['after'].get('in_a_breath', 0),
                         'over_speech_before': report['before'].get('over_speech', 0),
                         'over_speech_after': report['after'].get('over_speech', 0)}
    return report


def _cut_counts(cut_times, pauses, blocks):
    counts = {}
    for seconds in cut_times:
        quality, _ = classify_cut(seconds, pauses, blocks)
        counts[quality] = counts.get(quality, 0) + 1
    return counts


def apply_flow(p, root, snap=True):
    """Run the full pass for one project and return a JSON-ready report."""
    snap_report = snap_cuts(p, root) if snap else {'summary': {}, 'cuts': [], 'reason': 'Snapping not requested.'}
    conform = conform_picture(p, root)
    return {'project_id': p.get('id'), 'project_title': p.get('title'),
            'picture': conform, 'narration': snap_report,
            'summary': {'beats': len(p.get('scenes') or []),
                        'shot_conform': conform.get('summary', {}),
                        'cut_flow': snap_report.get('summary', {})},
            'note': ('Requested source moments were bent onto continuous decoded shots and picture changes were '
                     'moved into decoded narration pauses. Narration, SRT wording and total duration are unchanged. '
                     'Neither step establishes who or what a frame shows.')}


def outstanding_notes(report):
    """Plain-language notes for the pre-render check. Never an export blocker."""
    picture = (report or {}).get('picture') or {}
    narration = (report or {}).get('narration') or {}
    notes = []
    psum, nsum = picture.get('summary') or {}, narration.get('summary') or {}
    if psum:
        notes.append('Picture flow: %(untouched)s of %(beats)s beats already sat inside one decoded shot; '
                     '%(moved)s were conformed to the nearest playable moment inside a single shot.' % psum)
        review = [b for b in picture.get('beats', []) if b.get('review')]
        if review:
            scenes = ', '.join(str(b['scene']) for b in review[:10]) + ('…' if len(review) > 10 else '')
            notes.append('Picture flow: %d beats need a human look (scene %s) — the declared moment moved far '
                         'or re-entered a shot already used. Timing was not trimmed, looped or frozen.' % (len(review), scenes))
    if nsum:
        notes.append('Narration flow: %(in_a_breath_before)s of %(cuts)s cuts already landed in a decoded narration pause; '
                     'after snapping %(in_a_breath_after)s do. Scene lengths moved by at most 0.35s; the narration audio, '
                     'SRT wording and total duration are unchanged.' % nsum)
    elif narration.get('reason'):
        notes.append('Narration flow: ' + str(narration['reason']))
    conflicts = narration.get('block_conflicts') or []
    if conflicts:
        scenes = ', '.join(str(c['scene']) for c in conflicts[:10]) + ('…' if len(conflicts) > 10 else '')
        notes.append('Narration flow: %d cut(s) moved into a breath, so scene %s now overlap a neighbouring SRT '
                     'block. The beat keeps the block reference the plan gave it; nothing was re-labelled.' % (len(conflicts), scenes))
    return notes


def csv_rows(report):
    """One row per picture beat for the cut-flow audit."""
    picture = {b['scene']: b for b in (report.get('picture') or {}).get('beats', [])}
    cuts = {c['cut']: c for c in (report.get('narration') or {}).get('cuts', [])}
    rows = [['scene', 'asset', 'srt_blocks', 'duration_s', 'declared_in_s', 'conformed_in_s', 'shift_s',
             'shot_start_s', 'shot_end_s', 'picture_status', 'cut_to_next_after_s', 'cut_quality', 'note']]
    for number in sorted(picture):
        beat, cut = picture[number], cuts.get(number, {})
        shot = beat.get('shot') or ['', '']
        rows.append([number, beat.get('asset', ''), '/'.join(map(str, beat.get('srt_blocks') or [])),
                     beat.get('duration_s', ''), beat.get('declared_in_s', ''), beat.get('conformed_in_s', ''),
                     beat.get('shift_s', ''), shot[0], shot[1], beat.get('status', ''),
                     cut.get('after_s', ''), cut.get('quality_after', ''), beat.get('note', '')])
    return rows
