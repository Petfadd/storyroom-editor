"""Low-resolution editing copies of uploaded video.

Why this exists: a 4K source is roughly sixteen times the pixels of 1080p. Scrub a
timeline of them and the browser drops frames, the seek takes a second, and playback
stutters -- while the machine spends its time decoding detail nobody can see in a
900-pixel-wide monitor.

A proxy is a 540p or 720p copy used for *playback and scrubbing only*. The export path
never touches one: `engine.py` opens the source files directly from disk, never over
HTTP, so a proxy cannot reach a delivered frame even by accident. That is a structural
guarantee rather than a setting someone has to remember, and it is why proxies are safe
to make aggressive.

What is preserved in a proxy, because editing depends on it:

* duration and frame rate -- the timeline is timed from these
* the audio track -- narration timing and ducking are read from it
* rotation metadata -- so a phone clip is not sideways in the editor
* timestamps -- set from the source, not regenerated

What is not preserved, because nothing depends on it: resolution, bit depth, and the
exact bytes. A proxy is disposable.

Proxies are only made when they would actually help. A source that is already small is
used as its own proxy, which costs nothing and avoids a pointless encode.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import cache
import resources

# Source heights at or below this are already cheap to decode, so they are not proxied.
# 720p playback of a 720p file gains nothing and would only add a transcode and a copy.
MIN_HEIGHT_TO_PROXY = 900

QUALITY_HEIGHTS = {'540p': 540, '720p': 720}

DEFAULT_QUALITY = '540p'


def probe(ffmpeg: str, path: Path) -> dict:
    """Height, width, frame rate and duration of a video, straight from ffmpeg."""
    try:
        out = subprocess.run([ffmpeg, '-hide_banner', '-i', str(path)],
                             capture_output=True, text=True, timeout=60).stderr
    except Exception:
        return {}
    info = {'width': 0, 'height': 0, 'fps': 0.0, 'duration': 0.0, 'audio': False}
    for line in out.splitlines():
        line = line.strip()
        if line.startswith('Duration:'):
            try:
                stamp = line.split('Duration:')[1].split(',')[0].strip()
                h, m, s = stamp.split(':')
                info['duration'] = int(h) * 3600 + int(m) * 60 + float(s)
            except (IndexError, ValueError):
                pass
        if 'Video:' in line and 'Stream' in line:
            try:
                for token in line.split(','):
                    token = token.strip()
                    if 'x' in token and token.split('x')[0].strip().isdigit():
                        w, h = token.split('x')[:2]
                        info['width'] = int(w)
                        info['height'] = int(h.split()[0])
                        break
                if ' fps' in line:
                    info['fps'] = float(line.split(' fps')[0].split()[-1])
            except (ValueError, IndexError):
                pass
        if 'Audio:' in line and 'Stream' in line:
            info['audio'] = True
    return info


def needs_proxy(info: dict) -> bool:
    """Only large sources benefit. A small file is left alone."""
    return (info.get('height') or 0) > MIN_HEIGHT_TO_PROXY


def target_height(source_height: int, quality: str) -> int:
    """The proxy height for a source, never larger than the source itself."""
    wanted = QUALITY_HEIGHTS.get(quality, QUALITY_HEIGHTS[DEFAULT_QUALITY])
    return min(wanted, source_height)


def build(ffmpeg: str, source: Path, destination: Path, quality: str = DEFAULT_QUALITY,
          info: dict | None = None, context=None) -> dict:
    """Transcode one proxy. Returns what was produced, or raises on failure.

    Written to a temporary name and moved into place only when complete, so an
    interrupted run can never leave a half-file that looks like a finished proxy.
    """
    source = Path(source)
    destination = Path(destination)
    info = info or probe(ffmpeg, source)
    if not info.get('height'):
        raise RuntimeError('The video could not be read, so no editing copy was made.')
    height = target_height(info['height'], quality)
    # -2 keeps the width even and proportional, which every player and the encoder need.
    scale = f'scale=-2:{height}:flags=fast_bilinear'

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.with_name(destination.stem + '.part' + destination.suffix)

    encoder = resources.encoder_for('proxy')
    cmd = [ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
           '-i', str(source),
           '-map', '0:v:0', '-map', '0:a:0?',
           '-vf', scale,
           *encoder,
           '-preset', 'veryfast', '-crf', '26',
           '-c:a', 'aac', '-b:a', '128k', '-ac', '2',
           # Keep the source clock rather than regenerating one: the timeline, the
           # narration and the ducking are all timed from these numbers. This is
           # -fps_mode in ffmpeg 6+, where -vsync was removed; the older flag is not
           # accepted at all by the bundled build.
           '-fps_mode', 'passthrough',
           '-movflags', '+faststart',
           str(temp)]
    if context is not None:
        context.set(5, 'Preparing an editing copy')
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    except subprocess.SubprocessError as error:
        temp.unlink(missing_ok=True)
        raise RuntimeError(f'The editing copy could not be made: {error}') from error
    if result.returncode != 0 or not temp.exists() or temp.stat().st_size < 1024:
        temp.unlink(missing_ok=True)
        detail = (result.stderr or '').strip().splitlines()
        raise RuntimeError('The editing copy could not be made'
                           + (': ' + detail[-1][:200] if detail else '.'))
    temp.replace(destination)

    made = probe(ffmpeg, destination)
    return {'path': str(destination), 'height': made.get('height') or height,
            'width': made.get('width') or 0, 'quality': quality,
            'duration': made.get('duration') or info.get('duration'),
            'fps': made.get('fps') or info.get('fps'),
            'audio': made.get('audio', False)}


def metadata(store: cache.Store, source: Path) -> dict | None:
    """The recorded description of an existing proxy, if it is still current."""
    name = cache.entry_key(source, 'proxy')
    if not store.has('proxies', name, '.json'):
        return None
    data = store.read_json('proxies', name)
    if not data or not Path(data.get('path') or '').is_file():
        return None
    return data


def ensure(ffmpeg: str, store: cache.Store, source: Path, quality: str = DEFAULT_QUALITY,
           context=None) -> dict | None:
    """The proxy for a source, building it first if needed.

    Returns None when the source is small enough that a proxy would be pointless, which
    callers treat as "serve the original".
    """
    source = Path(source)
    info = probe(ffmpeg, source)
    if not needs_proxy(info):
        return None
    name = cache.entry_key(source, 'proxy')
    existing = metadata(store, source)
    if existing and existing.get('quality') == quality:
        store.touch('proxies', name, '.json')
        return existing
    built = build(ffmpeg, source, store.path('proxies', name, '.mp4'), quality,
                  info=info, context=context)
    store.write_json('proxies', name, built)
    return built
