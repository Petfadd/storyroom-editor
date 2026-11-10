#!/usr/bin/env python3
"""Install an externally hosted YAMNet ONNX model for local CPU inference.

The model file is hosted by amplify-project/Physiological-Signals, whose
repository is AGPL-3.0. It identifies the file as YAMNet but does not document
its conversion provenance. See README for attribution and limitations.
"""
import argparse
import hashlib
import os
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / '.cache/storyroom-models/yamnet.onnx'
URL = 'https://api.github.com/repos/amplify-project/Physiological-Signals/git/blobs/15658631e3ab2705fbf72dc302b9df8a1304a8fa'
SHA256 = 'b96e5ca9359eb99ba3e8e372729bd61ef24274c8aa0d53022bbbfb04388a4527'
SIZE = 16093366


def install(dest):
    dest = Path(dest).expanduser().resolve()
    if dest.exists():
        if dest.is_file() and dest.stat().st_size == SIZE and hashlib.sha256(dest.read_bytes()).hexdigest() == SHA256:
            print('Already installed and verified:', dest); return
        raise FileExistsError('Destination exists but is not the pinned model; nothing was replaced.')
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='storyroom-yamnet-', dir=dest.parent) as tmp:
        path = Path(tmp) / 'model.onnx'; digest = hashlib.sha256()
        request = urllib.request.Request(URL, headers={'Accept': 'application/vnd.github.raw+json', 'User-Agent': 'Storyroom-local-model-installer'})
        with urllib.request.urlopen(request, timeout=120) as src, path.open('wb') as out:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk: break
                out.write(chunk); digest.update(chunk)
        if path.stat().st_size != SIZE or digest.hexdigest() != SHA256:
            raise ValueError('Model checksum or size mismatch; nothing was installed.')
        # Use a hard link to avoid overwriting an existing destination in a race.
        os.link(path, dest)
    print('Installed verified local model:', dest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default=str(DEFAULT))
    install(parser.parse_args().dest)
