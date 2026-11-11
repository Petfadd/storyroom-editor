"""Background analysis, so a long clip never blocks the editor.

Analysis is only about a third of realtime, but a forty minute interview still takes
over ten minutes, and a request that long would time out. So it runs in a worker thread
and the editor polls, exactly like the preview and export jobs already do.

One analysis per production at a time. A second request while one is running is refused
rather than queued, because the user is waiting on the result and two competing runs
would both be slower.
"""
from __future__ import annotations

import threading
import time
import traceback
from pathlib import Path

GUARD = threading.Lock()
JOBS: dict[str, dict] = {}
_PENDING: set[str] = set()


def _set(pid: str, **values) -> None:
    with GUARD:
        job = JOBS.get(pid)
        if job is None:
            return
        job.update(values)
        job['updated_at'] = time.time()


def snapshot(pid: str) -> dict:
    with GUARD:
        job = JOBS.get(pid)
        return dict(job) if job else {'status': 'idle', 'progress': 0, 'detail': 'Not analysed.'}


def running(pid: str) -> bool:
    with GUARD:
        job = JOBS.get(pid)
        return bool(job and job.get('status') in ('queued', 'running'))


def start(pid: str, source: Path, out_width: int, out_height: int, mode: str,
          save, on_done=None) -> dict:
    """Analyse `source` in the background. `save(plan_dict)` persists the result."""
    with GUARD:
        if pid in _PENDING:
            raise ValueError('Analysis is already running for this production.')
        _PENDING.add(pid)
        JOBS[pid] = {'status': 'queued', 'progress': 0, 'detail': 'Queued',
                     'started_at': time.time(), 'updated_at': time.time(),
                     'source': str(source), 'mode': mode}

    def work(report=None):
        try:
            import reframe_analyse
            _set(pid, status='running', detail='Looking for people and scene changes',
                 progress=1)
            def on_sample(done, total):
                percent=max(1, int(done * 100 / max(1, total)))
                _set(pid, progress=percent, detail=f'Analysed {done} of {total} samples')
                if report is not None:report.set(percent, f'Analysed {done} of {total} samples')

            result = reframe_analyse.analyse(
                Path(source), out_width, out_height, mode=mode, progress=on_sample)
            payload = result.plan.as_dict()
            payload['notes'] = result.notes
            save(payload)
            _set(pid, status='done', progress=100,
                 detail=f'Framing ready for {len(payload.get("scenes") or [])} scene'
                        f'{"s" if len(payload.get("scenes") or []) != 1 else ""}.',
                 scenes=len(payload.get('scenes') or []), detector=result.detector)
            if on_done:
                on_done(payload)
        except Exception as error:                        # pragma: no cover
            traceback.print_exc()
            _set(pid, status='failed', progress=0,
                 detail=f'Analysis failed: {error}')
        finally:
            with GUARD:
                _PENDING.discard(pid)

    # Submit into the shared queue rather than a private thread. Analysis is heavy and
    # used to run alongside renders; now it waits its turn at priority 3.
    import jobqueue
    def queued(context):
        try:
            return work(report=context)
        finally:
            pass

    jobqueue.QUEUE.submit(jobqueue.Job(
        f'reframe-{pid}', 'Framing analysis', queued,
        priority=jobqueue.PRIORITY_ANALYSIS, weight='heavy', project=pid,
        meta={'mode': mode}))
    return snapshot(pid)


def forget(pid: str) -> None:
    with GUARD:
        JOBS.pop(pid, None)
        _PENDING.discard(pid)
