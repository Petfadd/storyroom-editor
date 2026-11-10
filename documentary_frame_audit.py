"""Frame-boundary audit for a Beat storyboard, without changing its timeline.

This is a preflight primitive for a future documentary player, not a renderer.
"""
from fractions import Fraction
from pathlib import Path
from documentary_storyboard import verified_source_interval
from documentary_beats import read_beats
from strict_cuts import _nearest_frame, source_rate, inspect_video


def audit(project, root, plan):
    root = Path(root).resolve()
    assets = {a['id']: a for a in project.get('assets', [])}
    result = {'parts': [], 'errors': [], 'verified': False}
    required = {}
    try:
        beats = read_beats(project['production_script']['text'])
        scripted = {b['label']: b for b in beats if b['kind'] == 'sound_bite'}
        bites = plan.get('bites', [])
        if len(bites) != len(scripted) or len({b.get('script_beat') for b in bites}) != len(bites):
            raise ValueError('Saved sound bites do not match the script.')
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
            if not beat or not asset or (asset.get('original_name') or asset.get('name')) != beat['source_file'] or round(bite.get('pause_s', -1)*1000) != beat['pause_ms'] or round(bite.get('source_in_s', -1)*1000) != beat['source_in_ms'] or round(bite.get('source_out_s', -1)*1000) != beat['source_out_ms']:
                raise ValueError('A saved sound bite differs from its script; playback must not silently change it.')
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


def playback_schedule(project, root, plan):
    """Create a frame-indexed review playback schedule, without rendering media.

    Narration slots and bites retain the exact script clock. A consumer must still
    reconcile snapped frame durations with that clock; this function does not imply
    those durations are compatible or authorize changing playback speed.
    """
    checked = audit(project, root, plan)
    if not checked['verified']:
        raise ValueError('; '.join(checked['errors']))
    beats = {b['label']: b for b in read_beats(project['production_script']['text'])}
    bites = sorted(plan['bites'], key=lambda b: (b['pause_s'], b['created']))
    frames = checked['parts']; scenes = project['scenes']; events = []
    def text(value):
        return '' if not value or value.strip().lower() == 'none' else value.strip()
    for i, scene in enumerate(scenes):
        row = scene['plan_row']; start, end = row['timeline_in_ms'], row['timeline_out_ms']
        label = row['continuity'].removeprefix('Documentary beat ')
        expected = '' if label == 'approved-gap' else text(beats[label]['caption'])
        if text(row.get('required_caption')) != expected:
            raise ValueError(f'Scene {i+1}: scripted caption has changed.')
        if any(start < round(b['pause_s']*1000) < end for b in bites):
            raise ValueError(f'Scene {i+1}: sound-bite handoff falls inside the picture slot.')
        offset = sum(round((b['source_out_s']-b['source_in_s'])*1000) for b in bites if round(b['pause_s']*1000) <= start)
        part = dict(frames[i])
        # A scene is one physical slot; the audited frame count is authoritative for
        # its source indices, even when the storyboard recorded a different rate.
        part['source_fps'] = part['source_rate_fps']
        part.update(kind='narration', scene_id=scene['id'], asset_id=scene['asset_id'], timeline_in_ms=start+offset, timeline_out_ms=end+offset, caption=expected, picture_scale=int(scene.get('picture_scale') or project.get('picture_scale') or 100))
        events.append(part)
    for j, bite in enumerate(plan['bites']):
        label = bite['script_beat']; beat = beats[label]
        if text(bite.get('script_caption')) != text(beat['caption']):
            raise ValueError(f'Beat {label}: scripted caption has changed.')
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
