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


def _whisper_model_ready(model):
    # Faster-Whisper can call Tokenizer.from_pretrained even with local_files_only
    # when tokenizer.json is absent. Refuse before loading to prevent network access.
    if not model: return False
    root = Path(model)
    return root.is_dir() and all((root / name).is_file() for name in ('model.bin', 'config.json', 'tokenizer.json'))


def _whisper_model_path():
    if 'STORYROOM_WHISPER_MODEL_DIR' in os.environ:
        return os.environ['STORYROOM_WHISPER_MODEL_DIR'].strip()
    return str(Path(__file__).resolve().parent / '.cache' / 'storyroom-models' / 'whisper-small')


def _yamnet_model_path():
    if 'STORYROOM_YAMNET_MODEL' in os.environ:
        return os.environ['STORYROOM_YAMNET_MODEL'].strip()
    return str(Path(__file__).resolve().parent / '.cache' / 'storyroom-models' / 'yamnet.onnx')


def capabilities():
    model = _whisper_model_path()
    local_model = _whisper_model_ready(model)
    return {'ffmpeg': bool(imageio_ffmpeg.get_ffmpeg_exe()),
            'faster_whisper': bool(importlib.util.find_spec('faster_whisper')),
            'whisper_model_ready': local_model,
            'scenedetect': bool(importlib.util.find_spec('scenedetect')),
            'librosa': bool(importlib.util.find_spec('librosa')),
            'sound_classifier': bool(importlib.util.find_spec('onnxruntime') and _yamnet_model_path() and Path(_yamnet_model_path()).is_file()),
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
        if result.get('audio_timing_version') != 2:
            result['timing_warning'] = 'Audio stages were generated before the delayed-audio alignment fix. Reanalyse if this source has a delayed audio track.'
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
                'kind': kind, 'duration_s': duration, 'stages': {}, 'started_at': time.time(), 'audio_timing_version': 2}
        _write(result_path(root, target), data)
        # Through the shared queue rather than its own thread. Analysis is heavy and
        # must not compete with a render, which is precisely what it used to do.
        import jobqueue
        jobqueue.QUEUE.submit(jobqueue.Job(
            'understanding-%s-%s' % (root.name, target),
            'Media analysis · %s' % (target or 'source'),
            lambda context: _run_queued(root, target, path, data, context),
            priority=jobqueue.PRIORITY_ANALYSIS, weight='heavy',
            project=root.name, meta={'target': target}))
        return True
    except Exception:
        with _GUARD: _RUNNING.discard(key)
        raise


def _run_queued(root, target, path, data, context):
    """Run the analysis inside the queue, reporting progress and honouring pause.

    `_run` streams stage updates into the report file as it goes, so the queue only has
    to hold the slot, report liveness and let cancellation through.
    """
    def note(stage, detail=None):
        if context is not None and detail:
            context.set(detail=str(detail)[:200])
    try:
        return _run(root, target, path, data, observer=note)
    finally:
        with _GUARD:
            _RUNNING.discard((str(root), target))


def enqueue_sound_events(root, target, path):
    """Reanalyse just sound events, preserving other completed observations."""
    root, path = Path(root), Path(path)
    key = (str(root), target)
    with _GUARD:
        if key in _RUNNING: raise ValueError('An analysis is already running for this source.')
        # Read directly under the guard: read_result also takes _GUARD.
        report = result_path(root, target)
        if not report.is_file(): raise ValueError('Run a full analysis first.')
        try: data = json.loads(report.read_text(encoding='utf-8'))
        except (OSError, ValueError): raise ValueError('Analysis report could not be read.')
        if data.get('status') not in ('complete', 'partial') or data.get('source_signature') != _signature(path):
            raise ValueError('A current completed or partial report is required.')
        model = _yamnet_model_path()
        if not model or not Path(model).is_file(): raise ValueError('No local YAMNet model is configured.')
        _RUNNING.add(key)
    try:
        data['status'] = 'queued'
        data['stages']['sound_events'] = {'status': 'queued'}
        _write(report, data)
        def run():
            try:
                with _WORKER:
                    data['status'] = 'running'; _write(report, data)
                    with tempfile.TemporaryDirectory(prefix='storyroom-sound-') as tmp:
                        wav = Path(tmp) / 'audio.wav'
                        def classify():
                            _decode_audio(path, wav)
                            return _sound_events(wav)
                        _stage(data, report, 'sound_events', classify)
                    data['status'] = 'complete' if all(x['status'] in ('complete', 'not_requested') for x in data['stages'].values()) else 'partial'
                    data['finished_at'] = time.time()
                    if _signature(path) != data['source_signature']:
                        data['status'] = 'stale'; data['detail'] = 'Source changed during analysis; run again.'
                    _write(report, data)
            except Exception as e:
                data['status'] = 'partial'; data['stages']['sound_events'] = {'status': 'unavailable', 'detail': str(e)[:600]}
                try: _write(report, data)
                except OSError: pass
            finally:
                with _GUARD: _RUNNING.discard(key)
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        with _GUARD: _RUNNING.discard(key)
        raise


