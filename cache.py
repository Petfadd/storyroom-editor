"""One cache for everything expensive, addressed by content rather than by name.

Every entry is stored under a key built from what actually went into it: the source
file's size and modification time, plus the settings that shaped the result. Two
consequences follow, and both remove whole classes of bug:

* An entry cannot be stale. If the source changed by a single byte, or a setting moved,
  the key changes and the old entry is simply never asked for again. There is no
  invalidation logic to get wrong, and no chance of a thumbnail showing a frame from a
  file that has since been replaced.
* Entries can be shared freely. Two scenes asking for the same frame at the same size
  hit the same file.

The cost of that is orphaned entries, which is why the cleanup routines exist: they are
what reclaims the space the old keys left behind.

Nothing here ever deletes anything under `sources/` or `assets/`. Only this module's own
directories are touched, and every removal is checked to be inside the cache root first.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# Where each kind of cached thing lives, and whether losing it is merely inconvenient
# or expensive enough to be worth showing the user how much space it holds.
KINDS = {
    'proxies': 'Editing copies of video',
    'thumbnails': 'Timeline and pool images',
    'frames': 'Frames pulled from video',
    'analysis': 'Scene detection and reframe decisions',
    'speech': 'Transcription and speaker results',
    'enhanced': 'AI enhancement output',
    'faststart': 'Seekable copies of uploaded video',
    'renders': 'Temporary render segments',
}

# Anything older than this is fair game for automatic cleanup. It is long enough that a
# project left open overnight keeps its caches, which is what makes reopening it fast.
STALE_SECONDS = 14 * 24 * 3600

_GUARD = threading.Lock()


def fingerprint(path: Path) -> dict:
    """Identify a source file by what it contains, cheaply.

    Size and nanosecond modification time, not a hash of the bytes: hashing a 4 GB video
    would cost more than regenerating the thumbnail it identifies.
    """
    stat = Path(path).stat()
    return {'size': stat.st_size, 'mtime': stat.st_mtime_ns}


def key(*parts) -> str:
    """A short, stable key for anything JSON-serialisable."""
    payload = json.dumps(parts, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:32]


def entry_key(path: Path, *settings) -> str:
    """Key for something derived from `path` under `settings`."""
    try:
        base = fingerprint(path)
    except OSError:
        return key('missing', str(path), *settings)
    return key(str(Path(path).resolve()), base['size'], base['mtime'], *settings)


@dataclass
class Store:
    """A cache rooted in one directory."""
    root: Path

    def __post_init__(self):
        self.root = Path(self.root)

    def dir(self, kind: str) -> Path:
        target = self.root / kind
        target.mkdir(parents=True, exist_ok=True)
        return target

    def path(self, kind: str, name: str, suffix: str = '') -> Path:
        name = hashlib.sha256(name.encode()).hexdigest()[:40] if len(name) > 80 else name
        return self.dir(kind) / (name + suffix)

    def has(self, kind: str, name: str, suffix: str = '',
            min_bytes: int = 1, newer_than: Path | None = None) -> bool:
        target = self.path(kind, name, suffix)
        try:
            if not target.is_file() or target.stat().st_size < min_bytes:
                return False
            if newer_than is not None and target.stat().st_mtime < newer_than.stat().st_mtime:
                return False
            return True
        except OSError:
            return False

    def write_json(self, kind: str, name: str, data) -> Path:
        target = self.path(kind, name, '.json')
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps(data, default=str))
        temp.replace(target)
        return target

    def read_json(self, kind: str, name: str):
        target = self.path(kind, name, '.json')
        try:
            return json.loads(target.read_text())
        except (OSError, ValueError):
            return None

    def touch(self, kind: str, name: str, suffix: str = '') -> None:
        """Record that an entry was just used, so cleanup keeps it."""
        try:
            os.utime(self.path(kind, name, suffix), None)
        except OSError:
            pass

    # -- management -------------------------------------------------------
    def usage(self) -> dict:
        """Bytes held per kind, and in total."""
        report = {'kinds': {}, 'total_bytes': 0, 'total_gb': 0.0, 'root': str(self.root)}
        if not self.root.exists():
            return report
        for kind, label in KINDS.items():
            folder = self.root / kind
            size, count = 0, 0
            if folder.is_dir():
                for item in folder.rglob('*'):
                    try:
                        if item.is_file():
                            size += item.stat().st_size
                            count += 1
                    except OSError:
                        continue
            report['kinds'][kind] = {'label': label, 'bytes': size,
                                     'gb': round(size / 1024 ** 3, 2), 'files': count}
            report['total_bytes'] += size
        report['total_gb'] = round(report['total_bytes'] / 1024 ** 3, 2)
        return report

    def clear(self, kind: str | None = None) -> dict:
        """Delete cached entries. Never touches anything outside this cache root."""
        removed = {'files': 0, 'bytes': 0}
        targets = [kind] if kind else [k for k in KINDS if (self.root / k).is_dir()]
        for name in targets:
            folder = (self.root / name).resolve()
            # A cache root can be chosen by the user, so prove the target really is
            # inside it before removing a tree.
            try:
                if not folder.is_relative_to(self.root.resolve()):
                    continue
            except (OSError, ValueError):
                continue
            if not folder.is_dir():
                continue
            for item in folder.rglob('*'):
                try:
                    if item.is_file():
                        removed['bytes'] += item.stat().st_size
                        removed['files'] += 1
                except OSError:
                    continue
            shutil.rmtree(folder, ignore_errors=True)
        return removed

    def cleanup(self, keep_seconds: int = STALE_SECONDS) -> dict:
        """Remove entries that have not been used recently.

        Only this cache is touched. Originals live in `sources/` and `assets/`, which are
        outside every cache kind, so they cannot be reached by this function at all.
        """
        cutoff = time.time() - keep_seconds
        removed = {'files': 0, 'bytes': 0}
        if not self.root.exists():
            return removed
        with _GUARD:
            for kind in KINDS:
                folder = self.root / kind
                if not folder.is_dir():
                    continue
                for item in folder.rglob('*'):
                    try:
                        if item.is_file() and item.stat().st_mtime < cutoff:
                            removed['bytes'] += item.stat().st_size
                            removed['files'] += 1
                            item.unlink(missing_ok=True)
                    except OSError:
                        continue
        return removed


def would_fill_disk(store: Store, needed_bytes: int, headroom_gb: float = 1.0) -> str:
    """A refusal message when an operation clearly cannot fit, otherwise ''.

    Deliberately conservative about when it warns: a warning on a machine with plenty of
    space trains the user to ignore warnings.
    """
    try:
        free = shutil.disk_usage(str(store.root if store.root.exists() else Path.home())).free
    except OSError:
        return ''
    if needed_bytes <= 0:
        return ''
    reserve = int(headroom_gb * 1024 ** 3)
    if needed_bytes + reserve > free:
        return (f'This needs about {needed_bytes / 1024 ** 2:.0f} MB but only '
                f'{free / 1024 ** 2:.0f} MB is free. Clear the cache or free up space first.')
    return ''
