"""Build a documentary Beat timeline as a job that reports its own progress.

The build used to be one request that held the connection while it did several
expensive things in a row: reading and fitting the Beats, preparing the plan, and
then a full decode of every source video from its first frame to prove the frames
exist where the plan says they do. On a real production that last part is the slow
one -- it decodes up to the last required frame of each file -- and from the outside
the editor simply appeared to be doing nothing, with a disabled button as the only
hint that anything had been asked for.

The work is the same. What changed is that it now happens on a worker thread and
publishes a step, a percentage and a sentence naming what it is doing, so the dialog
can show that it is alive and roughly how far along it is.

The percentages here are weighted by how long each step is understood to take on a
real project, not by counting steps. A build that reported "step 2 of 5" would sit at
40 percent through most of the work and then jump to 100, which tells the user less
than nothing. Each source's frame check is itself reported by the inspector, so the
longest step also moves smoothly rather than in one jump.
"""
from __future__ import annotations

import copy
import threading
import time
import traceback

GUARD = threading.RLock()
JOBS: dict[str, dict] = {}

# How much of the build each step is assumed to take. These are proportions of total
# work, measured against a real production with three sources: reading the Beats is
# nearly free, the plan itself is cheap, and proving the frames exist dominates.
WEIGHTS = {'read': 0.06, 'fit': 0.12, 'prepare': 0.14, 'frames': 0.63, 'save': 0.05}

# The frame check is the long one, and it reports per source. These are the sentences
# shown while it runs; the step's share is divided between the sources it has to check.
STEP_TEXT = {
    'read': 'Reading the scripted Beats',
    'fit': 'Fitting each Beat inside its file',
    'prepare': 'Building the review plan',
    'frames': 'Checking every source frame the plan uses',
    'save': 'Saving the storyboard',
}


class BuildCancelled(Exception):
    """Raised when the user closes the progress dialog and asks to stop."""


def _percent(done: float) -> int:
    return max(1, min(99, int(round(done * 100))))


def start(pid: str, work) -> dict:
    """Run `work(report)` on a thread and return the job record immediately.

    `work` is given a `report(step, fraction=None, detail=None, source=None)` callable.
    Only one build per project may run: a second one would work from a project the
    first is about to replace, and whichever finished last would win.
    """
    with GUARD:
        existing = JOBS.get(pid)
        if existing and existing['status'] in ('queued', 'running'):
            raise ValueError('A Beat timeline is already being built for this production.')
        job = {'status': 'queued', 'step': 'read', 'percent': 0,
               'detail': STEP_TEXT['read'], 'result': None, 'error': '',
               'started': time.time(), 'cancel': False}
        JOBS[pid] = job

    def report(step: str, fraction: float | None = None, detail: str | None = None,
               source: str | None = None) -> None:
        with GUARD:
            if job['status'] == 'cancelled':
                raise BuildCancelled()
            if step not in WEIGHTS:
                return
            order = list(WEIGHTS)
            done = sum(WEIGHTS[s] for s in order[:order.index(step)])
            share = WEIGHTS[step]
            moved = share * (0.0 if fraction is None else max(0.0, min(1.0, fraction)))
            job['step'] = step
            job['percent'] = _percent(done + moved)
            if detail:
                job['detail'] = detail
            elif step == 'frames':
                # The slow step names the file it is on, because that is the only way
                # the number means anything while it is running.
                job['detail'] = (f'{STEP_TEXT[step]} · {source}' if source
                                 else STEP_TEXT[step])
            else:
                job['detail'] = STEP_TEXT[step]

    def run() -> None:
        with GUARD:
            if job['status'] == 'cancelled':
                return
            job['status'] = 'running'
        try:
            result = work(report)
            with GUARD:
                if job['status'] == 'cancelled':
                    return
                job.update(status='done', percent=100, detail='Storyboard built.',
                           result=result)
        except BuildCancelled:
            with GUARD:
                job.update(status='cancelled', detail='Cancelled.')
        except (ValueError, OSError, KeyError) as error:
            # These are the failures the user can act on -- a missing file, a range
            # past the end, a plan that changed underneath. The message is the point.
            with GUARD:
                job.update(status='error', error=str(error), detail='Could not build.')
        except Exception as error:                    # pragma: no cover
            traceback.print_exc()
            with GUARD:
                job.update(status='error', error=str(error) or error.__class__.__name__,
                           detail='Could not build.')

    # Snapshot *before* the thread starts. Taking it afterwards raced the worker: a
    # build that fails instantly (a missing script or SRT) could already be in its
    # error state when the caller read the answer, so the same POST returned either
    # "queued" or "error" depending on thread timing. Both are true statements, but a
    # response that changes run to run is not something a caller can rely on.
    started = snapshot(pid)
    threading.Thread(target=run, daemon=True, name=f'beat-build-{pid}').start()
    return started


def snapshot(pid: str) -> dict:
    with GUARD:
        job = JOBS.get(pid)
        if not job:
            return {'status': 'idle'}
        return {k: v for k, v in job.items() if k != 'result'}


def result(pid: str):
    with GUARD:
        job = JOBS.get(pid)
        return None if not job else job.get('result')


def cancel(pid: str) -> bool:
    """Ask the build to stop. The step in flight finishes; the next one does not start."""
    with GUARD:
        job = JOBS.get(pid)
        if not job or job['status'] not in ('queued', 'running'):
            return False
        job['status'] = 'cancelled'
        job['detail'] = 'Stopping after the current step…'
        return True


def clear(pid: str) -> None:
    with GUARD:
        JOBS.pop(pid, None)
