"""Validate a generated Storyroom production package before a new project is built.

Uses the editor's real SRT, Markdown beat-table and EDL parsers. This checks
documents/timing/source availability only; it is NOT face recognition, rights
clearance, or visual proof of what a shot depicts.
"""
from __future__ import annotations
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import app as studio
from document_plan import rows_for
from production import matching_assets


def probe_duration(path):
    try:
        info = studio.probe(path)
        return float(info.get('duration') or 0), info.get('has_video', False)
    except Exception as exc:
        return 0.0, False


def load_sources(arg):
    path = Path(arg)
    if path.is_file() and path.suffix.lower() == '.json':
        data = json.loads(path.read_text(encoding='utf-8'))
        return {str(k): float(v) for k, v in data.items()}
    if not path.is_dir():
        raise RuntimeError('Source argument must be a directory of MP4s or a JSON map of filename -> duration.')
    result = {}
    for f in sorted(path.iterdir()):
        if f.is_file() and f.suffix.lower() in ['.mp4', '.mov']:
            duration, has_video = probe_duration(f)
            if has_video and duration > 0:
                result[f.name] = duration
    return result


def build_project(script_text, srt_text, source_durations):
    blocks = studio.parse_srt(srt_text)
    assets = []
    for i, (name, duration) in enumerate(source_durations.items(), 1):
        assets.append({
            'id': 'validation%03d' % i, 'name': name, 'original_name': name,
            'path': name, 'kind': 'video', 'duration': duration,
            'source': 'validation-only', 'people': [], 'category': 'general',
            'verified': True, 'rights': 'reviewed_other', 'notes': 'Validation placeholder.',
            'derived_from': None
        })
    return {
        'id': 'validation', 'schema_version': 1, 'title': 'Production package validation',
        'brief': '', 'people': [], 'assets': assets, 'scenes': [],
        'voiceover': {'name': 'narration', 'path': 'voiceover', 'duration': blocks[-1]['end_ms'] / 1000},
        'srt': {'blocks': blocks, 'name': 'captions.srt'},
        'production_script': {'name': 'production_script.md', 'text': script_text},
        'source_bindings': {}, 'picture_policy': 'clips_only',
        'transition_policy': 'clean_cuts_only',
        'repetition_policy': {'cooldown_slots': 6, 'source_interval_overlap_forbidden_within_window': True, 'visual_hash_max_distance': 4},
        'auto_assemble_uploads': True, 'manual_scene_edits': False
    }


