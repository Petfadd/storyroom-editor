"""Upload progress, cancellation and direct-to-disk streaming for Storyroom.

This module is required by the upload endpoints. It keeps a small in-memory state
per (project, upload key), supports browser progress polling, allows cancellation,
and streams large MP4/MOV uploads to disk without buffering the whole file.
"""
from __future__ import annotations
import re, threading, time, uuid
from contextlib import contextmanager
from pathlib import Path
from flask import has_request_context, request
from werkzeug.exceptions import BadRequest

_KEY = re.compile(r'^[0-9a-fA-F]{16,64}$')
_STATES = {}
_LOCK = threading.RLock()


def token(value):
    """Return a browser-supplied upload key, or generate one for the upload route."""
    key = str(value or '').strip()
    if _KEY.fullmatch(key):
        return key
    if has_request_context() and request.endpoint == 'upload':
        return uuid.uuid4().hex
    return ''


def _default():
    return {'stage': 'waiting', 'detail': '', 'saved_bytes': 0, 'filename': '', 'cancelled': False, 'at': time.time()}


def update(pid, key, stage, detail='', saved_bytes=None, filename=None, error=False):
    with _LOCK:
        state = _STATES.setdefault((str(pid), str(key)), _default())
        state['stage'] = str(stage)
        if detail is not None:
            state['detail'] = str(detail)[:500]
        if saved_bytes is not None:
            state['saved_bytes'] = int(saved_bytes)
        if filename is not None:
            state['filename'] = str(filename)
        if error:
            state['error'] = True
        if stage == 'cancelled':
            state['cancelled'] = True
        if stage in ['done', 'error']:
            state['cancelled'] = False
        state['at'] = time.time()
        return dict(state)


def get(pid, key):
    with _LOCK:
        return dict(_STATES.get((str(pid), str(key)), _default()))


def cancel(pid, key):
    with _LOCK:
        state = _STATES.setdefault((str(pid), str(key)), _default())
        state.update({'stage': 'cancelled', 'detail': 'Import cancelled.', 'cancelled': True, 'at': time.time()})
        return dict(state)


def check(pid, key):
    state = get(pid, key)
    if state.get('cancelled'):
        raise BadRequest('Import cancelled.')


@contextmanager
def project_lock(lock):
    lock.acquire()
    try:
        yield
    finally:
        lock.release()


def save_local(f, dest, pid, key, chunk=1024 * 1024):
    """Write an uploaded file in chunks while reporting progress and cancellation."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    stream = getattr(f, 'stream', None)
    if stream is None and hasattr(f, 'read'):
        stream = f
    if stream is None:
        raise BadRequest('The upload stream could not be read.')
    written = 0
    with open(dest, 'wb') as out:
        while True:
            check(pid, key)
            data = stream.read(chunk)
            if not data:
                break
            out.write(data)
            written += len(data)
            update(pid, key, 'saving', saved_bytes=written)
    return written
