"""Optional local-only media understanding. Results are observations, not approvals.

Only reads project media and local model files; the sole HTTP endpoint is Ollama on
loopback. Jobs are serialized to bound CPU/RAM use and never modify the storyboard.
"""
import base64
import csv
import gc
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path

import imageio_ffmpeg

_WORKER = threading.Lock()
_GUARD = threading.Lock()
_RUNNING = set()


def capabilities():
    model = os.environ.get('STORYROOM_WHISPER_MODEL_DIR', '').strip()
    local_model = bool(model and Path(model).is_dir() and (Path(model) / 'model.bin').is_file())
    return {'ffmpeg': bool(imageio_ffmpeg.get_ffmpeg_exe()),
            'faster_whisper': bool(importlib.util.find_spec('faster_whisper')),
            'whisper_model_ready': local_model,
            'scenedetect': bool(importlib.util.find_spec('scenedetect')),
            'librosa': bool(importlib.util.find_spec('librosa')),
            'sound_classifier': bool(importlib.util.find_spec('onnxruntime') and os.environ.get('STORYROOM_YAMNET_MODEL') and Path(os.environ['STORYROOM_YAMNET_MODEL']).is_file()),
            'ollama_url': 'http://127.0.0.1:11434',
            'vision_model': os.environ.get('STORYROOM_OLLAMA_MODEL', 'qwen3-vl:2b'),
            'speaker_diarization': bool(importlib.util.find_spec('pyannote') and os.environ.get('STORYROOM_PYANNOTE_MODEL_DIR') and Path(os.environ['STORYROOM_PYANNOTE_MODEL_DIR']).is_dir()),
            'note': 'CPU int8 transcription requires a local Faster-Whisper model directory; visual analysis requires a locally running Ollama with the selected vision model. No models are downloaded by Storyroom.'}


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _signature(path):
    st = path.stat()
    return {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}


def result_path(root, target):
    return Path(root) / 'reviews' / 'understanding' / (target + '.json')


def read_result(root, target, path):
    p = result_path(root, target)
    if not p.exists(): return {'status': 'not_started'}
    try:
        result = json.loads(p.read_text(encoding='utf-8'))
        if result.get('source_signature') != _signature(path):
            return {'status': 'stale', 'detail': 'Source changed; run the analysis again.'}
        with _GUARD: active = (str(root), target) in _RUNNING
        if result.get('status') in ('queued', 'running') and not active:
            result['status'] = 'interrupted'
        return result
    except (OSError, ValueError):
        return {'status': 'error', 'detail': 'Analysis report could not be read.'}


def enqueue(root, target, path, kind, duration):
    root, path = Path(root), Path(path)
    key = (str(root), target)
    with _GUARD:
        if key in _RUNNING: return False
        _RUNNING.add(key)
    try:
        data = {'status': 'queued', 'source_signature': _signature(path), 'source': path.name,
                'kind': kind, 'duration_s': duration, 'stages': {}, 'started_at': time.time()}
        _write(result_path(root, target), data)
        threading.Thread(target=_run, args=(root, target, path, data), daemon=True).start()
        return True
    except Exception:
        with _GUARD: _RUNNING.discard(key)
        raise


def _stage(data, report, name, fn):
    data['stages'][name] = {'status': 'running'}; _write(report, data)
    try:
        data['stages'][name] = {'status': 'complete', 'result': fn()}
    except Exception as e:
        data['stages'][name] = {'status': 'unavailable', 'detail': str(e)[:600]}
    _write(report, data)


def _decode_audio(source, wav):
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y', '-i', str(source),
           '-map', '0:a:0', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(wav)]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=7200)
    if proc.returncode: raise RuntimeError('No decodable audio stream: ' + proc.stderr.decode(errors='replace')[-300:])


def _transcribe(wav):
    model_path = os.environ.get('STORYROOM_WHISPER_MODEL_DIR', '').strip()
    if not model_path or not (Path(model_path) / 'model.bin').is_file():
        raise RuntimeError('Set STORYROOM_WHISPER_MODEL_DIR to a downloaded Faster-Whisper CTranslate2 model directory.')
    from faster_whisper import WhisperModel
    model = WhisperModel(model_path, device='cpu', compute_type='int8', cpu_threads=max(1, min(2, os.cpu_count() or 1)), local_files_only=True)
    try:
        segments, info = model.transcribe(str(wav), beam_size=3, vad_filter=True, word_timestamps=True)
        items = [{'start_s': round(s.start, 3), 'end_s': round(s.end, 3), 'text': s.text.strip(),
                  'words': [{'start_s': round(w.start, 3) if w.start is not None else None, 'end_s': round(w.end, 3) if w.end is not None else None, 'text': w.word} for w in (s.words or [])]} for s in segments]
        return {'language': info.language, 'language_probability': info.language_probability, 'segments': items}
    finally:
        del model; gc.collect()


