"""Group documentary picture slots by original SRT cue without changing cuts."""
from pathlib import Path
from documentary import annotate_plan, load_plan
from documentary_render_plan import plan as frame_plan, load_decisions


def groups(project):
    blocks = (project.get('srt') or {}).get('blocks') or []
    scenes = project.get('scenes') or []
    if not blocks or not scenes:
        raise ValueError('The original SRT and documentary picture slots are required.')
    numbers = [b.get('number') for b in blocks]
    if len(set(numbers)) != len(numbers):
        raise ValueError('Original SRT cue numbers are not unique.')
    result = []
    for block in blocks:
        result.append({'cue': block['number'], 'text': block['text'],
                       'cue_start_ms': block['start_ms'], 'cue_end_ms': block['end_ms'], 'slots': []})
    positions = {g['cue']: i for i, g in enumerate(result)}
    last_index = -1
    for i, scene in enumerate(scenes):
        row = scene.get('plan_row') or {}
        cue = row.get('srt_blocks') or []
        if len(cue) != 1 or cue[0] not in positions:
            raise ValueError(f'Picture slot {i+1} has no matching original SRT cue.')
        index = positions[cue[0]]
        if index < last_index:
            raise ValueError('Documentary picture slots are not in original SRT cue order.')
        last_index = index
        result[index]['slots'].append({'scene': i+1, 'id': scene['id'], 'asset_id': scene.get('asset_id'),
                                       'source_file': row.get('source_file'), 'source_in_ms': row.get('source_in_ms'),
                                       'source_out_ms': row.get('source_out_ms'), 'timeline_in_ms': row.get('timeline_in_ms'),
                                       'timeline_out_ms': row.get('timeline_out_ms'), 'continuity': row.get('continuity')})
    for group in result:
        if not group['slots']:
            raise ValueError(f"Original SRT cue {group['cue']} has no picture slot.")
    return result


def report(project, root, progress=None):
    """Validate both grouped cue order and every physical scripted source range."""
    result = {'required': True, 'checked_version': project.get('updated'), 'mode': 'documentary-cue-groups',
              'shots': [], 'errors': [], 'adjustments': [], 'physical_slots': len(project.get('scenes') or [])}
    try:
        grouped = groups(project)
        if progress: progress(0, len(grouped))
        sound = annotate_plan(project, Path(root), load_plan(root))
        check = frame_plan(project, Path(root), sound, load_decisions(root))
        result['errors'].extend(check['errors'])
        result['adjustments'] = [f"{x['label']}: {'add' if x['delta'] > 0 else 'omit'} one explicitly approved source frame." for x in check['adjustments']]
        result['fps'] = check['output_fps']
        result['shots'] = grouped
        if progress: progress(len(grouped), len(grouped))
    except (ValueError, OSError, KeyError, TypeError) as e:
        result['errors'].append(str(e))
    return result
