"""Local, CPU-only speech recognition for documentary sound bites.

Only the saved source IN/OUT is decoded, downmixed and resampled in memory. No
network access is attempted and no project file is changed here: this module
returns words, and the caller decides whether to keep them.
"""
from pathlib import Path
import os, sys

MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json')


def model_dir():
    configured = os.environ.get('STORYROOM_WHISPER_MODEL_DIR', '').strip()
    return Path(configured) if configured else Path(__file__).resolve().parent / '.cache' / 'storyroom-models' / 'whisper-small'


LOCAL_LIBS = Path(__file__).resolve().parent / '.cache' / 'pylibs'


def _local_import_path():
    if LOCAL_LIBS.is_dir() and str(LOCAL_LIBS) not in sys.path:
        sys.path.insert(0, str(LOCAL_LIBS))


def _import_whisper():
    # A private installed copy wins, so speech stays offline and CPU-only here.
    _local_import_path()
    from faster_whisper import WhisperModel
    return WhisperModel


def available():
    folder = model_dir()
    try:
        _import_whisper()
    except Exception:
        return False, 'The local faster-whisper package is not installed. No download was attempted.'
    if not all((folder / name).is_file() for name in MODEL_FILES):
        return False, ('No local speech model is installed. Put a Faster-Whisper model (model.bin, config.json, '
                       'tokenizer.json) in ' + str(folder) + ' or set STORYROOM_WHISPER_MODEL_DIR.')
    return True, ''


def _decode(path, start_s, end_s):
    """Decode one interval to 16 kHz mono float32 samples, in memory only."""
    _local_import_path()
    import av
    import numpy as np
    start_s, end_s = max(0.0, float(start_s)), float(end_s)
    if end_s <= start_s:
        raise ValueError('The sound bite has no audio interval.')
    with av.open(str(path)) as media:
        if not media.streams.audio:
            raise ValueError('The sound-bite source has no audio stream.')
        stream = media.streams.audio[0]
        resampler = av.AudioResampler(format='flt', layout='mono', rate=16000)
        chunks = []
        # Decode forward from the start of the file and keep only the requested
        # samples. Seeking is avoided because container seeks are keyframe-based
        # and can land after the IN point for some sources.
        for frame in media.decode(stream):
            if frame.pts is None:
                continue
            frame_pts = float(frame.pts * frame.time_base)
            for converted in resampler.resample(frame):
                samples = converted.to_ndarray().reshape(-1)
                # Use this converted frame's own start time; the resampler can
                # return several frames for one decoded frame.
                converted_start = float(converted.pts * converted.time_base) if converted.pts is not None else frame_pts
                lo = int(round((start_s - converted_start) * 16000))
                hi = int(round((end_s - converted_start) * 16000))
                lo, hi = max(lo, 0), min(hi, len(samples))
                if hi > lo:
                    chunks.append(samples[lo:hi])
                frame_pts = max(frame_pts, converted_start + len(samples) / 16000)
            if frame_pts >= end_s:
                break
    if not chunks:
        raise ValueError('No audio could be decoded from the saved bite interval.')
    pcm = np.concatenate(chunks).astype('float32')
    expected = int(round((end_s - start_s) * 16000))
    if abs(len(pcm) - expected) > 4000:
        raise ValueError('Decoded bite audio does not match the saved IN/OUT window.')
    # Report the real decoded window so segments are never described as fitting
    # a longer interval than the decoder returned.
    return pcm, len(pcm) / 16000


def transcribe(path, start_s, end_s):
    """Return bite-relative segments from a local model. Never guesses silence."""
    ok, reason = available()
    if not ok:
        raise ValueError(reason)
    WhisperModel = _import_whisper()
    pcm, decoded_s = _decode(Path(path), start_s, end_s)
    model = WhisperModel(str(model_dir()), device='cpu', compute_type='int8',
                         cpu_threads=max(1, min(4, os.cpu_count() or 1)), local_files_only=True)
    try:
        segments, info = model.transcribe(pcm, language='en', beam_size=5, vad_filter=True,
                                          condition_on_previous_text=False)
        items = []
        for segment in segments:
            text = segment.text.strip()
            if not text:
                continue
            items.append({'start_s': round(max(0.0, float(segment.start)), 3),
                          'end_s': round(float(segment.end), 3), 'text': text})
    finally:
        del model
        import gc
        gc.collect()
    # Whisper pads the end of the clip, so the final timestamp can point past the
    # real audio. Clamp to the decoded length instead of inventing extra speech.
    clamped = []
    for item in items:
        item['end_s'] = round(min(item['end_s'], decoded_s), 3)
        if item['end_s'] > item['start_s']:
            clamped.append(item)
    if not clamped:
        raise ValueError('No speech was recognised in this bite interval. Nothing was saved.')
    return clamped, {'engine': 'faster-whisper', 'model': model_dir().name,
                   'decoded_s': round(decoded_s, 3),
                   'requested_window_s': round(float(end_s) - float(start_s), 3),
                   'end_clamped_to_decoded_audio': bool(items and items[-1]['end_s'] >= decoded_s - 0.001),
                   'language': info.language, 'language_probability': round(float(info.language_probability), 4),
                   'device': 'cpu', 'network': 'none', 'verified': 'machine transcription; listen before relying on it'}
