"""Optional background music for a documentary, kept separate from narration.

The upload is normalised into the project's own folder, mixed at a low bed level,
and ducked under every narration and footage-speech window. The narration, the
sound-bite audio and the original SRT are never altered.
"""
from pathlib import Path
import re, subprocess, uuid

import av
import imageio_ffmpeg

MUSIC_VERSION = 'music-bed-autoduck-v1'
ALLOWED_SUFFIXES = {'.mp3', '.wav', '.m4a', '.aac', '.flac', '.ogg', '.opus'}
MAX_BYTES = 200 * 1024 * 1024


def _run(args):
    result = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=1800)
    if result.returncode:
        raise ValueError('Background music could not be processed: ' + result.stderr[-500:])


def music_path(root):
    """The saved upload, keeping its real extension so it can still be decoded."""
    folder = Path(root) / 'inputs'
    marker = folder / 'background_music.name'
    try:
        name = marker.read_text(encoding='utf-8').strip()
    except OSError:
        name = ''
    if name and re.fullmatch(r'background_music\.[a-z0-9]{1,5}', name) and (folder / name).is_file():
        return folder / name
    for suffix in sorted(ALLOWED_SUFFIXES):
        candidate = folder / ('background_music' + suffix)
        if candidate.is_file():
            return candidate
    return folder / 'background_music'


def probe_duration(path):
    with av.open(str(path)) as media:
        if not media.streams.audio:
            raise ValueError('That file has no audio track.')
        stream = media.streams.audio[0]
        if stream.duration is not None and stream.time_base is not None:
            return float(stream.duration * stream.time_base)
        if media.duration is not None:
            return float(media.duration / av.time_base)
    raise ValueError('The music length could not be read.')


def save_upload(root, stream, filename):
    """Store the uploaded music privately, checking it really is audio."""
    suffix = Path(filename or '').suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise ValueError('Upload an audio file: ' + ', '.join(sorted(ALLOWED_SUFFIXES)) + '.')
    target_dir = Path(root) / 'inputs'
    target_dir.mkdir(parents=True, exist_ok=True)
    temp = target_dir / ('music-' + uuid.uuid4().hex + suffix)
    # Werkzeug's FileStorage saves in chunks; a raw stream is copied instead.
    if hasattr(stream, 'save'):
        stream.save(str(temp))
    else:
        with temp.open('wb') as handle:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                if handle.tell() > MAX_BYTES:
                    handle.close()
                    temp.unlink(missing_ok=True)
                    raise ValueError('The music file is larger than 200 MB.')
    size = temp.stat().st_size
    if not size:
        temp.unlink(missing_ok=True)
        raise ValueError('The uploaded music file is empty.')
    if size > MAX_BYTES:
        temp.unlink(missing_ok=True)
        raise ValueError('The music file is larger than 200 MB.')
    try:
        duration = probe_duration(temp)
    except ValueError:
        temp.unlink(missing_ok=True)
        raise
    if duration < 1:
        temp.unlink(missing_ok=True)
        raise ValueError('The music is shorter than one second.')
    final = music_path(root)
    final.parent.mkdir(parents=True, exist_ok=True)
    keep = target_dir / ('background_music' + suffix)
    # One bed per project, always. A new upload replaces the previous track
    # outright, including when the extension changes, so no older file can be
    # picked up by the extension fallback in `music_path`.
    temp.replace(keep)
    # Sweep after the replacement, and never touch the file just written: the
    # staging name also lives in this folder while the upload is in flight.
    for stale in target_dir.glob('background_music.*'):
        if stale.name not in (keep.name, 'background_music.name'):
            stale.unlink(missing_ok=True)
    for leftover in target_dir.glob('music-*'):
        if leftover != keep:
            leftover.unlink(missing_ok=True)
    if final != keep and final.exists():
        final.unlink()
    (target_dir / 'background_music.name').write_text(keep.name, encoding='utf-8')
    return {'name': Path(filename).name, 'size': size, 'duration_s': round(duration, 3),
            'path': str(keep.relative_to(Path(root))), 'version': MUSIC_VERSION}