def validate(script_path, srt_path, source_arg):
    script_text = Path(script_path).read_text(encoding='utf-8-sig')
    srt_text = Path(srt_path).read_text(encoding='utf-8-sig')
    source_durations = load_sources(source_arg)
    p = build_project(script_text, srt_text, source_durations)
    errors, warnings = [], []
    try:
        rows, mode = rows_for(p)
    except Exception as exc:
        return {'ready': False, 'errors': ['Parser rejected the production script: ' + str(exc)], 'warnings': []}
    if mode not in ['production_script_table', 'srt_block_map']:
        errors.append('No recognized Markdown beat table was found. Check the exact header row and the Unicode arrow →.')
    if not rows:
        errors.append('The beat table produced zero rows; a new project would build zero scenes.')
        return {'ready': False, 'errors': errors, 'warnings': warnings, 'mode': mode}
    blocks = p['srt']['blocks']
    expected_numbers = list(range(1, len(blocks) + 1))
    last_ms = blocks[-1]['end_ms']
    expected_frames = round(last_ms * .03)
    if rows[0]['timeline_in_ms'] != 0:
        errors.append('First beat does not start at 0 ms.')
    if rows[-1]['timeline_out_ms'] != last_ms:
        errors.append('Final beat OUT ' + str(rows[-1]['timeline_out_ms']) + ' ms does not equal final SRT end ' + str(last_ms) + ' ms.')
    if round(rows[-1]['timeline_out_ms'] * .03) != expected_frames:
        errors.append('Final 30 fps frame count does not match the SRT timeline.')
    covered = []
    for i, row in enumerate(rows, 1):
        pre = 'Beat ' + str(row.get('beat_id', i)) + ': '
        if i > 1 and row['timeline_in_ms'] != rows[i - 2]['timeline_out_ms']:
            errors.append(pre + 'timeline is not contiguous with the previous beat.')
        if row['timeline_out_ms'] <= row['timeline_in_ms']:
            errors.append(pre + 'empty or reversed timeline interval.')
        refs = row.get('srt_blocks') or []
        covered.extend(refs)
        if str(row.get('transition_out', 'cut')).lower() != 'cut':
            errors.append(pre + 'transition is not a clean cut.')
        if int(row.get('transition_handle_frames') or 0):
            errors.append(pre + 'clean cut has a transition handle.')
        source = str(row.get('source_file') or '').strip()
        if not source:
            errors.append(pre + 'no native MP4 filename.');continue
        matches = matching_assets(p, source)
        if len(matches) != 1:
            errors.append(pre + str(len(matches)) + ' uploaded files match ' + source + '; expected exactly one.');continue
        duration = float(matches[0]['duration'])
        sin = row.get('source_in_ms'); sout = row.get('source_out_ms')
        if sin is None or sout is None or sout <= sin:
            errors.append(pre + 'missing or invalid source IN/OUT milliseconds.');continue
        beat_duration = row['timeline_out_ms'] - row['timeline_in_ms']
        if abs((sout - sin) - beat_duration) > 1:
            errors.append(pre + 'source interval duration differs from the timeline beat by more than 1 ms.')
        if sin < 0 or sout / 1000 > duration + .034:
            errors.append(pre + 'source interval exceeds ' + source + ' (' + str(round(duration, 3)) + ' s).')
        if str(row.get('visual_status', '')).upper() == 'STILL':
            warnings.append(pre + 'document status says STILL; the clips-only builder converts it to a native MP4 interval. Verify that the MP4 actually shows the intended content.')
        for blocker in row.get('import_blockers') or []:
            errors.append(pre + 'import blocker: ' + str(blocker))
    if sorted(set(covered)) != expected_numbers or len(covered) != len(set(covered)):
        errors.append('SRT blocks are not covered exactly once in order. Covered=' + str(sorted(set(covered)))[:300])
    overlaps = []
    for i, row in enumerate(rows):
        source = str(row.get('source_file') or '')
        sin = row.get('source_in_ms') or 0
        sout = row.get('source_out_ms') or 0
        group = row.get('footage_group') or ''
        for j in range(max(0, i - 6), i):
            prev = rows[j]
            psource = str(prev.get('source_file') or '')
            pin = prev.get('source_in_ms') or 0
            pout = prev.get('source_out_ms') or 0
            pgroup = prev.get('footage_group') or ''
            same_group = bool(group and group == pgroup)
            same_overlap = source and source == psource and sin < pout - 1 and sout > pin + 1
            if same_group or same_overlap:
                overlaps.append({'beat': i + 1, 'repeats_beat': j + 1, 'source_file': source, 'footage_group': group})
                break
    if overlaps:
        errors.append('Six-slot rule: ' + str(len(overlaps)) + ' overlapping source interval / footage-group repeats found.')
    report = {
        'ready': not errors, 'mode': mode, 'beat_rows': len(rows), 'srt_blocks': len(blocks),
        'first_timeline_ms': rows[0]['timeline_in_ms'], 'last_timeline_ms': rows[-1]['timeline_out_ms'],
        'expected_30fps_frames': expected_frames, 'source_files': sorted(source_durations),
        'six_slot_overlap_violations': overlaps, 'warnings': warnings, 'errors': errors,
        'scope': 'Document, timing and source-availability validation only; not face recognition or visual identity verification.'
    }
    return report


def main():
    if len(sys.argv) != 4:
        print('Usage: python validate_production_package.py <production_script.md> <captions.srt> <source_dir_or_duration_map.json>')
        sys.exit(2)
    report = validate(sys.argv[1], sys.argv[2], sys.argv[3])
    print(json.dumps(report, indent=2))
    sys.exit(0 if report['ready'] else 1)


if __name__ == '__main__':
    main()
