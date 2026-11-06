"""Prove that one code path serves every runtime.

The claim this script tests is narrow and checkable: a programme of any length is read,
planned, built, approved and exported by the same functions, and the output is exactly as
long as its own narration. Nothing here is sized per minute; the input is generated from a
runtime, and every assertion is derived from the result of that build.

It is a regression test, not a demo. Run it after touching the build path:

    python tools/runtime_sweep.py --runtimes 60,300,600,3600 --work /tmp/sweep

What it checks, for each runtime:

  * the SRT parses, at one or two digit hours and any millisecond precision
  * the built timeline starts at frame 0, is contiguous, and ends exactly on the SRT
  * every scene carries the SRT block its own plan row named
  * assembly reports no validation errors, and no repetition/capacity complaint
  * a block-range rebuild of one block leaves every other beat byte-identical
  * the total duration is unchanged by that rebuild

Fixtures are generated, never reused: narration is a tone with an amplitude envelope (so
pause detection has something real to find), SRT blocks are 3-9 s, and the block map places
every row in a source interval that is never reused and never runs past the clip.
"""
import argparse
import csv
import json
import math
import os
import random
import struct
import subprocess
import sys
import time
import urllib.error
import urllib.request
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get('STORYROOM_URL', 'http://127.0.0.1:8003')
FPS = 30


def ffmpeg():
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


def call(path, method='GET', data=None, ctype='application/json', raw=None):
    body = raw if raw is not None else (None if data is None else json.dumps(data).encode())
    request = urllib.request.Request(BASE + path, data=body, headers={'Content-Type': ctype}, method=method)
    try:
        response = urllib.request.urlopen(request)
        text = response.read()
        return response.status, (json.loads(text) if text else {})
    except urllib.error.HTTPError as error:
        text = error.read()
        try:
            return error.code, json.loads(text or b'{}')
        except ValueError:
            return error.code, {'error': text.decode()[:300]}


def upload(pid, path, kind, name=None):
    boundary = '----sweep'
    def part(key, value): return f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'
    filename = name or os.path.basename(path)
    payload = (part('kind', kind) +
               f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
               'Content-Type: application/octet-stream\r\n\r\n').encode()
    payload += Path(path).read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
    return call(f'/api/projects/{pid}/upload', 'POST', raw=payload,
                ctype='multipart/form-data; boundary=' + boundary)


def clock(seconds, digits=2, ms_digits=3):
    total = int(round(seconds * 1000))
    hours, rest = divmod(total, 3600000)
    minutes, rest = divmod(rest, 60000)
    secs, millis = divmod(rest, 1000)
    return '%0*d:%02d:%02d,%0*d' % (digits, hours, minutes, secs, ms_digits, millis)


def write_srt(path, runtime, rng, digits=2, ms_digits=3):
    blocks, cursor, number = [], 0.0, 1
    while cursor < runtime - 0.05:
        length = min(rng.choice([3.4, 4.2, 5.0, 6.1, 7.3, 8.0, 9.0]), runtime - cursor)
        if runtime - (cursor + length) < 3.0:
            length = runtime - cursor
        end = round(cursor + length, 3)
        blocks.append((number, round(cursor, 3), end))
        number += 1
        cursor = end
    cues = []
    for number, start, end in blocks:
        cues.append('%d\n%s --> %s\nNarration line %d.' % (number, clock(start, digits, ms_digits), clock(end, digits, ms_digits), number))
    Path(path).write_text('\n\n'.join(cues) + '\n')
    return blocks


def write_narration(path, runtime, rate=22050):
    with wave.open(str(path), 'wb') as handle:
        handle.setnchannels(1); handle.setsampwidth(2); handle.setframerate(rate)
        frames, chunk = int(runtime * rate), []
        for index in range(frames):
            moment = index / rate
            envelope = 0.5 + 0.5 * math.sin(2 * math.pi * 0.31 * moment)
            chunk.append(struct.pack('<h', int(6000 * envelope * math.sin(2 * math.pi * 180 * moment))))
            if len(chunk) >= rate:
                handle.writeframes(b''.join(chunk)); chunk = []
        if chunk:
            handle.writeframes(b''.join(chunk))