def enqueue_visual(root, target, path):
    """Reanalyse visual frames without retranscribing or replacing other stages."""
    root, path = Path(root), Path(path)
    key = (str(root), target)
    with _GUARD:
        if key in _RUNNING: raise ValueError('An analysis is already running for this source.')
        report = result_path(root, target)
        if not report.is_file(): raise ValueError('Run a full analysis first.')
        try: data = json.loads(report.read_text(encoding='utf-8'))
        except (OSError, ValueError): raise ValueError('Analysis report could not be read.')
        if data.get('status') not in ('complete', 'partial') or data.get('source_signature') != _signature(path):
            raise ValueError('A current completed or partial report is required.')
        cuts = (data.get('stages') or {}).get('scene_changes') or {}
        if cuts.get('status') != 'complete' or not cuts.get('result'):
            raise ValueError('A completed scene-change analysis is required.')
        previous = (data['stages'].get('visual') or {}).get('result')
        _RUNNING.add(key)
    try:
        data['status'] = 'queued'; data['stages']['visual'] = {'status': 'queued'}
        if previous: data['stages']['visual']['result'] = previous
        _write(report, data)
        def run():
            try:
                with _WORKER:
                    data['status'] = 'running'; _write(report, data)
                    def checkpoint(result):
                        data['stages']['visual'] = {'status': 'running', 'result': result}; _write(report, data)
                    _stage(data, report, 'visual', lambda: _vision(path, cuts['result'], checkpoint))
                    # _stage clears the pending value. Preserve old observations if
                    # the new attempt failed before producing its first frame.
                    if data['stages']['visual']['status'] == 'unavailable' and previous:
                        data['stages']['visual']['status'] = 'partial'
                        data['stages']['visual']['result'] = previous
                    data['status'] = 'complete' if all(x['status'] in ('complete', 'not_requested') for x in data['stages'].values()) else 'partial'
                    data['finished_at'] = time.time()
                    if _signature(path) != data['source_signature']:
                        data['status'] = 'stale'; data['detail'] = 'Source changed during analysis; run again.'
                    _write(report, data)
            except Exception as e:
                data['status'] = 'partial'; data['stages']['visual'] = {'status': 'unavailable', 'detail': str(e)[:600]}
                if previous:
                    data['stages']['visual'].update(status='partial', result=previous)
                try: _write(report, data)
                except OSError: pass
            finally:
                with _GUARD: _RUNNING.discard(key)
        threading.Thread(target=run, daemon=True).start()
    except Exception:
        with _GUARD: _RUNNING.discard(key)
        raise


def _stage(data, report, name, fn):
    data['stages'][name] = {'status': 'running'}; _write(report, data)
    try:
        data['stages'][name] = {'status': 'complete', 'result': fn()}
    except Exception as e:
        partial = data['stages'][name].get('result')
        data['stages'][name] = {'status': 'partial' if partial else 'unavailable', 'detail': str(e)[:600]}
        if partial: data['stages'][name]['result'] = partial
    _write(report, data)


def _decode_audio(source, wav):
    # Preserve audio's start relative to the media timeline. Otherwise FFmpeg's
    # WAV muxer starts a delayed stream at zero and every observation shifts early.
    # start_at_zero anchors to the container start, not the audio stream start;
    # aresample inserts silence or trims negative samples against that origin.
    cmd = [imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y',
           '-copyts', '-start_at_zero', '-i', str(source), '-map', '0:a:0',
           '-af', 'aresample=async=1:first_pts=0', '-ac', '1', '-ar', '16000',
           '-c:a', 'pcm_s16le', str(wav)]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=7200)
    if proc.returncode: raise RuntimeError('No decodable audio stream: ' + proc.stderr.decode(errors='replace')[-300:])


