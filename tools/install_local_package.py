#!/usr/bin/env python3
"""Install the local speech-recognition packages into .cache/pylibs.

`bite_speech` already prefers a private copy of the libraries in `.cache/pylibs` over
anything installed system-wide, so that speech recognition stays offline and cannot be
disturbed by an unrelated pip install. This is the script that fills that folder.

Why not just `pip install faster-whisper`? Because a system install can be replaced by
another project's dependency resolution, and because on Windows the app is often run
from a folder without a virtual environment. A private copy is predictable.

Nothing here touches the network except to fetch the wheels, and nothing is installed
unless every wheel downloads successfully. An interrupted run leaves the existing copy
exactly as it was.
"""
from __future__ import annotations

import argparse
import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = ROOT / '.cache' / 'pylibs'

# The packages speech recognition needs. faster-whisper pulls the rest, but they are
# named explicitly so a failure says which one failed.
PACKAGES = ['faster-whisper']


def _python() -> str:
    return sys.executable or 'python'


def install(dest: Path, upgrade: bool = False) -> None:
    dest = Path(dest).expanduser().resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix='storyroom-pylibs-') as tmp:
        tmp = Path(tmp)
        command = [_python(), '-m', 'pip', 'install', '--target', str(tmp)]
        if upgrade:
            command.append('--upgrade')
        command += PACKAGES
        print('Installing', ', '.join(PACKAGES), 'into a staging folder', flush=True)
        result = subprocess.run(command)
        if result.returncode != 0:
            raise SystemExit('pip could not install the packages. Nothing was changed.')

        # Move into place only after a successful install. Individual top-level folders
        # are replaced rather than the whole directory, so a partial previous install
        # does not survive as a broken mixture.
        installed = [item for item in tmp.iterdir()]
        if not installed:
            raise SystemExit('pip reported success but installed nothing.')
        dest.mkdir(parents=True, exist_ok=True)
        for item in installed:
            target = dest / item.name
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            elif target.exists():
                target.unlink()
            shutil.move(str(item), str(target))
    print('Installed speech packages into:', dest)


def verify(dest: Path | None = None) -> tuple[bool, str]:
    """Can this machine import faster-whisper from the private folder?"""
    dest = Path(dest or DEFAULT).expanduser().resolve()
    if dest.is_dir() and str(dest) not in sys.path:
        sys.path.insert(0, str(dest))
    try:
        module = importlib.import_module('faster_whisper')
    except Exception as error:
        return False, f'faster-whisper is not importable: {error}'
    version = getattr(module, '__version__', 'unknown')
    return True, f'faster-whisper {version} is available from {dest}'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dest', default=str(DEFAULT),
                        help='Where to install; defaults to the local library cache')
    parser.add_argument('--upgrade', action='store_true',
                        help='Replace an existing copy')
    parser.add_argument('--verify', action='store_true',
                        help='Only report whether the packages can be imported')
    args = parser.parse_args()
    if args.verify:
        ok, detail = verify(Path(args.dest))
        print(detail)
        raise SystemExit(0 if ok else 1)
    install(Path(args.dest), upgrade=args.upgrade)
    ok, detail = verify(Path(args.dest))
    print(detail)
    raise SystemExit(0 if ok else 1)
