"""Locally transcribed sound-bite speech, stored separately from the original SRT.

A transcript here is machine-generated from the approved source IN/OUT only. It
is not evidence of the speaker's identity, and it never replaces the scripted
on-screen caption or the original uploaded SRT. Every entry is bound to the
exact plan revision, source revision, and source interval it was made from, so
a changed bite invalidates it instead of silently reusing stale words.
"""
from pathlib import Path
import hashlib, json, time

TRANSCRIPT_VERSION = 1


def path(root):
    return Path(root) / 'reviews' / 'documentary_bite_speech.json'


def load(root):
    file = path(root)
    if not file.exists():
        return {'version': TRANSCRIPT_VERSION, 'bites': {}}
    try:
        data = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, ValueError) as exc:
        raise ValueError('Saved sound-bite speech cannot be read safely.') from exc
    if not isinstance(data, dict) or data.get('version') != TRANSCRIPT_VERSION or not isinstance(data.get('bites'), dict):
        raise ValueError('Saved sound-bite speech has an unexpected format.')
    return data


def _key(plan_revision, asset, bite):
    return '|'.join(str(x) for x in (plan_revision, asset.get('id'), asset.get('revision'), bite.get('id'),
                                     bite.get('source_in_s'), bite.get('source_out_s')))


def for_bite(root, plan, bite, asset):
    """Return reviewed speech segments for this exact bite, or an empty list."""
    entry = load(root)['bites'].get(bite.get('id'))
    if not entry:
        return []
    if entry.get('key') != _key(plan.get('revision'), asset, bite) or entry.get('reviewed') is not True:
        return []
    segments = entry.get('segments')
    if not isinstance(segments, list):
        return []
    clean = []
    for segment in segments:
        try:
            start, end = float(segment['start_s']), float(segment['end_s'])
            text = str(segment['text']).strip()
        except (KeyError, TypeError, ValueError):
            return []
        if end <= start or not text:
            return []
        clean.append({'start_s': start, 'end_s': end, 'text': text})
    return clean


def save(root, plan, bite, asset, segments, transcript_meta):
    """Persist one bite's transcribed speech after checking it fits its interval.

    `segments` use bite-relative seconds, matching the exported audio slice.
    """
    length = round(float(bite['source_out_s']) - float(bite['source_in_s']), 3)
    if not isinstance(segments, list) or not segments:
        raise ValueError('The transcription returned no speech for this bite.')
    clean = []
    for segment in segments:
        if not isinstance(segment, dict):
            raise ValueError('A transcribed segment is malformed.')
        try:
            start, end = float(segment['start_s']), float(segment['end_s'])
            text = str(segment['text']).strip()
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('A transcribed segment is malformed.') from exc
        if start < -0.001 or end > length + 0.25 or end <= start or not text:
            raise ValueError('Transcribed speech does not fit the saved bite interval.')
        clean.append({'start_s': round(max(0.0, start), 3), 'end_s': round(end, 3), 'text': text})
    data = load(root)
    data['bites'][bite['id']] = {
        'key': _key(plan.get('revision'), asset, bite),
        'plan_revision': plan.get('revision'), 'asset_id': asset.get('id'),
        'asset_revision': asset.get('revision'), 'source_in_s': bite['source_in_s'],
        'source_out_s': bite['source_out_s'], 'script_beat': bite.get('script_beat'),
        'script_caption': bite.get('script_caption'), 'segments': clean,
        'source': 'local_machine_transcription', 'reviewed': True,
        'reviewed_note': 'Machine transcription of this bite only. Speaker identity, wording and rights require your review.',
        'transcript': transcript_meta if isinstance(transcript_meta, dict) else {},
        'created': time.time(),
    }
    target = path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_suffix('.json.tmp')
    temp.write_text(json.dumps(data, indent=1), encoding='utf-8')
    temp.replace(target)
    return data['bites'][bite['id']]


def cues(root, project, plan, annotated):
    """Bite speech as review subtitle cues on the documentary clock."""
    assets = {a['id']: a for a in project.get('assets', [])}
    bites = {b['id']: b for b in plan.get('bites', [])}
    result = []
    for segment in annotated.get('segments', []):
        if segment.get('kind') != 'sound_bite':
            continue
        bite = bites.get(segment.get('id'))
        asset = assets.get((bite or {}).get('asset_id'))
        if not bite or not asset:
            continue
        for part in for_bite(root, plan, bite, asset):
            start = round((segment['timeline_in_s'] + part['start_s']) * 1000)
            end = round((segment['timeline_in_s'] + part['end_s']) * 1000)
            if end > start:
                result.append({'text': part['text'], 'timeline_in_ms': start, 'timeline_out_ms': end})
    return result


def transcript_digest(segments):
    return hashlib.sha256(json.dumps(segments, sort_keys=True).encode()).hexdigest()
