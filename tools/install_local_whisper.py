#!/usr/bin/env python3
"""Install a pinned CTranslate2 Whisper small model; inference remains offline.

Fetches a public, MIT-licensed mirror of Systran's Whisper-small conversion from
GitHub, verifies pinned SHA-256 values, and installs outside version control.
"""
import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / '.cache' / 'storyroom-models' / 'whisper-small'
REPO = 'YankS1/faster-whisper-mirror'
COMMIT = '40efd16c5a10f523b0eff5c6f6eca5cdd561e90d'
MODEL_SIZE = 483546902
MODEL_HASH = '3e305921506d8872816023e4c273e75d2419fb89b24da97b4fe7bce14170d671'
OTHER = {
    'config.json': 'b55496ac7940a7ae47d2c01eab40edfd8701feec1229d9cce3b40014383fb828',
    'tokenizer.json': 'fb7b63191e9bb045082c79fd742a3106a12c99513ab30df4a0d47fa6cb6fd0ab',
    'vocabulary.txt': '34ce3fe1c5041027b3f8d42912270993f986dbc4bb34cf27f951e34a1e453913',
}


def file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as src:
        for block in iter(lambda: src.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def valid(dest):
    try:
        return (dest / 'model.bin').stat().st_size == MODEL_SIZE and file_hash(dest / 'model.bin') == MODEL_HASH and all(file_hash(dest / name) == digest for name, digest in OTHER.items())
    except OSError:
        return False


def download(name, out, digest):
    url = f'https://api.github.com/repos/{REPO}/contents/small/{name}?ref={COMMIT}'
    request = urllib.request.Request(url, headers={'Accept': 'application/vnd.github.raw+json', 'User-Agent': 'Storyroom-local-model-installer'})
    with urllib.request.urlopen(request, timeout=120) as source:
        while True:
            chunk = source.read(1024 * 1024)
            if not chunk: break
            out.write(chunk); digest.update(chunk)


def install(dest):
    dest = Path(dest).expanduser().resolve()
    if dest.exists():
        if valid(dest): print('Already installed and verified:', dest); return
        raise FileExistsError('Destination exists but does not match the pinned model; choose another destination. Nothing was replaced.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='storyroom-whisper-download-', dir=dest.parent) as tmp:
        tmp = Path(tmp)
        for name, expected in OTHER.items():
            print('Downloading', name, flush=True)
            h = hashlib.sha256()
            with (tmp / name).open('wb') as out: download(name, out, h)
            if h.hexdigest() != expected: raise ValueError('Checksum mismatch for ' + name)
        h = hashlib.sha256()
        with (tmp / 'model.bin').open('wb') as out:
            for i in range(11):
                name = f'model.bin.part-{i:02}'
                print('Downloading', name, flush=True); download(name, out, h)
        if (tmp / 'model.bin').stat().st_size != MODEL_SIZE or h.hexdigest() != MODEL_HASH:
            raise ValueError('Model checksum or size mismatch; no model was installed.')
        # Rename the verified directory only after all files pass. Never replace an existing directory.
        os.rename(tmp, dest)
    print('Installed verified local model:', dest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default=str(DEFAULT), help='Empty destination directory; defaults to the local model cache')
    args = parser.parse_args()
    install(args.dest)
