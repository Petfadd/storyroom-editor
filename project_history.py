"""Project history: a safe copy of the project file after every save.

The save itself was already atomic -- written to a temporary name and renamed over the
original -- so a crash during a write could not leave a half-written project. What was
missing was any way back from a save that turned out to be wrong: a bad bulk edit, a
mistaken approval, or simply closing the tab after deleting something.

This keeps the last N versions of each project in the project's own folder. It is small
-- project.json is text, and a hundred edits of a normal production is a few megabytes --
and it is deliberately dumb: whole files, not diffs, so recovering never depends on
replaying a sequence correctly.

Nothing here is load-bearing. If the history folder cannot be written, saving the
project still succeeds and the failure is only that undo is unavailable.
"""
from __future__ import annotations

import json
import shutil
import time
import uuid
from pathlib import Path

# How many versions to keep. Enough to undo a session's worth of mistakes without the
# folder growing without limit.
KEEP = 40

# Two saves within this window are treated as one edit, so dragging a slider does not
# fill the history with forty near-identical copies.
COALESCE_SECONDS = 20


def history_dir(project_root: Path) -> Path:
    return Path(project_root) / '.history'


def _index_path(project_root: Path) -> Path:
    return history_dir(project_root) / 'index.json'


def _read_index(project_root: Path) -> list[dict]:
    try:
        data = json.loads(_index_path(project_root).read_text())
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def _write_index(project_root: Path, entries: list[dict]) -> None:
    target = _index_path(project_root)
    temp = target.with_suffix('.tmp')
    temp.write_text(json.dumps(entries, indent=1))
    temp.replace(target)


def snapshot(project_root: Path, project: dict, label: str = '') -> str | None:
    """Keep a copy of this project state. Returns its id, or None if it was skipped.

    Called after a successful save, so the copy is always a state that the app agreed
    was valid rather than something half-formed.
    """
    root = Path(project_root)
    try:
        folder = history_dir(root)
        folder.mkdir(parents=True, exist_ok=True)
        entries = _read_index(root)
        now = time.time()

        # Coalesce rapid edits: rewrite the newest version in place instead of adding
        # one, so a burst of slider changes is one undoable step rather than forty.
        #
        # Rewriting the same file, not saving into a new one -- an earlier version of
        # this overwrote the previous entry's file while the index claimed two versions
        # existed, so the older state was destroyed by the edit that was meant to sit on
        # top of it.
        if entries and now - float(entries[-1].get('at') or 0) < COALESCE_SECONDS:
            latest = entries[-1]
            latest['at'] = now
            latest['label'] = label or latest.get('label', '')
            latest['scenes'] = len(project.get('scenes') or [])
            latest['assets'] = len(project.get('assets') or [])
            _write_atomic(folder / latest['file'], project)
            _write_index(root, entries)
            return latest['id']

        # The id must be unique even when two saves land in the same millisecond.
        # A plain millisecond stamp collided in testing and the second save silently
        # overwrote the first, destroying the version it was meant to sit beside.
        ident = f'{int(now*1000)}-{uuid.uuid4().hex[:6]}'
        name = f'{ident}.json'
        _write_atomic(folder / name, project)
        entries.append({'id': ident, 'at': now, 'file': name, 'label': label[:120],
                        'scenes': len(project.get('scenes') or []),
                        'assets': len(project.get('assets') or [])})
        # Trim the oldest beyond the limit.
        while len(entries) > KEEP:
            old = entries.pop(0)
            (folder / old['file']).unlink(missing_ok=True)
        _write_index(root, entries)
        return ident
    except (OSError, ValueError, TypeError):
        return None


def _write_atomic(target: Path, project: dict) -> None:
    temp = target.with_name(target.name + '.tmp')
    temp.write_text(json.dumps(project, indent=1))
    temp.replace(target)


def versions(project_root: Path) -> list[dict]:
    """Recorded versions, newest first."""
    entries = _read_index(Path(project_root))
    return sorted(entries, key=lambda e: e.get('at', 0), reverse=True)


def load(project_root: Path, ident: str) -> dict | None:
    """One recorded version, or None when it is gone."""
    root = Path(project_root)
    for entry in _read_index(root):
        if entry.get('id') == ident:
            try:
                return json.loads((history_dir(root) / entry['file']).read_text())
            except (OSError, ValueError):
                return None
    return None


def restore(project_root: Path, ident: str, current: dict) -> dict | None:
    """Return the project state to switch to, keeping the current one in history.

    The state being replaced is saved first, so an undo is itself undoable and there is
    no way to restore a version and lose the work that was on screen at the time.
    """
    root = Path(project_root)
    state = load(root, ident)
    if state is None:
        return None
    snapshot(root, current, label='Before restore')
    return state


def clear(project_root: Path) -> int:
    root = Path(project_root)
    folder = history_dir(root).resolve()
    try:
        if not folder.is_relative_to(root.resolve()):
            return 0
    except (OSError, ValueError):
        return 0
    if not folder.is_dir():
        return 0
    count = len(_read_index(root))
    shutil.rmtree(folder, ignore_errors=True)
    return count


def size_bytes(project_root: Path) -> int:
    folder = history_dir(Path(project_root))
    total = 0
    if folder.is_dir():
        for item in folder.rglob('*'):
            try:
                if item.is_file():
                    total += item.stat().st_size
            except OSError:
                continue
    return total
