"""Frame-indexed documentary MP4 assembly primitive.

The caller must enforce final editorial, rights and project-version gates.
This module never approves footage.
"""
from fractions import Fraction
from pathlib import Path
import copy
import subprocess
import tempfile

import av
import imageio_ffmpeg
from documentary import annotate_plan, build_timeline
from documentary_audio import build_audio
# `ultrafast` is not just faster, it is far larger: on this project's footage it
# wrote 4.3x the bytes of `veryfast` at the same CRF, because it drops the
# compression tools and falls back to the Constrained Baseline profile. The
# export encodes twice (clips, then captions), so every byte was paid twice.
VIDEO_PRESET = 'veryfast'

def sweep_work_dirs(renders_dir, keep=None):
    """Remove work directories left by a render that was killed, not raised.

    `TemporaryDirectory` cleans up when the job finishes or raises, but a killed
    process leaves its `documentary-final-*` directory behind with hundreds of
    megabytes of intermediate audio. Rendering again is the safe moment to clear
    them, because no other render is running against this project.
    """
    import shutil
    removed = 0
    for old in Path(renders_dir).glob('documentary-final-*'):
        if not old.is_dir() or (keep and old.name == keep):
            continue
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    return removed


from documentary_render_plan import plan as picture_plan, load_decisions
from strict_cuts import inspect_video


def _run(args):
    run = subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                         text=True, timeout=1800)
    if run.returncode:
        raise ValueError('Documentary assembly failed: ' + run.stderr[-800:])


def _video_info(path):
    with av.open(str(path)) as media:
        if not media.streams.video:
            raise ValueError('Assembled documentary has no video stream.')
        stream = media.streams.video[0]
        # base_rate is the container timebase and is exactly 30000/1001 for an NTSC
        # frame rate. average_rate is frames/duration, which only equals the
        # nominal rate when the duration divides exactly -- a clip trimmed to its
        # own frame count is short by one frame, so average_rate drifts to
        # 29.9849 and every comparison against 30000/1001 failed on a clip that
        # was in fact correct. Fall back to average_rate only if no timebase.
        rate = stream.base_rate or stream.average_rate
        return stream.frames, Fraction(rate) if rate else None


