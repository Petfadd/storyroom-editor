"""What is missing for local speech recognition, and how to install it.

Speech recognition needs two separate things, and conflating them is why the editor used
to report a single unhelpful line:

1. the `faster-whisper` Python package, which does the inference;
2. the model files, which are about 484 MB and are not shipped.

Either can be missing on its own, and the fix is different for each. This module works
out which one is missing, explains it in terms a person can act on, and can install
either one on request.

Everything is offline apart from the download itself, which the user has to ask for.
No model is fetched on a status check.
"""
from __future__ import annotations

import importlib
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LIB_DIR = ROOT / '.cache' / 'pylibs'
MODEL_DIR = ROOT / '.cache' / 'storyroom-models' / 'whisper-small'

MODEL_FILES = ('model.bin', 'config.json', 'tokenizer.json')

# One install at a time, whichever half is being installed.
_GUARD = threading.Lock()
_RUNNING: dict[str, dict] = {}


def _package_available() -> tuple[bool, str]:
    if LIB_DIR.is_dir() and str(LIB_DIR) not in sys.path:
        sys.path.append(str(LIB_DIR))
    try:
        module = importlib.import_module('faster_whisper')
        return True, getattr(module, '__version__', 'installed')
    except Exception as error:
        return False, str(error)


def _model_available() -> tuple[bool, str]:
    import bite_speech
    folder = bite_speech.model_dir()
    missing = [name for name in MODEL_FILES if not (folder / name).is_file()]
    if missing:
        return False, f'Missing {", ".join(missing)}'
    size = (folder / 'model.bin').stat().st_size
    return True, f'{size / 1024 ** 2:.0f} MB'


def status() -> dict:
    """The honest state of local speech, and the next step when something is missing."""
    package_ok, package_detail = _package_available()
    model_ok, model_detail = _model_available()
    ready = package_ok and model_ok

    if ready:
        step = ''
        message = 'Local speech recognition is ready.'
    elif not package_ok and not model_ok:
        step = 'both'
        message = ('Speech recognition needs two downloads: the faster-whisper package '
                   'and the speech model (about 484 MB). Both stay on this computer.')
    elif not package_ok:
        step = 'package'
        message = ('The speech model is installed but faster-whisper is not. '
                   'Install the package to finish setup.')
    else:
        step = 'model'
        message = ('faster-whisper is installed but the speech model is missing '
                   '(about 484 MB). Install the model to finish setup.')

    with _GUARD:
        running = dict(_RUNNING.get('speech') or {})
    return {'ready': ready, 'package': {'installed': package_ok, 'detail': package_detail},
            'model': {'installed': model_ok, 'detail': model_detail},
            'step': step, 'message': message,
            'model_dir': str(MODEL_DIR), 'lib_dir': str(LIB_DIR),
            'offline': True, 'installing': running}


def _run(kind: str) -> None:
    """Install one half, recording progress so the interface can show it."""
    record = _RUNNING['speech']
    try:
        record.update(status='running', detail='Downloading and verifying')
        if kind in ('package', 'both'):
            from tools import install_local_package
            install_local_package.install(LIB_DIR)
        if kind in ('model', 'both'):
            from tools import install_local_whisper
            install_local_whisper.install(MODEL_DIR)
        record.update(status='done', detail='Installed')
    except SystemExit as error:                       # the installers exit on failure
        record.update(status='failed', detail=str(error) or 'Installation stopped.')
    except Exception as error:
        record.update(status='failed', detail=str(error)[:300])
    finally:
        importlib.invalidate_caches()
        if LIB_DIR.is_dir() and str(LIB_DIR) not in sys.path:
            sys.path.append(str(LIB_DIR))


def start(kind: str = 'auto') -> dict:
    """Begin installing. `kind` is 'model', 'package', 'both' or 'auto' for what is missing."""
    state = status()
    if state['ready']:
        return {'started': False, 'reason': 'Speech recognition is already installed.'}
    kind = state['step'] if kind == 'auto' else kind
    if kind not in ('model', 'package', 'both'):
        return {'started': False, 'reason': 'Unknown install step.'}
    with _GUARD:
        existing = _RUNNING.get('speech')
        if existing and existing.get('status') == 'running':
            return {'started': False, 'reason': 'An installation is already running.'}
        _RUNNING['speech'] = {'status': 'running', 'detail': 'Starting', 'kind': kind}
    threading.Thread(target=_run, args=(kind,), daemon=True,
                     name='speech-install').start()
    return {'started': True, 'kind': kind}


def install_progress() -> dict:
    with _GUARD:
        return dict(_RUNNING.get('speech') or {'status': 'idle'})
