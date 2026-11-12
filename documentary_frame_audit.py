"""Frame-boundary audit for a Beat storyboard, without changing its timeline.

This is a preflight primitive for a future documentary player, not a renderer.
"""
import hashlib
from fractions import Fraction
from pathlib import Path
from documentary_storyboard import verified_source_interval
from documentary_beats import read_beats
from strict_cuts import _nearest_frame, source_rate, inspect_video


def audit(project, root, plan):
    root = Path(root).resolve()
    assets = {a['id']: a for a in project.get('assets', [])}
    result = {'parts': [], 'errors': [], 'warnings': [], 'verified': False}
    required = {}
    try:
        # A storyboard is checked against the plan it was built from, not against the
        # current text of the production script. The script is an input that can be
        # edited, replaced and edited again; the saved Beat timeline is the thing that
        # actually plays, and it is internally consistent or it is not. Requiring the
        # script's hash to still match turned every later edit into a blanket failure
        # of all picture slots, which made one project usable for exactly one script.
        # A changed script is now reported as a warning (rebuild to apply it).
        record = project.get('documentary_beat_review') or {}
        if not record.get('fitted_beats') and not record.get('fitted_ranges'):
            raise ValueError('This Beat storyboard did not record the ranges it was built from, so its '
                             'picture cannot be verified. Rebuild the Beat timeline.')
        text = (project.get('production_script') or {}).get('text', '')
        recorded = record.get('script_sha256')
        if text and recorded and recorded != hashlib.sha256(text.encode()).hexdigest():
            result['warnings'].append(stale_script_message(project, record, text))
        # What the build actually placed, after its own fitting. A beat that ran past
        # the end of its file was deliberately clamped and the clamp was recorded and
        # shown to the user; comparing the saved bite with the *raw script* instead
        # made the audit reject the app's own documented decision, so a script the
        # build accepted could never be played back and the only way out was editing
        # the script. The fitted record is the contract the storyboard was built to.
        fitted_all = list(record.get('fitted_beats') or [])
        fitted = {b['label']: b for b in fitted_all}
        scripted = {b['label']: b for b in fitted_all if b.get('kind') == 'sound_bite'}
        bites = plan.get('bites', [])
        # Bites in plan order, keyed by the source file they were cut from, so each
        # slot's caption is checked against the Beat it actually came from.
        scripted_by_file = {}
        for b in sorted(bites, key=lambda b: b.get('pause_s', 0)):
            beat = scripted.get(b.get('script_beat'))
            if beat:
                scripted_by_file[beat.get('source_file')] = beat
        if len(bites) != len(scripted) or len({b.get('script_beat') for b in bites}) != len(bites):
            raise ValueError(f'This storyboard recorded {len(scripted)} sound bite(s) but its plan holds '
                             f'{len(bites)}. Rebuild the Beat timeline.')
        for i, scene in enumerate(project.get('scenes') or []):
            asset = assets.get(scene.get('asset_id'))
            if not asset:
                raise ValueError(f'Scene {i+1}: source is missing.')
            interval = verified_source_interval(project, scene, asset)
            if interval is None:
                raise ValueError(f'Scene {i+1}: source or timeline no longer matches its script or approved gap.')
            add_part(result, required, root, asset, round(interval[0]*1000), round(interval[1]*1000), f'Scene {i+1}', scene.get('plan_row', {}).get('source_fps'))
        if not project.get('scenes'):
            raise ValueError('The storyboard is empty.')
        for bite in bites:
            beat = scripted.get(bite.get('script_beat'))
            asset = assets.get(bite.get('asset_id'))
            if not beat or not asset:
                raise ValueError(f"Saved sound bite {bite.get('script_beat') or '?'} is not in this storyboard's "
                                 f"recorded plan. Rebuild the Beat timeline.")
            # Compare with the range the build actually placed. `fitted` is what the
            # storyboard recorded at build time; the raw script is only a fallback for
            # storyboards made before that record existed.
            placed = fitted.get(beat['label']) or beat
            saved_name = asset.get('original_name') or asset.get('name')
            differences = []
            if saved_name != placed['source_file']:
                differences.append(f"file is {placed['source_file']} in the script but {saved_name} in the storyboard")
            if round(bite.get('pause_s', -1) * 1000) != placed['pause_ms']:
                differences.append(f"pause is {placed['pause_ms']/1000:.3f}s in the script but "
                                   f"{round(bite.get('pause_s', -1)*1000)/1000:.3f}s in the storyboard")
            if round(bite.get('source_in_s', -1) * 1000) != placed['source_in_ms']:
                differences.append(f"source IN is {placed['source_in_ms']/1000:.3f}s in the script but "
                                   f"{round(bite.get('source_in_s', -1)*1000)/1000:.3f}s in the storyboard")
            if round(bite.get('source_out_s', -1) * 1000) != placed['source_out_ms']:
                differences.append(f"source OUT is {placed['source_out_ms']/1000:.3f}s in the script but "
                                   f"{round(bite.get('source_out_s', -1)*1000)/1000:.3f}s in the storyboard")
            if differences:
                raise ValueError(f"Beat {beat['label']}: " + '; '.join(differences) +
                                 '. The saved bite and the range recorded when this storyboard was built '
                                 'disagree, so the picture is inconsistent. Rebuild the Beat timeline.')
            if bite.get('stale_reasons'):
                raise ValueError('A sound bite uses replaced or missing media.')
            add_part(result, required, root, asset, beat['source_in_ms'], beat['source_out_ms'], f"Beat {beat['label']}")
        for path, last in required.items():
            inspect_video(path, last)
        result['verified'] = True
    except (ValueError, KeyError, TypeError, OSError, OverflowError) as exc:
        result['errors'].append(str(exc))
    return result