def _diarize(wav):
    """Load an optional local-only community-1 pipeline in an isolated subprocess."""
    model = os.environ.get('STORYROOM_PYANNOTE_MODEL_DIR', '').strip()
    if not model or not Path(model).is_dir():
        raise RuntimeError('Set STORYROOM_PYANNOTE_MODEL_DIR to a downloaded local pyannote community-1 pipeline.')
    env = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
               PYANNOTE_METRICS_ENABLED='0', HF_HUB_DISABLE_TELEMETRY='1')
    with tempfile.TemporaryDirectory(prefix='storyroom-diarization-') as tmp:
        result = Path(tmp) / 'speakers.json'
        proc = subprocess.run([sys.executable, str(Path(__file__).with_name('local_diarization.py')),
                               str(Path(model).resolve()), str(wav), str(result)],
                              env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=7200)
        if proc.returncode:
            raise RuntimeError('Local pyannote failed: ' + proc.stderr.decode(errors='replace')[-500:])
        return json.loads(result.read_text(encoding='utf-8'))


def _align_speakers(transcript, speakers):
    """Annotate words/segments by strongest overlap; labels are anonymous per file."""
    turns = speakers.get('turns', [])
    def label(item):
        start, end = item.get('start_s'), item.get('end_s')
        if start is None or end is None: return None
        overlap = [(max(0, min(end, t['end_s']) - max(start, t['start_s'])), t['speaker']) for t in turns]
        best = max(overlap, default=(0, None))
        return best[1] if best[0] > 0 else None
    for segment in transcript.get('segments', []):
        segment['speaker'] = label(segment)
        for word in segment.get('words', []): word['speaker'] = label(word)


def _audio_features(wav):
    import numpy as np
    import librosa
    chunks = []; silence = []; quiet_start = None; quiet_end = None
    with wave.open(str(wav), 'rb') as f:
        sr = f.getframerate(); samples = sr * 10; position = 0
        while True:
            raw = f.readframes(samples)
            if not raw: break
            y = np.frombuffer(raw, dtype='<i2').astype(np.float32) / 32768
            if not len(y): break
            rms = float(np.sqrt(np.mean(y * y)))
            # Signal statistics only: they are not a classifier for music or sound events.
            centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr))) if len(y) >= 2048 else 0.0
            chunks.append({'start_s': round(position / sr, 3), 'end_s': round((position + len(y)) / sr, 3),
                           'rms': round(rms, 5), 'spectral_centroid_hz': round(centroid, 1), 'low_energy': rms < 0.01})
            # Absolute threshold, 100 ms windows; merge across chunk boundaries.
            frame = sr // 10
            for offset in range(0, len(y), frame):
                fragment = y[offset:offset + frame]
                low = float(np.sqrt(np.mean(fragment * fragment))) < 0.01
                start = (position + offset) / sr
                end = (position + offset + len(fragment)) / sr
                if low:
                    if quiet_start is None: quiet_start = start
                    quiet_end = end
                elif quiet_start is not None:
                    if quiet_end - quiet_start >= 0.5: silence.append({'start_s': round(quiet_start, 3), 'end_s': round(quiet_end, 3)})
                    quiet_start = quiet_end = None
            position += len(y)
    if quiet_start is not None and quiet_end - quiet_start >= 0.5:
        silence.append({'start_s': round(quiet_start, 3), 'end_s': round(quiet_end, 3)})
    return {'windows': chunks, 'low_energy_intervals': silence,
            'note': 'Approximate low-energy intervals use an absolute RMS threshold; they are not guaranteed silence or a music/sound-event classification.'}