def settings(project):
    music = (project.get('documentary_music') or {})
    # A sound bite is always silent, so there is no duck-under-bites setting any
    # more. `narration_level` is the only choice left: 'mute' stops the bed under
    # the narration, 'duck' dips it. `speech_mode` and `duck_under_speech` are
    # still reported because saved projects and older clients read them.
    # Dipping under the narration is the default, because the bed should be
    # audible under continuous speech. Stopping the bed under the narration as
    # well is the opt-in, and a project that already chose it keeps it: older
    # saves wrote the same choice as `speech_mode`. A bite is cut either way.
    saved = music.get('narration_level') or music.get('speech_mode')
    narration = 'mute' if saved == 'mute' else 'duck'
    return {'enabled': bool(music.get('enabled')), 'volume_percent': int(music.get('volume_percent') or 35),
            'narration_level': narration, 'speech_mode': narration,
            'bite_cut': True,
            'present': bool(music.get('name'))}


def _windows(kinds, segments, duration_s, padding_s):
    windows = []
    for segment in segments:
        if segment.get('kind') not in kinds:
            continue
        start = float(segment['timeline_in_s'])
        end = float(segment['timeline_out_s'])
        windows.append((max(0.0, start - padding_s), min(duration_s, end + padding_s)))
    windows.sort()
    merged = []
    for start, end in windows:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def speed_windows(segments, duration_s, padding_s=0.12):
    """Windows that already carry narration or footage speech, on the final clock."""
    return _windows(('narration', 'sound_bite'), segments, duration_s, padding_s)


def speech_windows(segments, duration_s, padding_s=0.12):
    """Narration and chosen footage speech, kept apart.

    A footage sound bite carries the only audio in its span, so the bed is
    silent there and no setting can change that. The narration is continuous,
    so the bed only follows the narration setting: dip, or stop.
    """
    return {'narration': _windows(('narration',), segments, duration_s, padding_s),
            'bite': _windows(('sound_bite',), segments, duration_s, padding_s)}


def _speech_gain(windows, duration_s, narration_ratio=0.355):
    """Piecewise constant gain over the whole timeline, step at each boundary.

    A sound bite is always silent: the bite gain is fixed at 0.0 and is not a
    parameter, so no saved setting can put music under a bite. Narration either
    dips to `narration_ratio` or, when that is 0.0, stops as well.
    """
    points = {0.0, float(duration_s)}
    for group in windows.values():
        for start, end in group:
            if 0.0 < start < duration_s:
                points.add(start)
            if 0.0 < end < duration_s:
                points.add(end)
    edges = sorted(points)
    parts = []
    def overlaps(group):
        # Every boundary is an edge, so any overlap covers this whole slice.
        return any(min(b, e) - max(a, s) > 1e-9 for s, e in group)
    for a, b in zip(edges, edges[1:]):
        if overlaps(windows['bite']):
            ratio = 0.0
        elif overlaps(windows['narration']):
            ratio = narration_ratio
        else:
            ratio = 1.0
        if abs(ratio - 1.0) > 1e-6:
            parts.append((a, b, ratio))
    # Joining neighbours with the same gain keeps the filter expression short.
    merged = []
    for a, b, ratio in parts:
        if merged and merged[-1][2] == ratio and abs(merged[-1][1] - a) < 1e-9:
            merged[-1] = (merged[-1][0], b, ratio)
        else:
            merged.append((a, b, ratio))
    return merged


def target_lufs(level_percent):
    """Map the volume percentage to a bed loudness under narration.

    100% is an assertive -24 LUFS bed, 35% sits around -32 LUFS, and the floor is
    -42 LUFS. Ducking then takes it a further 9 dB under any speech.
    """
    percent = max(1, min(100, int(level_percent)))
    return round(max(-42.0, min(-18.0, -42.0 + percent * 0.18)), 2)


