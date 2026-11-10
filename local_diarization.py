"""Subprocess entry point for an explicitly installed, local pyannote pipeline."""
import json
import sys
from pathlib import Path


def main(model_dir, wav, destination):
    import soundfile as sf
    import torch
    from pyannote.audio import Pipeline

    torch.set_num_threads(2)
    waveform, sample_rate = sf.read(wav, dtype='float32', always_2d=True)
    # Passing samples avoids torchcodec's system-FFmpeg dependency on Windows.
    signal = torch.from_numpy(waveform.T.copy())
    pipeline = Pipeline.from_pretrained(str(Path(model_dir).resolve()))
    if pipeline is None:
        raise RuntimeError('Could not load the local pyannote pipeline.')
    pipeline.to(torch.device('cpu'))
    output = pipeline({'waveform': signal, 'sample_rate': sample_rate})
    annotation = getattr(output, 'exclusive_speaker_diarization', None)
    if annotation is None:
        annotation = getattr(output, 'speaker_diarization', output)
    turns = [{'start_s': round(turn.start, 3), 'end_s': round(turn.end, 3), 'speaker': str(speaker)}
             for turn, _, speaker in annotation.itertracks(yield_label=True)]
    Path(destination).write_text(json.dumps({'turns': turns,
        'note': 'Anonymous per-file speaker labels; they do not identify people.'}), encoding='utf-8')


if __name__ == '__main__':
    main(*sys.argv[1:])