def _sound_events(wav):
    """CPU YAMNet waveform ONNX: independent 3-second source-time windows."""
    import numpy as np
    import onnxruntime as ort
    model = os.environ.get('STORYROOM_YAMNET_MODEL', '').strip()
    if not model or not Path(model).is_file():
        raise RuntimeError('Set STORYROOM_YAMNET_MODEL to a compatible local YAMNet waveform ONNX model.')
    with (Path(__file__).parent / 'data' / 'yamnet_class_map.csv').open(encoding='utf-8', newline='') as f:
        labels = [row['display_name'] for row in csv.DictReader(f)]
    opts = ort.SessionOptions(); opts.intra_op_num_threads = 2; opts.inter_op_num_threads = 1
    session = ort.InferenceSession(model, sess_options=opts, providers=['CPUExecutionProvider'])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    if len(inputs) != 1 or inputs[0].shape != [1, 48000] or inputs[0].type != 'tensor(float)' or not any(o.name == 'scores' for o in outputs):
        raise RuntimeError('Incompatible YAMNet model: expected float32 waveform [1, 48000] and a scores output.')
    windows = []
    with wave.open(str(wav), 'rb') as f:
        if f.getframerate()!=16000 or f.getnchannels()!=1 or f.getsampwidth()!=2:
            raise ValueError('Expected mono 16 kHz PCM audio.')
        position = 0
        while True:
            raw=f.readframes(48000)
            if not raw: break
            y=np.frombuffer(raw, dtype='<i2').astype(np.float32)/32768.0
            length=len(y)
            top=[]
            # A very short zero-padded tail can be confidently misclassified as silence.
            if length >= 16000:
                y=np.pad(y, (0, 48000-length))[None, :]
                scores=np.asarray(session.run(['scores'], {inputs[0].name: y})[0])
                if scores.ndim != 2 or scores.shape[1] != len(labels) or not np.isfinite(scores).all():
                    raise RuntimeError('YAMNet returned incompatible or invalid class scores.')
                # Score frames cover approximately 0.96 s, starting every 0.48 s.
                # Exclude frames extending into padding rather than mislabelling it.
                valid=max(1, min(scores.shape[0], 1 + int((length/16000-0.96)/0.48)))
                mean=scores[:valid].mean(axis=0)
                top=[{'label': labels[int(i)], 'score': round(float(mean[i]), 4)}
                     for i in np.argsort(mean)[::-1][:5] if mean[i] >= 0.1]
            windows.append({'start_s': round(position/16000,3), 'end_s': round((position+length)/16000,3), 'predictions': top, 'too_short': length < 16000})
            position += length
    return {'windows': windows, 'note': 'YAMNet estimates for independent 3-second windows, not verified sound events. Low-scoring classes are omitted; speech and music can overlap. Tails under one second are not classified.'}


def _scenes(path, duration):
    from scenedetect import detect, ContentDetector
    spans = detect(str(path), ContentDetector(), show_progress=False)
    if not spans: return [{'start_s': 0.0, 'end_s': round(duration, 3)}]
    return [{'start_s': round(a.get_seconds(), 3), 'end_s': round(b.get_seconds(), 3)} for a, b in spans]


