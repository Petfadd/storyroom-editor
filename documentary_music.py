"""Optional background music for a documentary, kept separate from narration.

The upload is normalised into the project's own folder and played at one level
for the whole film: it is not ducked under the narration and not cut under a
sound bite. The narration, the sound-bite audio and the original SRT are never
altered.
"""
from pathlib import Path
import re, subprocess, uuid

import av
import imageio_ffmpeg

MUSIC_VERSION = 'music-bed-continuous-v2'
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
    # The bed is continuous: it never stops and never dips, under the narration
    # or under a sound bite. `narration_level` and `speech_mode` are still
    # reported, as 'full', because saved projects and older clients read them.
    saved = music.get('narration_level') or music.get('speech_mode')
    narration = 'full' if saved in (None, '', 'full', 'duck', 'mute') else 'full'
    return {'enabled': bool(music.get('enabled')), 'volume_percent': int(music.get('volume_percent') or 35),
            'narration_level': narration, 'speech_mode': narration,
            'bite_cut': False,
            'present': bool(music.get('name'))}


def target_lufs(level_percent):
    """Map the volume percentage to the bed's own loudness.

    100% is an assertive -24 LUFS bed, 35% sits around -32 LUFS, and the floor is
    -42 LUFS. This is the level the bed plays at for the whole film.
    """
    percent = max(1, min(100, int(level_percent)))
    return round(max(-42.0, min(-18.0, -42.0 + percent * 0.18)), 2)


def build_bed(project, music_file, destination, duration_s, segments, level_percent=None, duck=False, work_dir=None, narration_level=None):
    """Loop and level the music, then cut it to an exact-length WAV bed.

    The bed is continuous: it is neither dipped under the narration nor cut under
    a sound bite, so the score runs unbroken from the first frame to the last.
    `segments` and `narration_level` are still accepted because saved projects
    and older callers pass them; they no longer change the gain.
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
    narration_level = narration_level if narration_level in ('mute', 'duck') else settings(project)['narration_level']
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    work = Path(work_dir) if work_dir else destination.parent
    work.mkdir(parents=True, exist_ok=True)
    looped = work / ('music-loop-' + uuid.uuid4().hex + '.wav')
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
            'ducked_under_speech': False,
            'narration_level': narration_level,
            'speech_mode': narration_level,
            'narration_windows': [],
            'mute_windows': [],
            'music_version': MUSIC_VERSION}