def render(root, project, sound_plan, destination, width, height, progress=lambda *_: None, enhance_picture=False, subtitle_ass=None, cancel_check=lambda: None, music=False):
    """Render compatible, verified picture ranges and script captions, non-destructively.

    This is only an assembly primitive, NOT authorization to export. Caller must
    apply editorial/rights checks and check for changes before publishing output.
    Original source files and original SRT are never changed.
    """
    root = Path(root).resolve(); destination = Path(destination).resolve()
    if width < 64 or height < 64 or width % 2 or height % 2 or width > 7680 or height > 7680:
        raise ValueError('Output size must have even dimensions from 64 to 7680 pixels.')
    if destination.exists():
        raise ValueError('The destination already exists; existing files are never overwritten.')
    if not destination.is_relative_to(root / 'renders') or destination.suffix.lower() != '.mp4':
        raise ValueError('Destination must be a new MP4 in this project’s renders folder.')
    annotated = annotate_plan(project, root, copy.deepcopy(sound_plan))
    picture = picture_plan(project, root, annotated, load_decisions(root))
    if not picture['picture_compatible']:
        raise ValueError('Picture frame check failed: ' + '; '.join(picture['errors']))
    assets = {a['id']: a for a in project.get('assets', [])}
    fps = Fraction(picture['output_fps']); rate = f'{fps.numerator}/{fps.denominator}'
    total_frames = sum(s['source_frame_count'] for s in picture['spans'])
    if not total_frames:
        raise ValueError('The documentary has no picture frames.')
    video_duration = Fraction(total_frames, 1) / fps
    ff = imageio_ffmpeg.get_ffmpeg_exe(); destination.parent.mkdir(parents=True, exist_ok=True)
    sweep_work_dirs(destination.parent)
    with tempfile.TemporaryDirectory(prefix='documentary-final-', dir=destination.parent) as temp:
        work = Path(temp); audio = work / 'audio.wav'
        levels = build_audio(root, project, annotated, audio, progress=lambda *_: cancel_check(), strict_handoffs=True, music=music)
        cancel_check()
        audio_duration = Fraction(round(levels['duration_s'] * 48000), 48000)
        difference = video_duration - audio_duration
        # A last output frame naturally rounds the end of the original narration.
        # Any larger silent picture tail requires an explicit saved authorization.
        allowed_tail = Fraction(1, 2) / fps
        # The decoded last narration segment may end before its container's
        # reported duration. No spoken samples are invented; this is silence
        # already implied by the declared end. Non-final handoffs were checked
        # to within two samples by strict_handoffs above.
        last = build_timeline(project, annotated)['segments'][-1]
        expected_last = Fraction(round((last['source_out_s']-last['source_in_s'])*48000),48000)
        decoded_last = Fraction(round(levels['segment_lengths_s'][-1]*48000),48000)
        missing_last = max(Fraction(0),expected_last-decoded_last)
        if missing_last > Fraction(25,1000):raise ValueError('Decoded narration ends too far before the declared duration.')
        allowed_tail += missing_last
        record = project.get('documentary_beat_review') or {}
        if record.get('silent_tail_authorized'):
            voice_ms = round(float(project['voiceover']['duration']) * 1000)
            for g in record.get('approved_gaps', []):
                if g['narration_out_ms'] == voice_ms:
                    allowed_tail += Fraction(max(0, (g['source_out_ms']-g['source_in_ms'])-(g['narration_out_ms']-g['narration_in_ms'])), 1000)
        if difference < -Fraction(1, 48000) or difference > allowed_tail:
            raise ValueError(f'Picture/audio end differs by {float(difference):.6f}s; no unapproved trim or silence was applied.')
        clips = []
        for i, span in enumerate(picture['spans']):
            cancel_check()
            progress(i, len(picture['spans']))
            asset = assets.get(span['asset_id']) or {}; source = (root / asset.get('path', '')).resolve()
            if asset.get('kind') != 'video' or not source.is_relative_to(root / 'sources') or not source.is_file():
                raise ValueError('An assigned source video is missing.')
            scale = span['picture_scale']; clip = work / f'{i:04}.mp4'
            # A source shot at twice the output rate (59.94 fps into a 29.97 fps
            # film) must be decimated, not slowed. The render plan already halves
            # the frame count and records `frame_conversion`; without the matching
            # `select` below, `setpts` replayed every source frame at the output
            # rate, so a 30 s bite showed its first 15 s stretched over 30 s.
            halve = span.get('frame_conversion') == 'halve'
            select = 'select=not(mod(n' + chr(92) + ',2)),' if halve else ''
            # Fit the source into the frame. A narrow output (a 9:16 reel from a
            # 16:9 source) cannot be fitted without leaving the picture a thin
            # strip between black bars, so it is filled and the overflow cropped
            # away from the centre instead. A wide output keeps the whole frame.
            fill = width / height < 16 / 9
            fit = ('increase,crop=' + str(width) + ':' + str(height)) if fill else (
                'decrease,pad=' + str(width) + ':' + str(height) + ':(ow-iw)/2:(oh-ih)/2:black')
            filters = (f"trim=start_frame={span['source_start_frame']}:end_frame={span['source_end_frame']},"
                       f"{select}setpts=N/({rate}*TB),scale={width}:{height}:force_original_aspect_ratio={fit},"
                       f"setsar=1")
            if scale != 100:
                sw = round(width * scale / 200) * 2; sh = round(height * scale / 200) * 2
                # The zoom is applied as a scale followed by a crop back to the frame
                # size. That scale carries the source's pixel aspect ratio through and
                # leaves a non-square one behind -- a 1080x1920 film came out with
                # SAR 4079:4077, so its real display shape was not exactly 9:16. Reset
                # the ratio after the crop, or the delivered file plays slightly
                # stretched and some players report the wrong aspect.
                filters += f',scale={sw}:{sh},crop={width}:{height},setsar=1'
            if enhance_picture:
                filters += ',hqdn3d=1:1:0:0,unsharp=5:5:0.35:5:5:0'
            filters += ',format=yuv420p'
            # `-video_track_timescale` pins the container time base. Without it a clip
            # cut from a 59.94 fps source is written at 1/60000 while the rest sit at
            # 1/30000, and the `-c copy` concat below cannot stitch mixed time bases:
            # it silently leaves hundreds of seconds of black where those clips belong.
            _run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(source), '-an',
                  '-vf', filters, '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', VIDEO_PRESET, '-bf', '0',
                  '-crf', '20', '-threads', '2', '-frames:v', str(span['source_frame_count']),
                  '-video_track_timescale', str(fps.numerator), str(clip)])
            count, actual_rate = _video_info(clip)
            if count != span['source_frame_count'] or actual_rate != fps:
                raise ValueError(f"{span['labels'][0]}: encoded picture frame count or frame rate changed.")
            clips.append(clip)
        listing = work / 'clips.txt'
        listing.write_text(''.join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in clips), encoding='utf-8')
        joined = work / 'joined.mp4'
        # Timestamps are rewritten from the frame index instead of trusted. Clips
        # cut from a 59.94 fps source do not land on exactly `source_frame_count`
        # output frames, so the join inherits a sub-frame deficit at every such cut
        # and every timestamp after it slides. Concatenating pulls frames out of
        # order for the same reason. Rebuilding each PTS as N * numerator over a
        # 30000 timescale is exact for the whole timeline, and `-c copy` means not
        # one pixel is encoded again.
        # A 1/numerator timescale makes one frame exactly `denominator` ticks long,
        # so N frames is N * denominator with no rounding anywhere.
        timescale = fps.numerator
        step = fps.denominator
        _run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', str(listing),
              '-c', 'copy', '-bsf:v', f'setts=pts=N*{step}:dts=N*{step}',
              '-video_track_timescale', str(timescale), str(joined)])
        count, actual_rate = _video_info(joined)
        if count != total_frames or actual_rate != fps:
            raise ValueError('Concatenated picture has unexpected frame count or frame rate.')
        caption_filters = []
        # `burn_captions` is False only when the project explicitly turned the
        # script's on-screen captions off. The timed cues are still computed.
        if picture['captions'] and picture.get('burn_captions', True) is not False:
            font = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
            if not font.is_file():
                raise ValueError('The caption font is missing; scripted captions cannot be omitted.')
            for i, caption in enumerate(picture['captions']):
                text = work / f'caption-{i:03}.txt'; text.write_text(caption['text'], encoding='utf-8')
                caption_filters.append(f"drawtext=fontfile={font}:textfile={text}:expansion=none:fontsize={max(16,height//24)}:fontcolor=white:borderw=2:bordercolor=black:x=(w-text_w)/2:y=h*0.08:enable='gte(t,{caption['timeline_in_ms']/1000:.3f})*lt(t,{caption['timeline_out_ms']/1000:.3f})'")
        if subtitle_ass is not None:
            if not isinstance(subtitle_ass, str) or not subtitle_ass.startswith('[Script Info]'):
                raise ValueError('Adjusted subtitle data is invalid; no subtitles were omitted.')
            ass_file=work/'adjusted-subtitles.ass';ass_file.write_text(subtitle_ass,encoding='utf-8')
            caption_filters.append(f'ass=filename={ass_file}')
        cancel_check()
        # The script opens with `Cut Action: FADE IN FROM BLACK`. A fade is a
        # brightness ramp on existing frames, not a dissolve: the frame count, the
        # timing and every cut stay exactly as reviewed. `fade_out` is the closing
        # ramp to black, also frame-neutral.
        fade_in_s = float(picture.get('fade_in_s') or 0)
        fade_out_s = float(picture.get('fade_out_s') or 0)
        if fade_in_s or fade_out_s:
            faded = work / 'faded.mp4'
            parts = []
            if fade_in_s:
                parts.append(f'fade=t=in:st=0:d={fade_in_s:.3f}')
            if fade_out_s:
                start = max(0.0, float(video_duration) - fade_out_s)
                parts.append(f'fade=t=out:st={start:.3f}:d={fade_out_s:.3f}')
            parts.append('format=yuv420p')
            _run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(joined),
                  '-an', '-vf', ','.join(parts), '-fps_mode', 'passthrough',
                  '-c:v', 'libx264', '-preset', VIDEO_PRESET, '-bf', '0', '-crf', '20', '-threads', '2',
                  '-frames:v', str(total_frames), str(faded)])
            count, actual_rate = _video_info(faded)
            if count != total_frames or actual_rate != fps:
                raise ValueError('The fade changed the picture frame count or rate.')
            joined = faded
        result = work / 'result.mp4'
        cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y', '-i', str(joined), '-i', str(audio), '-map', '0:v:0', '-map', '1:a:0']
        if caption_filters:
            cmd += ['-vf', ','.join(caption_filters), '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', VIDEO_PRESET, '-bf', '0', '-crf', '20', '-threads', '2']
        else:
            cmd += ['-c:v', 'copy']
        if difference > 0:
            cmd += ['-af', f'apad=pad_dur={float(difference):.9f}']
        cmd += ['-c:a', 'aac', '-b:a', '192k', '-t', f'{float(video_duration):.9f}', '-movflags', '+faststart', str(result)]
        _run(cmd)
        count, actual_rate = _video_info(result)
        if count != total_frames or actual_rate != fps:
            raise ValueError('Final MP4 frame count or rate changed; no result was published.')
        # Decode every output frame to check timestamps rather than trusting only
        # container metadata. Audio must be present and cover the picture clock.
        inspect_video(result, total_frames)
        with av.open(str(result)) as finished:
            if not finished.streams.audio:
                raise ValueError('Final MP4 has no audio stream; no result was published.')
            stream = finished.streams.audio[0]
            if stream.duration is None or stream.time_base is None:
                raise ValueError('Final MP4 audio duration could not be verified.')
            encoded_audio_duration = Fraction(stream.duration) * stream.time_base
            if abs(encoded_audio_duration-video_duration) > Fraction(1, 25):
                raise ValueError('Final MP4 audio duration does not match the picture.')
        cancel_check()
        result.replace(destination)
    progress(len(picture['spans']), len(picture['spans']))
    return {'frames': total_frames, 'fps': str(fps), 'duration_s': float(video_duration),
            'audio_duration_s': float(audio_duration), 'silent_tail_s': max(0, float(difference)), 'decoded_narration_short_s': float(missing_last),
            'captions': len(picture['captions']) if picture.get('burn_captions', True) is not False else 0,
            'captions_written': len(picture['captions']), 'sound_bite_levels': levels['sound_bite_levels']}