def _source_frames(path):
    import av
    try:
        with av.open(str(path)) as container:
            return int(container.streams.video[0].frames or 0)
    except Exception:
        return 0


def add_part(result, required, root, asset, start_ms, end_ms, label, recorded_fps=None):
    path = (root / asset.get('path', '')).resolve()
    if asset.get('kind') != 'video' or not path.is_relative_to(root / 'sources') or not path.is_file():
        raise ValueError(f'{label}: source video is missing.')
    # The storyboard records the rate each slot was built against. Without it the
    # raw container rate is used, which is what older storyboards were checked on.
    try:
        rate = Fraction(str(recorded_fps)) if recorded_fps else source_rate(path)
    except (ValueError, ZeroDivisionError):
        rate = source_rate(path)
    start, end = _nearest_frame(start_ms, rate), _nearest_frame(end_ms, rate)
    if start < 0 or end <= start:
        raise ValueError(f'{label}: source interval has no verified frames.')
    if end_ms > float(asset.get('duration') or 0)*1000 + .001:
        raise ValueError(f'{label}: source OUT exceeds the video.')
    duration = Fraction(end-start, 1)/rate
    # An integer multiple of the output rate (a 59.94 fps source in a 29.97 fps
    # documentary) is converted by dropping every other frame: the same picture,
    # no speed change. Each converted frame keeps its real interval.
    # An integer multiple of the output rate (a 59.94 fps source in a 29.97 fps
    # documentary) is converted by dropping every other frame: the same picture and
    # timing, no speed change. The comparison allows for the averaged rate a
    # container reports (59.9400575909886 instead of exactly 59.94).
    basis = rate
    for multiple in (2, 3, 4):
        target = Fraction(30000, 1001)*multiple
        if abs(rate-target)/target < Fraction(1, 500):
            basis = Fraction(30000, 1001)
            break
    result['parts'].append({'label': label, 'source': asset.get('original_name') or asset.get('name'),
                            'source_start_frame': start, 'source_end_frame': end,
                            'source_fps': str(rate), 'output_fps': str(basis), 'source_rate_fps': str(rate),
                            'frame_conversion': 'halve' if basis != rate else 'none',
                            'source_frame_duration_s': float(duration),
                            'requested_duration_s': (end_ms-start_ms)/1000,
                            'duration_deviation_ms': float(duration*1000-(end_ms-start_ms))})
    required[path] = max(required.get(path, 0), end)


def playback_warnings(project, root, plan):
    """Anything worth telling the reader about an otherwise playable storyboard.

    Separate from `playback_schedule` so a caller can report a changed script
    without paying for the full frame walk a second time.
    """
    checked = audit(project, root, plan)
    if not checked['verified']:
        raise ValueError('; '.join(checked['errors']))
    return list(checked.get('warnings') or [])