def build_bed(project, music_file, destination, duration_s, segments, level_percent=None, duck=True, work_dir=None, narration_level=None):
    """Loop and level the music, then cut it to an exact-length WAV bed.

    A sound bite is always silent; that is not a setting. `narration_level` only
    decides what happens under the narration: 'mute' stops the bed there as well,
    'duck' dips it.
    """
    music_file = Path(music_file).resolve()
    destination = Path(destination).resolve()
    if not music_file.is_file():
        raise ValueError('The background music file is missing.')
    level_percent = int(level_percent if level_percent is not None else settings(project)['volume_percent'])
    if not 1 <= level_percent <= 100:
        raise ValueError('Music volume must be a whole percentage from 1 to 100.')
    if duration_s <= 0:
        raise ValueError('The documentary has no duration for music.')
    # No argument means "use the project's saved choice", not a hardcoded mode:
    # otherwise the export would ignore the setting the user just reviewed.
    narration_level = narration_level if narration_level in ('mute', 'duck') else settings(project)['narration_level']
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    work = Path(work_dir) if work_dir else destination.parent
    work.mkdir(parents=True, exist_ok=True)
    looped = work / ('music-loop-' + uuid.uuid4().hex + '.wav')
    # A bite is silent whatever the narration setting is: the bite's own speech
    # is never competing with music.
    windows = speech_windows(segments, duration_s) if duck else {'narration': [], 'bite': []}
    try:
        # `aloop` loops in the filter graph and `atrim` cuts an exact length;
        # `-stream_loop` with `-t` can end several frames short of the target.
        _run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(music_file),
              '-vn', '-filter_complex',
              f'[0:a]aloop=loop=-1:size=2e9,atrim=0:{duration_s:.3f},asetpts=PTS-STARTPTS,aresample=48000,'
              f'aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo[a]',
              '-map', '[a]', '-c:a', 'pcm_s16le', str(looped)])
        from documentary_audio import measure_loudness
        measured = measure_loudness(ff, looped)['integrated_lufs']
        want = target_lufs(level_percent)
        gain_db = max(-30.0, min(30.0, want - measured))
        filters = [f'volume={gain_db:.3f}dB']
        if windows['narration'] or windows['bite']:
            # Bites are set to zero by `_speech_gain`. The narration either dips to
            # 0.355 or stops. The step is instant, so the bed is already down when
            # a bite's speech starts.
            narration_ratio = 0.355 if narration_level == 'duck' else 0.0
            parts = _speech_gain({'narration': windows['narration'], 'bite': windows['bite']},
                                 duration_s, narration_ratio)
            expr = ''.join(f'between(t,{a:.3f},{b:.3f})*({r:.4f}-1)+' for a, b, r in parts)
            if expr:
                filters.append("volume=volume='" + expr + "1':eval=frame")
        filters.append(f'atrim=0:{duration_s:.3f}')
        filters.append('alimiter=limit=0.891251:level=0:latency=1')
        filters.append('aresample=48000')
        filters.append('aformat=sample_fmts=s16:sample_rates=48000:channel_layouts=stereo')
        _run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(looped), '-af', ','.join(filters),
              '-c:a', 'pcm_s16le', str(destination)])
    finally:
        looped.unlink(missing_ok=True)
    with av.open(str(destination)) as media:
        stream = media.streams.audio[0]
        length = float(stream.duration * stream.time_base) if stream.duration is not None else 0
    if abs(length - duration_s) > 0.05:
        destination.unlink(missing_ok=True)
        raise ValueError('The music bed length does not match the timeline.')
    return {'duration_s': length, 'level_percent': level_percent, 'nominal_lufs': target_lufs(level_percent),
            'measured_source_lufs': round(measured, 2), 'gain_db': round(gain_db, 2),
            'ducked_under_speech': bool(windows['narration'] or windows['bite']),
            'narration_level': narration_level,
            'speech_mode': narration_level,
            'narration_windows': [{'start_s': round(a, 3), 'end_s': round(b, 3)} for a, b in windows['narration']],
            'mute_windows': [{'start_s': round(a, 3), 'end_s': round(b, 3)} for a, b in windows['bite']],
            'music_version': MUSIC_VERSION}