def make_clips(directory, count, length, width=1280, height=720):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    existing = sorted(directory.glob('src*.mp4'))
    if len(existing) >= count:
        return existing[:count]
    for index in range(1, count + 1):
        target = directory / f'src{index}.mp4'
        if target.exists():
            continue
        colour = '0x%02x%02x%02x' % (20 + index * 6, 70 + index * 5, 130 + index * 4)
        subprocess.run([ffmpeg(), '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', f'color=c={colour}:size={width}x{height}:rate={FPS}:duration={length}',
                        '-vf', ("drawbox=x='mod(t*40\\,900)':y='mod(t*25+40\\,500)':w=220:h=180:color=white@0.85:t=fill,"
                                f"drawtext=text='SOURCE {index}':x=40:y=40:fontsize=64:fontcolor=white@0.9"),
                        '-c:v', 'libx264', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-g', '90', str(target)],
                       check=True)
    return sorted(directory.glob('src*.mp4'))[:count]


def write_map(path, blocks, sources, clip_length):
    """One beat per block, each in its own non-overlapping interval, never past the clip."""
    headroom = clip_length * 0.95
    cursor = {source.name: 0.0 for source in sources}
    rows = []
    for number, start, end in blocks:
        length = end - start
        chosen = next((s for s in sorted(sources, key=lambda s: cursor[s.name]) if cursor[s.name] + length <= headroom), None)
        if chosen is None:
            raise SystemExit(f'runtime needs more footage than {len(sources)} x {clip_length}s holds '
                             f'(failed at block {number}, a {length:.1f}s beat); raise --sources or --clip-seconds')
        offset = cursor[chosen.name]
        cursor[chosen.name] = offset + length + 0.05
        rows.append({'block': number, 'start': start, 'end': end, 'beat': len(rows) + 1,
                     'media_file': chosen.name, 'source_in': round(offset, 3), 'source_out': round(offset + length, 3)})
    with open(path, 'w', newline='') as handle:
        writer = csv.writer(handle)
        writer.writerow(['block', 'start', 'end', 'beat', 'media_file', 'source_in', 'source_out'])
        for row in rows:
            def srt_clock(value):
                total = int(round(value * 1000))
                hours, rest = divmod(total, 3600000)
                minutes, rest = divmod(rest, 60000)
                secs, millis = divmod(rest, 1000)
                return '%02d:%02d:%02d.%03d' % (hours, minutes, secs, millis)
            writer.writerow([row['block'], srt_clock(row['start']), srt_clock(row['end']), row['beat'],
                             row['media_file'], srt_clock(row['source_in']), srt_clock(row['source_out'])])
    return rows


def check(name, runtime, work, clip_seconds=180, sources=8, narration=True):
    home = Path(work) / name
    home.mkdir(parents=True, exist_ok=True)
    rng = random.Random(int(runtime))
    clips = make_clips(Path(work) / 'clips', sources, clip_seconds)
    # Exercise both cue dialects: standard for most, one-digit hours for a sub-hour piece.
    digits = 1 if runtime < 3600 and int(runtime) % 2 else 2
    blocks = write_srt(home / 'narration.srt', runtime, rng, digits=digits, ms_digits=3 if digits == 2 else 1)
    if narration:
        write_narration(home / 'voice.wav', runtime)
    write_map(home / 'map.csv', blocks, clips, clip_seconds)

    status, reply = call('/api/projects', 'POST', {'title': f'sweep {name}', 'picture_policy': 'clips_only'})
    project = reply.get('id') or (reply.get('project') or {}).get('id')
    if not project:
        raise SystemExit(f'{name}: could not create a project: {reply}')

    jobs = [(home / 'narration.srt', 'srt')]
    if narration:
        jobs.insert(0, (home / 'voice.wav', 'voiceover'))
    jobs += [(clip, 'asset') for clip in clips] + [(home / 'map.csv', 'srt_block_map')]
    for path, kind in jobs:
        status, reply = upload(project, path, kind)
        if status != 200:
            raise SystemExit(f'{name}: upload of {os.path.basename(str(path))} failed ({status}): {reply.get("error")}')

    started = time.time()
    status, reply = call(f'/api/projects/{project}/storyboard', 'POST', {'replace': True})
    build_seconds = time.time() - started
    if status != 200:
        raise SystemExit(f'{name}: build failed ({status}): {reply.get("error")}')
    scenes = reply['scenes']
    errors = list(reply['validation']['errors'])
    duration = scenes[-1]['frame_out'] / FPS

    problems = []
    if errors:
        problems.append(f'{len(errors)} validation error(s): {errors[:2]}')
        # A sweep with too few sources fails as a repetition complaint, which reads like an
        # engine bug. It is not: the cooldown is meant to stop a shot coming back within six
        # slots, so a runtime needing more than six distinct shots needs more than six files.
        # Say so, with the number that would fix it, instead of leaving it to be worked out.
        if any('cooldown' in str(e) for e in errors):
            needed = len(scenes)
            problems.append(f'this fixture has too few sources for {needed} beats: pass '
                            f'--sources {needed} or more (the cooldown is six slots)')
    if abs(duration - runtime) > 0.05:
        problems.append(f'duration {duration:.2f}s is not {runtime:.2f}s')
    if scenes[0]['frame_in'] != 0:
        problems.append(f'timeline does not start at frame 0 ({scenes[0]["frame_in"]})')
    gaps = [i + 1 for i in range(1, len(scenes)) if scenes[i]['frame_in'] != scenes[i - 1]['frame_out']]
    if gaps:
        problems.append(f'timeline is not contiguous at scene(s) {gaps[:5]}')
    mismatched = [i + 1 for i, scene in enumerate(scenes)
                  if scene.get('plan_row') and scene['plan_row'].get('srt_blocks')
                  and scene['srt_blocks'][0] != scene['plan_row']['srt_blocks'][0]]
    if mismatched:
        problems.append(f'scene(s) {mismatched[:5]} carry an SRT block their plan row did not name')

    # Rebuild one block: everything else must survive untouched, and the length must hold.
    # Keyed by the plan row, not the scene id: a rebuilt scene is a new record by design,
    # so comparing ids would report every beat as changed.
    def fingerprint(scene):
        return (scene.get('plan_row_id'), tuple(scene['srt_blocks']), scene['frame_in'], scene['frame_out'],
                round(float(scene['in_s']), 3), scene['asset_id'])
    before = {scene.get('plan_row_id'): fingerprint(scene) for scene in scenes}
    middle = blocks[len(blocks) // 2][0]
    started = time.time()
    status, rebuilt = call(f'/api/projects/{project}/storyboard', 'POST', {'replace': True, 'blocks': [middle]})
    rebuild_seconds = time.time() - started
    if status != 200:
        problems.append(f'block-range rebuild failed ({status}): {rebuilt.get("error")}')
    else:
        after = {scene.get('plan_row_id'): fingerprint(scene) for scene in rebuilt['scenes']}
        changed = [key for key in before if before[key] != after.get(key)]
        if len(changed) > 1:
            problems.append(f'a one-block rebuild changed {len(changed)} beats, not 1: {changed[:5]}')
        if rebuilt['scenes'][-1]['frame_out'] != scenes[-1]['frame_out']:
            problems.append('a one-block rebuild changed the total duration')

    verdict = 'PASS' if not problems else 'FAIL'
    print(f'{verdict}  {name:8s} {runtime:6.0f}s  blocks={len(blocks):3d} beats={len(scenes):3d} '
          f'build={build_seconds:6.1f}s rebuild={rebuild_seconds:5.2f}s duration={duration:8.2f}s '
          f'cue_digits={digits}')
    for problem in problems:
        print('       -', problem)
    return project, not problems


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--runtimes', default='60,300,1080,3600',
                        help='comma-separated runtimes in seconds, e.g. 60,600,1800,3600')
    parser.add_argument('--work', default='/tmp/sweep', help='where generated fixtures and clips live')
    parser.add_argument('--clip-seconds', type=float, default=180)
    parser.add_argument('--sources', type=int, default=8)
    parser.add_argument('--no-narration', action='store_true', help='also prove the text-only path')
    args = parser.parse_args()
    failures = 0
    for value in [float(x) for x in args.runtimes.split(',') if x.strip()]:
        minutes = value / 60
        name = ('%02dmin' % int(minutes)) if minutes == int(minutes) else ('%03ds' % int(value))
        _, ok = check(name, value, args.work, args.clip_seconds, args.sources)
        failures += 0 if ok else 1
        if args.no_narration:
            _, ok = check(name + '_text', value, args.work, args.clip_seconds, args.sources, narration=False)
            failures += 0 if ok else 1
    print('\n%d failure(s)' % failures)
    sys.exit(1 if failures else 0)