def playback_schedule(project, root, plan, warnings=None):
    """Create a frame-indexed review playback schedule, without rendering media.

    Narration slots and bites retain the exact script clock. A consumer must still
    reconcile snapped frame durations with that clock; this function does not imply
    those durations are compatible or authorize changing playback speed.

    `warnings`, when passed a list, receives the notes `audit` collected so a route
    can report them without auditing the same project twice.
    """
    checked = audit(project, root, plan)
    if not checked['verified']:
        raise ValueError('; '.join(checked['errors']))
    if isinstance(warnings, list):
        warnings.extend(checked.get('warnings') or [])
    # Captions come from the plan this storyboard was built to, not from the current
    # script. Reading the script here made playback depend on its exact text: a
    # caption edited after the build was reported as a broken picture schedule.
    record = project.get('documentary_beat_review') or {}
    beats = {b['label']: b for b in (record.get('fitted_beats') or [])}
    if not beats:
        beats = {b['label']: b for b in read_beats(project['production_script']['text'])}
    # Each sound-bite slot's caption is the speaker recorded on the Beat its own plan
    # bite was cut from, so a caption rewritten after the build is caught. Keyed by
    # the plan bite's id: two bites can share one source file (two speakers from the
    # same bulletin), and keying by file gave the later one the earlier's caption.
    scripted_by_bite = {}
    for b in plan.get('bites', []):
        beat = beats.get(b.get('script_beat'))
        if beat:
            scripted_by_bite[b['id']] = beat
    # Bite positions on the MASTER clock. The plan's pause_s is a mark on the
    # narration clock; comparing it with a scene's master-time span reports a handoff
    # in the middle of a shot that is nowhere near a bite.
    bites = sorted(({'start_ms': b['timeline_in_ms']} for b in (project.get('documentary_beat_review') or {}).get('sound_bites') or []),
                   key=lambda b: b['start_ms'])
    frames = checked['parts']; scenes = project['scenes']; events = []
    def text(value):
        return '' if not value or value.strip().lower() == 'none' else value.strip()
    for i, scene in enumerate(scenes):
        row = scene['plan_row']; start, end = row['timeline_in_ms'], row['timeline_out_ms']
        label = row['continuity'].removeprefix('Documentary beat ')
        # A sound bite is its own picture, not a scripted Beat. Its caption is the
        # speaker, taken from the Beat the plan was cut from -- not from the slot,
        # which would let a caption be rewritten after the build and still verify.
        if label == 'sound-bite':
            expected = text((scripted_by_bite.get(row.get('bite_id')) or {}).get('caption'))
        elif label == 'approved-gap':
            expected = ''
        else:
            expected = text(beats[label]['caption'])
        if text(row.get('required_caption')) != expected:
            raise ValueError(f'Scene {i+1}: scripted caption has changed.')
        if label != 'sound-bite' and any(start < b['start_ms'] < end for b in bites):
            raise ValueError(f'Scene {i+1}: sound-bite handoff falls inside the picture slot.')
        # Scene timings are already on the master clock the bites created. Adding
        # a bite offset here counted every bite twice and pushed the schedule out
        # of step at exactly the three points the bites sit on.
        offset = 0
        part = dict(frames[i])
        # A scene is one physical slot; the audited frame count is authoritative for
        # its source indices, even when the storyboard recorded a different rate.
        part['source_fps'] = part['source_rate_fps']
        part.update(kind=('sound_bite' if label == 'sound-bite' else 'narration'), scene_id=scene['id'], asset_id=scene['asset_id'], timeline_in_ms=start+offset, timeline_out_ms=end+offset, caption=expected, picture_scale=int(scene.get('picture_scale') or project.get('picture_scale') or 100))
        events.append(part)
    # Older storyboards kept bites out of the scene list and appended them here.
    # The current builder gives each bite its own scene, so appending them again
    # would place every bite twice and open a gap at each one.
    bite_scenes = any((sc.get('plan_row') or {}).get('continuity') == 'Documentary beat sound-bite'
                      for sc in scenes)
    for j, bite in enumerate([] if bite_scenes else plan['bites']):
        label = bite['script_beat']; beat = beats.get(label) or {}
        if text(bite.get('script_caption')) != text(beat.get('caption')):
            raise ValueError(f"Beat {label}: scripted caption has changed.")
        pause = round(bite['pause_s']*1000)
        offset = sum(round((b['source_out_s']-b['source_in_s'])*1000) for b in bites if (b['pause_s'],b['created']) < (bite['pause_s'],bite['created']))
        duration = round((bite['source_out_s']-bite['source_in_s'])*1000)
        part = dict(frames[len(scenes)+j]); part.update(kind='sound_bite', bite_id=bite['id'], asset_id=bite['asset_id'], timeline_in_ms=pause+offset, timeline_out_ms=pause+offset+duration, caption=text(beat['caption']), speech=list(bite.get('speech') or []))
        events.append(part)
    events.sort(key=lambda e: (e['timeline_in_ms'], e['timeline_out_ms']))
    for i, event in enumerate(events):
        if event['timeline_out_ms'] <= event['timeline_in_ms'] or (i == 0 and event['timeline_in_ms'] != 0) or (i and event['timeline_in_ms'] != events[i-1]['timeline_out_ms']):
            raise ValueError('Picture schedule contains a gap or overlap on the documentary clock.')
    return events

