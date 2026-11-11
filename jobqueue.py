"""One queue for every heavy operation.

Before this module each feature started its own thread: the automatic assembler, the
preview, the scene exports, the analyser, the documentary renderer and the final render
could all run at once, on the same machine, competing for the same cores and the same
memory. Nothing coordinated them, so a three-minute preview could stall because a
background analysis had already taken every core.

This replaces that with one dispatcher. Jobs declare a priority and a weight, and the
dispatcher decides what may run, how many things may run, and when to hold back.

The priorities are the user's, in the user's order:

    1  playback and editing responsiveness   -- never queued, never delayed
    2  essential media loading               -- a proxy or a poster needed to edit
    3  background media analysis             -- reframing, scene detection, speech
    4  AI image and video enhancement        -- the most expensive work
    5  thumbnails and other background tasks

Nothing at priority 1 or 2 is ever admitted through this queue: those are request-time
work whose whole purpose is to keep the interface responsive. The queue exists to
restrain everything else.

Design rules that matter:

* Only one heavy job runs at a time by default. Two renders on a four-core laptop is
  slower than the same two renders one after another, because they contend for memory
  bandwidth and the pagefile rather than sharing it usefully.
* An AI job waits for a free AI slot, which is capped at one. Loading two sets of model
  weights into 16 GB while an editor is open is how a machine starts swapping.
* A job may be paused, resumed, cancelled and retried. Cancelling a running job asks it
  to stop and marks it cancelled immediately, so the interface does not wait for the
  worker to notice.
* Interrupted jobs survive a restart: state is written to disk, and on startup a job
  that was running is marked interrupted and offered for retry rather than silently
  vanishing.
"""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import resources

# The user's priority order, made numeric. Lower runs first.
PRIORITY_EDITING = 1
PRIORITY_MEDIA = 2
PRIORITY_ANALYSIS = 3
PRIORITY_AI = 4
PRIORITY_BACKGROUND = 5

# How many of each kind may run at once. Heavy and AI are single-slot on purpose: they
# are the operations that saturate a machine, and running two of them never finishes
# both sooner on 16 GB.
SLOTS = {'heavy': 1, 'ai': 1, 'light': 2}

# How long the dispatcher will wait for a higher-priority job to become startable
# before giving up and running what it can. Short enough to be invisible, long enough
# to cover a set of jobs submitted in one action.
PRIORITY_SETTLE_SECONDS = 0.35

# Jobs that have not been cancelled are retried this many times automatically, then
# have to be retried by hand. A third automatic attempt at the same failing input is
# just a slower way of showing the same error.
MAX_AUTOMATIC_ATTEMPTS = 2


class Job:
    """One unit of queued work."""

    def __init__(self, key: str, title: str, fn, *, priority: int = PRIORITY_BACKGROUND,
                 weight: str = 'heavy', run=None, on_done=None, on_fail=None,
                 project: str = '', meta: dict | None = None):
        self.id = uuid.uuid4().hex[:12]
        self.key = key                  # stable identity, used to avoid duplicates
        self.title = title
        self.fn = fn                    # fn(context) -> result
        self.run = run                  # optional fn(context), for a job that must run now
        self.priority = priority
        self.weight = weight
        self.on_done = on_done
        self.on_fail = on_fail
        self.project = project
        self.meta = dict(meta or {})
        self.status = 'queued'          # queued|running|done|failed|cancelled|interrupted|paused
        self.progress = 0
        self.detail = ''
        self.error = ''
        self.result = None
        self.attempts = 0
        self.created = time.time()
        self.started = None
        self.finished = None
        # Timing samples, so remaining time is only ever shown when it is honest.
        self._samples: list[tuple[float, float]] = []
        self._context = JobContext(self)

    # -- reporting, called from inside fn ---------------------------------
    def update(self, progress: float | None = None, detail: str | None = None) -> None:
        self._context.set(progress, detail)

    def cancelled(self) -> bool:
        return self._context.cancelled

    def wait_if_paused(self) -> None:
        self._context.wait_if_paused()

    # -- state ------------------------------------------------------------
    def remaining_seconds(self) -> float | None:
        """Estimated time left, or None when no honest estimate is possible.

        A percentage alone says nothing: progress often runs in steps, and the first
        ten percent of an encode is not the first ten percent of the job's duration. The
        estimate is only given once progress has been observed rising for long enough to
        fit a rate, and only when the recent rate is stable.
        """
        if self.status not in ('running', 'paused') or self.progress <= 1:
            return None
        usable = [(t, p) for t, p in self._samples if p > 0]
        if len(usable) < 4:
            return None
        first_t, first_p = usable[0]
        last_t, last_p = usable[-1]
        elapsed = last_t - first_t
        gained = last_p - first_p
        if elapsed < 3.0 or gained <= 1:
            return None
        rate = gained / elapsed                  # percent per second
        if rate <= 0:
            return None
        remaining = (100 - last_p) / rate
        # A wildly unstable rate means the estimate would be fiction. Measured against
        # the mean rather than the max, so two rates of 2 and 30 percent per second are
        # rejected: that is a fifteen-fold disagreement, not noise.
        recent = usable[-4:]
        spans = [(b[0] - a[0], b[1] - a[1]) for a, b in zip(recent, recent[1:])]
        rates = [g / s for s, g in spans if s > 0 and g > 0]
        if len(rates) >= 2:
            mean = sum(rates) / len(rates)
            if mean <= 0:
                return None
            spread = (max(rates) - min(rates)) / mean
            if spread > 1.0:
                return None
        return max(0.0, remaining)

    def as_dict(self) -> dict:
        remaining = self.remaining_seconds()
        return {'id': self.id, 'key': self.key, 'title': self.title,
                'priority': self.priority, 'weight': self.weight,
                'status': self.status, 'progress': round(self.progress, 1),
                'detail': self.detail, 'error': self.error,
                'attempts': self.attempts, 'project': self.project,
                'remaining_seconds': None if remaining is None else round(remaining),
                'created': self.created, 'started': self.started,
                'finished': self.finished, 'meta': self.meta}