def _transcribe(wav, on_progress=None):
    model_path = _whisper_model_path()
    if not _whisper_model_ready(model_path):
        raise RuntimeError('Set STORYROOM_WHISPER_MODEL_DIR to a complete local Faster-Whisper model directory containing model.bin, config.json and tokenizer.json. No download is attempted.')
    from faster_whisper import WhisperModel
    model = WhisperModel(model_path, device='cpu', compute_type='int8', cpu_threads=max(1, min(2, os.cpu_count() or 1)), local_files_only=True)
    try:
        segments, info = model.transcribe(str(wav), beam_size=3, vad_filter=True, word_timestamps=True)
        items = []
        def snapshot():
            return {'language': info.language, 'language_probability': info.language_probability, 'segments': list(items)}
        checkpoint_at = time.monotonic()
        for s in segments:
            items.append({'start_s': round(s.start, 3), 'end_s': round(s.end, 3), 'text': s.text.strip(),
                          'words': [{'start_s': round(w.start, 3) if w.start is not None else None, 'end_s': round(w.end, 3) if w.end is not None else None, 'text': w.word} for w in (s.words or [])]})
            if on_progress and (len(items) == 1 or time.monotonic() - checkpoint_at >= 5):
                on_progress(snapshot()); checkpoint_at = time.monotonic()
        return snapshot()
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
    model = _yamnet_model_path()
    if not model or not Path(model).is_file():
        raise RuntimeError('Set STORYROOM_YAMNET_MODEL to a compatible local YAMNet waveform ONNX model.')
    with (Path(__file__).parent / 'data' / 'yamnet_class_map.csv').open(encoding='utf-8', newline='') as f:
        labels = [row['display_name'] for row in csv.DictReader(f)]
    opts = ort.SessionOptions(); opts.intra_op_num_threads = 2; opts.inter_op_num_threads = 1
    session = ort.InferenceSession(model, sess_options=opts, providers=['CPUExecutionProvider'])
    inputs, outputs = session.get_inputs(), session.get_outputs()
    shape = inputs[0].shape if len(inputs) == 1 else []
    names = [o.name for o in outputs]
    fixed = shape == [1, 48000] and 'scores' in names
    dynamic = len(shape) == 1 and not isinstance(shape[0], int) and 'output_0' in names
    if len(inputs) != 1 or inputs[0].type != 'tensor(float)' or not (fixed or dynamic):
        raise RuntimeError('Incompatible YAMNet model: expected a supported float32 waveform input and score output.')
    output_name = 'scores' if fixed else 'output_0'
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
                if fixed: y=np.pad(y, (0, 48000-length))[None, :]
                scores=np.asarray(session.run([output_name], {inputs[0].name: y})[0])
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


def _require_offline_ollama(opener, model):
    """Fail closed before sending image data to a loopback Ollama server.

    Loopback alone is insufficient: Ollama can forward cloud models remotely.
    Require the server's cloud-disabled status and reject remote model metadata.
    """
    if not model or model.lower().endswith((':cloud', '-cloud')):
        raise RuntimeError('Cloud Ollama models are not permitted for local analysis.')
    try:
        with opener.open('http://127.0.0.1:11434/api/status', timeout=10) as response:
            status = json.load(response)
        if not isinstance(status, dict) or not isinstance(status.get('cloud'), dict) or status['cloud'].get('disabled') is not True:
            raise RuntimeError('Ollama cloud must be disabled before analysing frames. Start Ollama with OLLAMA_NO_CLOUD=1.')
        request = urllib.request.Request('http://127.0.0.1:11434/api/show',
            data=json.dumps({'model': model}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
        with opener.open(request, timeout=10) as response: details = json.load(response)
    except (OSError, urllib.error.URLError, ValueError) as e:
        raise RuntimeError('Cannot verify Ollama local-only status and model. Use a recent Ollama with OLLAMA_NO_CLOUD=1: ' + str(e)) from e
    if not isinstance(details, dict) or details.get('remote_host') or details.get('remote_model'):
        raise RuntimeError('Ollama model is remote or its local status could not be verified.')
    capabilities = details.get('capabilities')
    if capabilities is not None and (not isinstance(capabilities, list) or 'vision' not in capabilities):
        raise RuntimeError('The selected local Ollama model does not report vision support.')


def _vision(path, scenes, on_progress=None):
    # No arbitrary URL or redirects: image data stays on this computer.
    model = os.environ.get('STORYROOM_OLLAMA_MODEL', 'qwen3-vl:2b')
    max_frames = max(1, min(300, int(os.environ.get('STORYROOM_VISION_MAX_FRAMES', '60'))))
    sampled = _sample_frames(scenes, max_frames)
    observations = []
    attempted_chat = False
    def snapshot():
        return {'observations': list(observations), 'sampled_frames': len(observations),
                'planned_frames': len(sampled), 'total_scenes': len(scenes),
                'note': 'Sampled still frames do not describe every moment or establish identities.'}
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs): return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with tempfile.TemporaryDirectory(prefix='storyroom-frames-') as tmp:
            for at, scene in sampled:
                _require_offline_ollama(opener, model)
                image = Path(tmp) / 'frame.jpg'
                proc = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-nostdin', '-v', 'error', '-y', '-ss', str(at), '-i', str(path), '-frames:v', '1', '-vf', 'scale=640:640:force_original_aspect_ratio=decrease:force_divisible_by=2', str(image)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=120)
                if proc.returncode or not image.is_file(): raise RuntimeError('Could not extract frame at %.3fs' % at)
                payload = {'model': model, 'stream': False, 'keep_alive': '2m', 'options': {'num_ctx': 2048, 'num_thread': 2, 'num_predict': 256}, 'messages': [{'role': 'user',
                    'content': 'Describe only what is visibly supported: activity, objects, people, setting, and readable text. Say when uncertain; do not infer real identities.',
                    'images': [base64.b64encode(image.read_bytes()).decode('ascii')]}]}
                req = urllib.request.Request('http://127.0.0.1:11434/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'}, method='POST')
                try:
                    attempted_chat = True
                    with opener.open(req, timeout=300) as response: result = json.load(response)
                except (OSError, urllib.error.URLError) as e:
                    raise RuntimeError('Local Ollama unavailable or model missing: ' + str(e)) from e
                text = (result.get('message') or {}).get('content', '').strip()
                if not text: raise RuntimeError('Ollama returned no visual description.')
                observations.append({'frame_time_s': round(at, 3), 'scene_start_s': scene['start_s'], 'scene_end_s': scene['end_s'], 'description': text})
                if on_progress: on_progress(snapshot())
        return snapshot()
    finally:
        if attempted_chat:
            # Keep the model warm between frames, then release it to conserve RAM.
            request = urllib.request.Request('http://127.0.0.1:11434/api/generate',
                data=json.dumps({'model': model, 'keep_alive': 0}).encode(), headers={'Content-Type': 'application/json'}, method='POST')
            try:
                with opener.open(request, timeout=10): pass
            except (OSError, urllib.error.URLError): pass