def stale_script_message(project, record, text):
    """Explain that the plan predates the script, and name the beats that moved.

    A hash mismatch used to surface as one anonymous scene error. Naming the beats
    whose ranges changed tells the reader whether they edited something small or
    replaced most of the plan, and either way the action is the same: rebuild.
    """
    from documentary_beats import read_beats
    try:
        now = {b['label']: b for b in read_beats(text)}
    except ValueError:
        return ('The production script has changed since this Beat timeline was built, and it can no '
                'longer be read as a Beat script. Rebuild the Beat timeline after correcting it.')
    built = {b.get('label'): b for b in (record.get('fitted_beats') or [])}
    moved, added, removed = [], [], []
    for label in sorted(set(built) | set(now), key=lambda x: (len(str(x)), str(x))):
        was, is_now = built.get(label), now.get(label)
        if was is None:
            added.append(label)
            continue
        if is_now is None:
            removed.append(label)
            continue
        fields = []
        if was.get('source_file') != is_now.get('source_file'):
            fields.append(f"file {was.get('source_file')} -> {is_now.get('source_file')}")
        if was.get('source_in_ms') != is_now.get('source_in_ms'):
            fields.append(f"IN {was.get('source_in_ms')} -> {is_now.get('source_in_ms')} ms")
        if was.get('source_out_ms') != is_now.get('source_out_ms'):
            fields.append(f"OUT {was.get('source_out_ms')} -> {is_now.get('source_out_ms')} ms")
        if was.get('pause_ms') != is_now.get('pause_ms'):
            fields.append(f"pause {was.get('pause_ms')} -> {is_now.get('pause_ms')} ms")
        # A caption is on screen, so a reworded caption is a real change even when the
        # picture range is identical. Without this, an edit that only touched the
        # wording produced "the script has changed" with no detail at all.
        if (was.get('caption') or '') != (is_now.get('caption') or ''):
            fields.append(f"caption {was.get('caption')!r} -> {is_now.get('caption')!r}")
        if fields:
            moved.append(f"Beat {label} ({', '.join(fields)})")
    parts = []
    if moved:
        shown = '; '.join(moved[:6])
        more = f' and {len(moved) - 6} more' if len(moved) > 6 else ''
        parts.append(f'{len(moved)} beat(s) changed: {shown}{more}')
    if added:
        parts.append(f'{len(added)} beat(s) added: {", ".join(added[:8])}')
    if removed:
        parts.append(f'{len(removed)} beat(s) removed: {", ".join(removed[:8])}')
    detail = ' ' + ' | '.join(parts) + '.' if parts else ''
    slots = len(project.get('scenes') or [])
    return ('The production script has changed since this Beat timeline was built. This '
            'storyboard still plays the picture it was built with'
            + (f', but its {slots} picture slots' if slots else '')
            + ' no longer follow the script, so rebuild the Beat timeline when you want the '
              'current script on screen.' + detail)