class JobContext:
    """What a running job is given: a way to report, and a way to be stopped."""

    def __init__(self, job: Job):
        self.job = job
        self.cancelled = False
        self._paused = threading.Event()
        self._paused.set()            # set means "not paused"
        self._lock = threading.Lock()

    def set(self, progress: float | None = None, detail: str | None = None) -> None:
        with self._lock:
            if progress is not None:
                value = max(0.0, min(100.0, float(progress)))
                if value > self.job.progress:
                    self.job.progress = value
                self.job._samples.append((time.time(), self.job.progress))
                # Keep the sample list short; only recent rate is meaningful.
                if len(self.job._samples) > 40:
                    self.job._samples = self.job._samples[-40:]
            if detail is not None:
                self.job.detail = str(detail)[:300]

    def wait_if_paused(self) -> None:
        """Block here while paused. Long jobs call this between batches."""
        self._paused.wait()

    def cancel(self) -> None:
        self.cancelled = True
        self._paused.set()            # a cancelled job must not stay blocked on pause

    def pause(self) -> None:
        self._paused.clear()

    def resume(self) -> None:
        self._paused.set()


class Queue:
    """The dispatcher. One instance for the whole application."""

    def __init__(self, state_path: Path | None = None):
        self._lock = threading.RLock()
        self._jobs: dict[str, Job] = {}
        self._running: dict[str, Job] = {}
        self._state_path = state_path
        self._wake = threading.Condition(self._lock)
        self._paused_all = False
        self._stopping = False
        self._started = False
        self._history: list[str] = []
        self._priority_wait: float | None = None
        self.metrics = {'started': 0, 'finished': 0, 'failed': 0, 'cancelled': 0}

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._restore()
        thread = threading.Thread(target=self._dispatch, daemon=True, name='job-dispatcher')
        thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stopping = True
            self._wake.notify_all()

    # -- submitting -------------------------------------------------------
    def submit(self, job: Job) -> Job:
        """Add a job. A duplicate of a queued or running job is returned, not added."""
        self.start()
        with self._lock:
            for existing in self._jobs.values():
                if (existing.key == job.key
                        and existing.status in ('queued', 'running', 'paused')):
                    return existing
            self._jobs[job.id] = job
            self._priority_wait = None          # a new arrival may outrank a candidate
            self._persist()
            self._wake.notify_all()
            return job

    def find(self, key: str) -> Job | None:
        with self._lock:
            for job in self._jobs.values():
                if job.key == key and job.status in ('queued', 'running', 'paused'):
                    return job
            return None

    # -- control ----------------------------------------------------------
    def cancel(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return False
            if job.status in ('done', 'failed', 'cancelled'):
                return False
            # Mark it at once, so the interface never waits for the worker to notice.
            job.status = 'cancelled'
            job.detail = 'Cancelled'
            job.finished = time.time()
            job._context.cancel()
            self.metrics['cancelled'] += 1
            self._persist()
            self._wake.notify_all()
            return True

    def retry(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ('failed', 'cancelled', 'interrupted'):
                return False
            job.status = 'queued'
            job.error = ''
            job.detail = 'Waiting to retry'
            job.progress = 0.0
            job._samples = []
            job.attempts = 0
            job.finished = None
            job._context = JobContext(job)
            self._persist()
            self._wake.notify_all()
            return True

    def pause(self, job_id: str | None = None) -> bool:
        """Pause one job, or the whole queue when no id is given."""
        with self._lock:
            if job_id is None:
                self._paused_all = True
                for job in self._running.values():
                    job._context.pause()
                    job.status = 'paused'
                self._persist()
                return True
            job = self._jobs.get(job_id)
            if job is None or job.status != 'running':
                return False
            job._context.pause()
            job.status = 'paused'
            self._persist()
            return True

    def resume(self, job_id: str | None = None) -> bool:
        with self._lock:
            if job_id is None:
                self._paused_all = False
                for job in self._running.values():
                    job._context.resume()
                    job.status = 'running'
                self._persist()
                self._wake.notify_all()
                return True
            job = self._jobs.get(job_id)
            if job is None or job.status != 'paused':
                return False
            job._context.resume()
            job.status = 'running'
            self._persist()
            return True

    def clear_finished(self) -> int:
        with self._lock:
            removed = [jid for jid, job in self._jobs.items()
                       if job.status in ('done', 'cancelled')]
            for jid in removed:
                self._jobs.pop(jid, None)
            self._persist()
            return len(removed)

    # -- reading ----------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            jobs = sorted(self._jobs.values(),
                          key=lambda j: (j.status not in ('running', 'paused'),
                                         j.priority, j.created))
            active = [j for j in jobs if j.status in ('running', 'paused')]
            waiting = [j for j in jobs if j.status == 'queued']
            return {
                'paused': self._paused_all,
                'running': [j.as_dict() for j in active],
                'queued': [j.as_dict() for j in waiting],
                'finished': [j.as_dict() for j in jobs
                             if j.status in ('done', 'failed', 'cancelled', 'interrupted')][:25],
                'counts': {'running': len(active), 'queued': len(waiting)},
                'metrics': dict(self.metrics),
                'system': resources.snapshot().as_dict(),
            }

    # -- the dispatcher ---------------------------------------------------
    def _dispatch(self) -> None:
        while True:
            with self._lock:
                if self._stopping:
                    return
                if self._paused_all:
                    self._wake.wait(timeout=2.0)
                    continue
                candidate = self._next_runnable()
                if candidate is None:
                    self._wake.wait(timeout=2.0)
                    continue
                # A set of jobs submitted together must be ordered by priority, not by
                # whichever one the dispatcher happened to see first. If anything is
                # still queued that outranks this candidate and could run once a slot
                # frees, wait a moment for it rather than starting the wrong job.
                # The timer stops that from deadlocking when the higher-priority job is
                # itself blocked on something that will not change on its own.
                if self._outranked(candidate):
                    if self._priority_wait is None:
                        self._priority_wait = time.time()
                    elif time.time() - self._priority_wait < PRIORITY_SETTLE_SECONDS:
                        self._wake.wait(timeout=0.05)
                        continue
                    else:
                        self._priority_wait = None
                else:
                    self._priority_wait = None
                candidate.status = 'running'
                candidate.started = candidate.started or time.time()
                candidate.attempts += 1
                candidate._samples = [(time.time(), 0.0)]
                self._running[candidate.id] = candidate
                self.metrics['started'] += 1
                self._persist()
            threading.Thread(target=self._execute, args=(candidate,), daemon=True,
                             name=f'job-{candidate.id}').start()

    def _next_runnable(self) -> Job | None:
        """The highest-priority queued job that this machine can take right now."""
        used: dict[str, int] = {}
        for job in self._running.values():
            if job.status in ('running', 'paused'):
                used[job.weight] = used.get(job.weight, 0) + 1
        queued = sorted((j for j in self._jobs.values() if j.status == 'queued'),
                        key=lambda j: (j.priority, j.created))
        for job in queued:
            if used.get(job.weight, 0) >= SLOTS.get(job.weight, 1):
                continue
            allowed, _why = resources.admit(job.weight)
            if not allowed:
                continue
            # Priority is absolute across weights, not within them. Weights have
            # separate slots, so without this check a low-priority background job could
            # start during the moment a higher-priority AI job was still waiting for its
            # own slot, which inverts the order the user asked for.
            blocked = [other for other in queued
                       if other.weight != job.weight
                       and other.priority < job.priority
                       and used.get(other.weight, 0) < SLOTS.get(other.weight, 1)]
            if blocked:
                continue
            return job
        return None


    def _outranked(self, candidate: Job) -> bool:
        """Is something still queued that should go first?

        Only jobs that could actually start are considered: one waiting for its own
        weight's slot, or one held back by machine pressure, must not block the queue.
        """
        used: dict[str, int] = {}
        for job in self._running.values():
            if job.status in ('running', 'paused'):
                used[job.weight] = used.get(job.weight, 0) + 1
        for other in self._jobs.values():
            if other.status != 'queued' or other.priority >= candidate.priority:
                continue
            if used.get(other.weight, 0) >= SLOTS.get(other.weight, 1):
                continue
            if not resources.admit(other.weight)[0]:
                continue
            return True
        return False

    def _execute(self, job: Job) -> None:
        try:
            result = job.fn(job._context)
            if job._context.cancelled:
                outcome = 'cancelled'
            else:
                outcome = 'done'
                job.result = result
                job.progress = 100.0
        except JobCancelled:
            outcome = 'cancelled'
        except Exception as error:
            traceback.print_exc()
            outcome = 'failed'
            job.error = str(error)[:500] or error.__class__.__name__
            job.detail = 'Failed'
        with self._lock:
            self._running.pop(job.id, None)
            if job.status != 'cancelled':
                job.status = outcome
            job.finished = time.time()
            if outcome == 'done':
                self.metrics['finished'] += 1
            elif outcome == 'failed':
                self.metrics['failed'] += 1
                if job.attempts < MAX_AUTOMATIC_ATTEMPTS and not job._context.cancelled:
                    job.status = 'queued'
                    job.detail = 'Retrying after a failure'
                    job.progress = 0.0
                    job._samples = []
            self._persist()
            self._wake.notify_all()
        if outcome == 'done' and job.on_done:
            try:
                job.on_done(job.result)
            except Exception:
                traceback.print_exc()
        elif outcome == 'failed' and job.on_fail:
            try:
                job.on_fail(job.error)
            except Exception:
                traceback.print_exc()

    # -- persistence, for crash recovery ----------------------------------
    def _persist(self) -> None:
        if self._state_path is None:
            return
        try:
            data = [job.as_dict() for job in self._jobs.values()
                    if job.status in ('queued', 'running', 'paused', 'interrupted')]
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temp = self._state_path.with_suffix('.tmp')
            temp.write_text(json.dumps(data, indent=1))
            temp.replace(self._state_path)
        except OSError:
            pass

    def _restore(self) -> None:
        """Bring back what was in flight when the process stopped.

        Nothing is restarted automatically. A job that was running when the machine
        crashed is marked interrupted and shown to the user with a retry button, because
        silently restarting an unknown amount of work after a crash is how a machine
        gets stuck in a loop it cannot get out of.
        """
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text())
        except (OSError, ValueError):
            return
        for raw in data if isinstance(data, list) else []:
            if raw.get('status') in ('running', 'paused'):
                raw['status'] = 'interrupted'
            else:
                continue
            self._jobs[raw['id']] = _RecoveredJob(raw)
        self._persist()

    def recovered(self) -> list[dict]:
        with self._lock:
            return [job.as_dict() for job in self._jobs.values()
                    if job.status == 'interrupted']


class JobCancelled(Exception):
    """Raised by a job when it notices it has been cancelled."""


class _RecoveredJob(Job):
    """A job read back from disk. It has no function, only a name to offer retry on."""

    def __init__(self, raw: dict):
        super().__init__(raw.get('key', ''), raw.get('title', 'Interrupted job'), None,
                         priority=raw.get('priority', PRIORITY_BACKGROUND),
                         weight=raw.get('weight', 'heavy'),
                         project=raw.get('project', ''), meta=raw.get('meta') or {})
        self.id = raw.get('id') or self.id
        self.status = 'interrupted'
        self.progress = 0.0
        self.detail = 'Stopped when the app closed'


# One queue for the application. Imported by every module that starts heavy work.
QUEUE = Queue()
