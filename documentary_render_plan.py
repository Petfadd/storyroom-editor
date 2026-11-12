"""Fail-closed, frame-indexed picture preflight for documentary export.

This module makes no editorial approvals, changes no project files, and does not
render a video by itself. Browser playback is intentionally not used as evidence of accuracy.
"""
from fractions import Fraction
from pathlib import Path
import hashlib, json
from strict_cuts import inspect_video
from documentary_frame_audit import playback_schedule
from strict_cuts import _nearest_frame


def load_decisions(root):
    path = Path(root) / 'reviews' / 'documentary_frame_decisions.json'
    if not path.exists(): return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if data.get('version') != 1 or not isinstance(data.get('changes'), list):
            raise ValueError('Frame decisions cannot be read safely.')
        return data
    except (OSError, ValueError, TypeError) as e:
        raise ValueError('Frame decisions cannot be read safely.') from e


def plan(project, root, sound_plan, decisions=None):
    result = {'picture_compatible': False, 'renderer_implemented': True, 'output_fps': None,
              'spans': [], 'captions': [], 'errors': [], 'adjustments': []}
    try:
        events = playback_schedule(project, root, sound_plan)
        # Captions are opt-in. `burn_captions` stays True unless the project has
        # explicitly turned on-screen script captions off; the timed cues are kept
        # either way so a reviewer can see what was written for each beat.
        burn_captions = (project.get('burn_captions') is not False)
        # The output rate is what every slot must convert to; a source may be a whole
        # multiple of it (59.94 fps in a 29.97 fps documentary) and is converted.
        rates = {Fraction(x.get('output_fps') or x['source_fps']) for x in events}
        if len(rates) != 1:
            raise ValueError('Sources have different frame rates; 1× output without frame conversion is not yet supported.')
        fps = next(iter(rates))
        result['output_fps'] = str(fps)
        # The script's `Cut Action: FADE IN FROM BLACK` (and an optional closing
        # fade) are brightness ramps, so they never move a cut or a frame. They
        # are reported here so the renderer and the reviewer agree on them.
        fades = project.get('documentary_fades') or {}
        if fades.get('enabled') is not False:
            result['fade_in_s'] = max(0.0, min(5.0, float(fades.get('in_s') or 0)))
            result['fade_out_s'] = max(0.0, min(5.0, float(fades.get('out_s') or 0)))
        else:
            result['fade_in_s'] = 0.0
            result['fade_out_s'] = 0.0
        spans = []
        for event in events:
            scale = event.get('picture_scale', 100)
            if not 100 <= scale <= 200:
                raise ValueError(f"{event['label']}: picture scale must be between 100 and 200 percent.")
            # Subtitle boundaries are not picture cuts. Coalesce continuous source
            # frames only when the displayed scale and audio role are unchanged.
            previous = spans[-1] if spans else None
            merge = (previous and event['kind'] == previous['kind'] == 'narration'
                     and event['asset_id'] == previous['asset_id']
                     and scale == previous['picture_scale']
                     and event['source_start_frame'] == previous['source_end_frame']
                     and event.get('source_rate_fps') == previous.get('source_rate_fps')
                     and event['timeline_in_ms'] == previous['timeline_out_ms'])
            if merge:
                previous['source_end_frame'] = event['source_end_frame']
                previous['timeline_out_ms'] = event['timeline_out_ms']
                previous['labels'].append(event['label'])
            else:
                spans.append({'labels': [event['label']], 'kind': event['kind'],
                              'asset_id': event['asset_id'], 'source': event['source'],
                              'source_start_frame': event['source_start_frame'],
                              'source_end_frame': event['source_end_frame'],
                              'timeline_in_ms': event['timeline_in_ms'],
                              'timeline_out_ms': event['timeline_out_ms'],
                              'source_fps': event.get('source_fps'),
                              'source_rate_fps': event.get('source_rate_fps'),
                              'frame_conversion': event.get('frame_conversion', 'none'),
                              'picture_scale': scale})
            result['burn_captions'] = burn_captions
            if not burn_captions:
                continue
            # The script's own On-screen Caption field is what the writer asked to
            # appear on screen, and the supplied production workflow says the editor
            # reads that field as a caption -- for a sound bite as much as for a
            # narration beat. A bite used to be skipped outright, so a script that
            # named its speaker on every bite rendered with no name on any of them.
            # The words SPOKEN inside the bite are still never transcribed here; this
            # only shows the caption the writer wrote for that beat.
            if not event['caption']:
                continue
            if (result['captions'] and result['captions'][-1]['text'] == event['caption']
                and result['captions'][-1]['timeline_out_ms'] == event['timeline_in_ms']):
                result['captions'][-1]['timeline_out_ms'] = event['timeline_out_ms']
            else:
                result['captions'].append({'text': event['caption'], 'timeline_in_ms': event['timeline_in_ms'], 'timeline_out_ms': event['timeline_out_ms']})

        approvals = {}
        if decisions is not None:
            script = (project.get('production_script') or {}).get('text', '')
            if decisions.get('script_sha256') != hashlib.sha256(script.encode()).hexdigest() or decisions.get('plan_revision') != sound_plan.get('revision') or decisions.get('source_fps') != str(fps):
                raise ValueError('Approved frame decisions no longer match the script, sound-bite plan or frame rate.')
            assets = {a['id']: a for a in project.get('assets', [])}
            for change in decisions.get('changes', []):
                asset = assets.get(change.get('asset_id'))
                if not asset or asset.get('revision') != change.get('asset_revision') or (asset.get('original_name') or asset.get('name')) != change.get('source'):
                    raise ValueError('An approved frame decision uses a replaced source.')
                path = (Path(root).resolve()/asset.get('path', '')).resolve()
                if not path.is_relative_to(Path(root).resolve()/'sources') or not path.is_file():
                    raise ValueError('An approved frame decision source is missing.')
                with path.open('rb') as source:
                    digest = hashlib.file_digest(source, 'sha256').hexdigest()
                if digest != change.get('source_sha256'):
                    raise ValueError('An approved frame decision source changed on disk.')
                key = (change.get('asset_id'),change.get('source_start_frame'),change.get('source_end_frame'),change.get('timeline_in_ms'),change.get('timeline_out_ms'))
                if key in approvals or type(change.get('delta')) is not int or change['delta'] not in (-1,1):
                    raise ValueError('Approved frame decisions are invalid or duplicated.')
                approvals[key] = (change, path)
        used = set()
        for span in spans:
            out_start = _nearest_frame(span['timeline_in_ms'], fps)
            out_end = _nearest_frame(span['timeline_out_ms'], fps)
            source_frames = span['source_end_frame'] - span['source_start_frame']
            output_frames = out_end - out_start
            # A coalesced span may join slots built against the same rate; the rate
            # recorded on the span is what its frame indices were counted at.

            raw_source = span['source_end_frame'] - span['source_start_frame']
            if span.get('frame_conversion') == 'halve':
                # Two source frames per output frame. A 59.94 fps slot is shown
                # at 29.97 by keeping every second frame: same picture and timing.
                span['converted_from_frames'] = raw_source
                source_frames = (raw_source + 1)//2
                per_output = 2
            else:
                per_output = 1
            # Rounding each slot against the script clock can land one output frame
            # away from the source count. Correct by at most one output frame and
            # record it; anything larger is a real mismatch and still blocks.
            target = output_frames*per_output
            # Never ask for a frame the source does not contain: check the real
            # frame count before extending a slot by even one frame. Only a real
            # project has files to check; a plan with no sources keeps its arithmetic.
            try:
                import av
                asset_path=(Path(root).resolve()/next(a['path'] for a in project.get('assets',[]) if a['id']==span['asset_id'])).resolve()
                if not asset_path.is_file():raise OSError('source missing')
                with av.open(str(asset_path)) as container:
                    stream=container.streams.video[0]
                    available=int(stream.frames or 0)
                    # Some containers do not declare a frame count (S11.mp4 did
                    # not), which silently disabled the one-frame correction and
                    # blocked an otherwise valid beat. Fall back to the duration.
                    if not available and container.duration:
                        rate=stream.average_rate or stream.guessed_rate
                        if rate:
                            available=int(round(float(container.duration)/1e6*float(rate)))
            except Exception:
                available=0
            if available:
                target = min(target, available - span['source_start_frame'])
            # The tolerance is one output frame, which is ``per_output`` source
            # frames when the source runs at a multiple of the output rate.
            if available and raw_source != target and abs(span['source_end_frame'] - (span['source_start_frame'] + target)) <= per_output:
                wanted = target
                if wanted > raw_source:
                    inspect_video((Path(root).resolve()/next(a['path'] for a in project.get('assets',[]) if a['id']==span['asset_id'])).resolve(), span['source_end_frame'] + (wanted-raw_source))
                span['source_end_frame'] = span['source_start_frame'] + wanted
                result['adjustments'].append({'label':span['labels'][0], 'delta':wanted-raw_source,
                    'original_end_frame':span['source_start_frame']+raw_source, 'adjusted_end_frame':span['source_end_frame'],
                    'source':span['source'], 'reason':'one output frame of slot rounding'})
                if per_output == 1:
                    source_frames = wanted
                else:
                    source_frames = (wanted + 1)//2
            key = (span['asset_id'],span['source_start_frame'],span['source_end_frame'],span['timeline_in_ms'],span['timeline_out_ms'])
            if key in approvals:
                change, path = approvals[key]
                if output_frames-source_frames != change['delta'] or span['labels'][0] != change.get('label'):
                    raise ValueError('An approved frame decision no longer matches the planned cut.')
                if change['delta'] > 0:
                    inspect_video(path, span['source_end_frame'] + 1)
                original_end = span['source_end_frame']
                span['source_end_frame'] += change['delta']
                source_frames += change['delta'];used.add(key)
                result['adjustments'].append({'label':span['labels'][0], 'delta':change['delta'], 'original_end_frame':original_end, 'adjusted_end_frame':span['source_end_frame'], 'source':span['source']})
            span.update(output_start_frame=out_start, output_end_frame=out_end,
                        source_frame_count=source_frames, output_frame_count=output_frames,
                        frame_difference=output_frames-source_frames)
            if output_frames <= 0:
                result['errors'].append(f"{span['labels'][0]}: output interval has no frames.")
            elif source_frames != output_frames:
                result['errors'].append(f"{span['labels'][0]}: {source_frames} approved source frames do not fit {output_frames} output frames at {fps} fps. No frames were added, dropped or repeated.")
        if len(used) != len(approvals):
            raise ValueError('An approved frame decision no longer matches the storyboard.')
        result['spans'] = spans
        result['picture_compatible'] = not result['errors']
    except (ValueError, KeyError, TypeError, ZeroDivisionError, OSError) as e:
        result['errors'].append(str(e))
    return result
