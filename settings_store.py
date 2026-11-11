"""Machine-wide settings: where caches live and how proxies are made.

These are properties of the computer rather than of one production, so they live beside
the projects folder rather than inside a project. A cache location chosen once applies
to everything, which is what makes "put the cache on the SSD" a useful instruction
instead of something to repeat for every new edit.

Nothing here is required for the app to run. Every value has a working default, and an
unreadable or malformed settings file falls back to those defaults rather than stopping
the server from starting.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import threading

import cache
import proxy
import resources

# One lock per settings file, so two objects that point at the same file cannot race.
_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock_for(path: Path) -> threading.RLock:
    key = str(path)
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.RLock()
        return _LOCKS[key]

DEFAULTS = {
    'cache_root': '',            # empty means "use the recommended location"
    'proxy_quality': proxy.DEFAULT_QUALITY,
    'proxies_enabled': True,
    'auto_cleanup': True,
    'keep_cache_days': 14,
    'max_preview_height': 720,   # what the monitor is allowed to decode
}


class Settings:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._data = dict(DEFAULTS)
        # Save is read-modify-write, so two settings changed at the same moment would
        # otherwise lose one of them: measured at one lost update in sixty concurrent
        # saves before this lock was added. One lock per file, not per object, because
        # more than one Settings instance can point at the same file.
        self._lock = _lock_for(Path(path).resolve())

    def load(self) -> dict:
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> dict:
        try:
            raw = json.loads(self.path.read_text())
            if isinstance(raw, dict):
                for key, default in DEFAULTS.items():
                    value = raw.get(key, default)
                    # Guard each field separately: one bad value must not discard the
                    # rest of the user's settings.
                    if key == 'proxy_quality' and value not in proxy.QUALITY_HEIGHTS:
                        value = default
                    if key == 'max_preview_height' and value not in (480, 540, 720, 1080):
                        value = default
                    if key == 'keep_cache_days':
                        try:
                            value = max(1, min(365, int(value)))
                        except (TypeError, ValueError):
                            value = default
                    if key in ('proxies_enabled', 'auto_cleanup'):
                        value = bool(value)
                    self._data[key] = value
        except (OSError, ValueError):
            pass
        return dict(self._data)

    def save(self, changes: dict) -> dict:
        with self._lock:
            return self._save_unlocked(changes)

    def _save_unlocked(self, changes: dict) -> dict:
        self._load_unlocked()
        for key in DEFAULTS:
            if key not in changes:
                continue
            value = changes[key]
            if key == 'proxy_quality':
                if value not in proxy.QUALITY_HEIGHTS:
                    raise ValueError('Choose 540p or 720p for the editing copies.')
                self._data[key] = value
            elif key == 'max_preview_height':
                if value not in (480, 540, 720, 1080):
                    raise ValueError('Choose a preview height of 480, 540, 720 or 1080.')
                self._data[key] = value
            elif key == 'keep_cache_days':
                self._data[key] = max(1, min(365, int(value)))
            elif key == 'cache_root':
                self._data[key] = self._validate_cache_root(str(value or ''))
            elif key in ('proxies_enabled', 'auto_cleanup'):
                self._data[key] = bool(value)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(self._data, indent=2))
        temp.replace(self.path)
        return dict(self._data)

    @staticmethod
    def _validate_cache_root(value: str) -> str:
        """A cache location must be a folder we can actually write to.

        Checked before it is stored, because discovering on the next upload that the
        chosen drive is full, missing or read-only is a much worse time to find out.
        """
        if not value:
            return ''
        target = Path(value).expanduser()
        try:
            target.mkdir(parents=True, exist_ok=True)
            probe_file = target / '.storyroom-write-test'
            probe_file.write_text('ok')
            probe_file.unlink(missing_ok=True)
        except OSError as error:
            raise ValueError(f'That folder cannot be used for the cache: {error}')
        return str(target)

    # -- derived ----------------------------------------------------------
    def cache_store(self) -> tuple[cache.Store, str]:
        root, detail = resources.suitable_cache_root(
            Path(self._data['cache_root']) if self._data['cache_root'] else None)
        return cache.Store(root), detail

    def as_dict(self) -> dict:
        store, detail = self.cache_store()
        data = dict(self._data)
        data['cache_root_resolved'] = str(store.root)
        data['cache_detail'] = detail
        data['proxy_quality_options'] = list(proxy.QUALITY_HEIGHTS)
        return data


def disk_report(store: cache.Store) -> dict:
    """Free space where caches live, in the terms the warnings are phrased in."""
    report = {'root': str(store.root)}
    try:
        usage = shutil.disk_usage(str(store.root if store.root.exists() else Path.home()))
        report.update({'free_gb': round(usage.free / 1024 ** 3, 1),
                       'total_gb': round(usage.total / 1024 ** 3, 1)})
    except OSError:
        report.update({'free_gb': None, 'total_gb': None})
    return report