def _run(root, target, source, data, observer=None):
    """Analyse one source. `observer(stage, detail)` reports progress to the queue."""
    key = (str(root), target); report = result_path(root, target)
    def announce(stage, detail=None):
        if observer:
            try: observer(stage, detail)
            except Exception: pass
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
                    def checkpoint_transcript(result):
                        data['stages']['transcript'] = {'status': 'running', 'result': result}
                        _write(report, data)
                    announce('transcript', 'Reading speech')
                    _stage(data, report, 'transcript', lambda: _transcribe(wav, checkpoint_transcript))
                    announce('audio_features', 'Measuring the audio')
                    _stage(data, report, 'audio_features', lambda: _audio_features(wav))
                    if _yamnet_model_path() and Path(_yamnet_model_path()).is_file():
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
                announce('scene_changes', 'Finding scene changes')
                _stage(data, report, 'scene_changes', lambda: _scenes(source, data['duration_s']))
                cuts = data['stages']['scene_changes']
                if cuts['status'] == 'complete':
                    def checkpoint_visual(result):
                        data['stages']['visual'] = {'status': 'running', 'result': result}
                        _write(report, data)
                    announce('visual', 'Examining frames')
                    _stage(data, report, 'visual', lambda: _vision(source, cuts['result'], checkpoint_visual))
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
              'transcript': [], 'sound_events': [], 'visual': [], 'speakers': [],
              'scene_changes': [], 'audio_features': [], 'low_energy_intervals': [], 'stage_status': {},
              'note': 'Source observations are estimates; timeline times are mapped from the saved in-point and not editorial verification.',
              'timing_warning': report.get('timing_warning')}
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
        return stage.get('result', {}) if stage.get('status') in ('complete', 'partial') else {}
    result['stage_status'] = {name: stage.get('status', 'unknown') for name, stage in stages.items()}
    result['transcript'] = interval(data('transcript').get('segments', []))
    for segment in result['transcript']:
        # Keep original words unchanged; map only words with known overlapping times.
        segment['timeline_words'] = interval([word for word in segment.get('words', [])
                                              if word.get('start_s') is not None and word.get('end_s') is not None])
    result['sound_events'] = interval(data('sound_events').get('windows', []))
    result['speakers'] = interval(data('speakers').get('turns', []))
    result['scene_changes'] = interval(data('scene_changes') or [])
    result['audio_features'] = interval(data('audio_features').get('windows', []))
    result['low_energy_intervals'] = interval(data('audio_features').get('low_energy_intervals', []))
    for item in data('visual').get('observations', []):
        at = float(item['frame_time_s'])
        if source_in <= at < source_out:
            result['visual'].append(dict(item, timeline_time_s=round(timeline_in + at - source_in, 3)))
    return result
