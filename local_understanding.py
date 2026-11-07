"""Optional local-only media understanding. Results are observations, not approvals.

Only reads project media and local model files; the sole HTTP endpoint is Ollama on
loopback. Jobs are serialized to bound CPU/RAM use and never modify the storyboard.
"""
import base64
import gc
import importlib.util
import json
import os
import subprocess
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
            'ollama_url': 'http://127.0.0.1:11434',
            'vision_model': os.environ.get('STORYROOM_OLLAMA_MODEL', 'qwen3-vl:2b'),
            'speaker_diarization': False,
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


def _audio_features(wav):
    import numpy as np
    import librosa
    chunks = []
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
            position += len(y)
    return {'windows': chunks, 'note': '10-second signal windows; low energy is not a speech, music, or sound-event classification.'}


def _scenes(path, duration):
    from scenedetect import detect, ContentDetector
    spans = detect(str(path), ContentDetector(), show_progress=False)
    if not spans: return [{'start_s': 0.0, 'end_s': round(duration, 3)}]
    return [{'start_s': round(a.get_seconds(), 3), 'end_s': round(b.get_seconds(), 3)} for a, b in spans]


def _vision(path, scenes):
    # No arbitrary URL or redirects: image data stays on this computer.
    model = os.environ.get('STORYROOM_OLLAMA_MODEL', 'qwen3-vl:2b')
    max_frames = max(1, min(300, int(os.environ.get('STORYROOM_VISION_MAX_FRAMES', '60'))))
    sampled = scenes if len(scenes) <= max_frames else [scenes[int(i * len(scenes) / max_frames)] for i in range(max_frames)]
    observations = []
    with tempfile.TemporaryDirectory(prefix='storyroom-frames-') as tmp:
        for scene in sampled:
            at = (scene['start_s'] + scene['end_s']) / 2
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
    return {'observations': observations, 'sampled_scenes': len(sampled), 'total_scenes': len(scenes), 'note': 'One sampled frame per selected scene; observations do not describe every moment.'}


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
            if data['kind'] == 'video':
                _stage(data, report, 'scene_changes', lambda: _scenes(source, data['duration_s']))
                cuts = data['stages']['scene_changes']
                if cuts['status'] == 'complete':
                    _stage(data, report, 'visual', lambda: _vision(source, cuts['result']))
                else:
                    data['stages']['visual'] = {'status':'unavailable','detail':'Scene detection failed; no visual frames analysed.'}
            data['stages']['speakers'] = {'status': 'unavailable', 'detail': 'Speaker diarization is not yet integrated; speaker identities are not inferred.'}
            data['status'] = 'complete' if all(x['status'] == 'complete' for x in data['stages'].values()) else 'partial'
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