def _sample_frames(scenes, max_frames, interval_s=10):
    """Spread the budget over the entire source, including within long scenes."""
    candidates = []
    for scene in scenes:
        length = max(0, scene['end_s'] - scene['start_s'])
        count = max(1, int((length + interval_s - 1e-9) // interval_s))
        # Bound planning memory even for multi-hour files.
        count = min(count, max_frames * 10)
        for i in range(count):
            candidates.append((scene['start_s'] + length * (i + .5) / count, scene))
    if len(candidates) > max_frames:
        candidates = [candidates[int((i + .5) * len(candidates) / max_frames)] for i in range(max_frames)]
    return candidates


def _vision(path, scenes):
    # No arbitrary URL or redirects: image data stays on this computer.
    model = os.environ.get('STORYROOM_OLLAMA_MODEL', 'qwen3-vl:2b')
    max_frames = max(1, min(300, int(os.environ.get('STORYROOM_VISION_MAX_FRAMES', '60'))))
    sampled = _sample_frames(scenes, max_frames)
    observations = []
    with tempfile.TemporaryDirectory(prefix='storyroom-frames-') as tmp:
        for at, scene in sampled:
            image = Path(tmp) / 'frame.jpg'
            proc = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y', '-ss', str(at), '-i', str(path), '-frames:v', '1', '-vf', 'scale=640:-2', str(image)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
            if proc.returncode or not image.is_file(): raise RuntimeError('Could not extract frame at %.3fs' % at)
            payload = {'model': model, 'stream': False, 'keep_alive': '0', 'options': {'num_ctx': 2048, 'num_thread': 2}, 'messages': [{'role': 'user',
                'content': 'Describe only what is visibly supported: activity, objects, people, setting, and readable text. Say when uncertain; do not infer real identities.',
                'images': [base64.b64encode(image.read_bytes()).decode('ascii')]}]}
            req = urllib.request.Request('http://127.0.0.1:11434/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            # Disable environment proxies and redirects so media cannot be sent off-host.
            class NoRedirect(urllib.request.HTTPRedirectHandler):
                def redirect_request(self, *args, **kwargs): return None
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
            try:
                with opener.open(req, timeout=300) as response: result = json.load(response)
            except (OSError, urllib.error.URLError) as e:
                raise RuntimeError('Local Ollama unavailable or model missing: ' + str(e)) from e
            text = (result.get('message') or {}).get('content', '').strip()
            if not text: raise RuntimeError('Ollama returned no visual description.')
            observations.append({'frame_time_s': round(at, 3), 'scene_start_s': scene['start_s'], 'scene_end_s': scene['end_s'], 'description': text})
    return {'observations': observations, 'sampled_frames': len(sampled), 'total_scenes': len(scenes), 'note': 'Sampled still frames do not describe every moment or establish identities.'}


def _run(root, target, source, data):
    key = (str(root), target); report = result_path(root, target)
    try:
        with _WORKER:
            data['status'] = 'running'; _write(report, data)
            with tempfile.TemporaryDirectory(prefix='storyroom-audio-') as tmp:
                wav = Path(tmp) / 'audio.wav'
                try:
                    _decode_audio(source, wav)
                except Exception as e:
                    for stage in ('transcript', 'audio_features'):
                        data['stages'][stage] = {'status': 'unavailable', 'detail': str(e)[:600]}
                    _write(report, data)
                else:
                    _stage(data, report, 'transcript', lambda: _transcribe(wav))
                    _stage(data, report, 'audio_features', lambda: _audio_features(wav))
                    if os.environ.get('STORYROOM_YAMNET_MODEL'):
                        _stage(data, report, 'sound_events', lambda: _sound_events(wav))
                    else:
                        data['stages']['sound_events'] = {'status': 'not_requested', 'detail': 'Optional local sound-event model is not configured.'}
                    if os.environ.get('STORYROOM_PYANNOTE_MODEL_DIR'):
                        _stage(data, report, 'speakers', lambda: _diarize(wav))
                        if data['stages']['speakers']['status'] == 'complete' and data['stages']['transcript']['status'] == 'complete':
                            _align_speakers(data['stages']['transcript']['result'], data['stages']['speakers']['result'])
                    else:
                        data['stages']['speakers'] = {'status': 'not_requested', 'detail': 'Optional local pyannote speaker diarization is not configured.'}
            if data['kind'] == 'video':
                _stage(data, report, 'scene_changes', lambda: _scenes(source, data['duration_s']))
                cuts = data['stages']['scene_changes']
                if cuts['status'] == 'complete':
                    _stage(data, report, 'visual', lambda: _vision(source, cuts['result']))
                else:
                    data['stages']['visual'] = {'status':'unavailable','detail':'Scene detection failed; no visual frames analysed.'}
            if 'sound_events' not in data['stages']:
                data['stages']['sound_events'] = {'status': 'unavailable', 'detail': 'No decoded audio for sound classification.'}
            if 'speakers' not in data['stages']:
                data['stages']['speakers'] = {'status': 'unavailable', 'detail': 'No decoded audio for diarization.'}
            data['status'] = 'complete' if all(x['status'] in ('complete', 'not_requested') for x in data['stages'].values()) else 'partial'
            data['finished_at'] = time.time()
            if _signature(source) != data['source_signature']:
                data['status'] = 'stale'; data['detail'] = 'Source changed during analysis; run again.'
            _write(report, data)
    except Exception as e:
        data['status'] = 'error'; data['detail'] = str(e)[:600]
        try: _write(report, data)
        except OSError: pass
    finally:
        with _GUARD: _RUNNING.discard(key)


def scene_context(report, scene):
    """Project source-time observations onto a saved scene without asserting verification."""
    source_in = float(scene.get('in_s') or 0)
    timeline_in = scene['frame_in'] / 30
    duration = (scene['frame_out'] - scene['frame_in']) / 30
    source_out = source_in + duration
    result = {'status': report.get('status'), 'source_in_s': round(source_in, 3),
              'source_out_s': round(source_out, 3), 'timeline_in_s': round(timeline_in, 3),
              'transcript': [], 'sound_events': [], 'visual': [],
              'note': 'Source observations are estimates; timeline times are mapped from the saved in-point and not editorial verification.'}
    if report.get('status') not in ('complete', 'partial'): return result
    stages = report.get('stages') or {}
    def interval(rows):
        found = []
        for item in rows:
            a, b = float(item['start_s']), float(item['end_s'])
            if a >= source_out or b <= source_in: continue
            row = dict(item)
            row['timeline_start_s'] = round(timeline_in + max(a, source_in) - source_in, 3)
            row['timeline_end_s'] = round(timeline_in + min(b, source_out) - source_in, 3)
            found.append(row)
        return found
    def data(name):
        stage = stages.get(name) or {}
        return stage.get('result', {}) if stage.get('status') == 'complete' else {}
    result['transcript'] = interval(data('transcript').get('segments', []))
    result['sound_events'] = interval(data('sound_events').get('windows', []))
    for item in data('visual').get('observations', []):
        at = float(item['frame_time_s'])
        if source_in <= at < source_out:
            result['visual'].append(dict(item, timeline_time_s=round(timeline_in + at - source_in, 3)))
    return result
