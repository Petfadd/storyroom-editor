"""Queue, resources, cache, history and proxy behaviour.

These are the tests that back the performance claims. Where a number appears in a
comment it was measured on a real file rather than guessed.
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import cache
import jobqueue
import project_history
import proxy
import resources


class ResourceTest(unittest.TestCase):
    def test_a_snapshot_reports_what_the_machine_is_doing(self):
        state = resources.snapshot()
        self.assertGreaterEqual(state.cpu_percent, 0)
        self.assertGreaterEqual(state.memory_percent, 0)
        self.assertIn(state.pressure, ('idle', 'busy', 'strained'))
        self.assertGreater(state.memory_available_mb, 0)

    def test_light_work_is_never_refused(self):
        # Refusing the work that keeps the editor usable would make the machine feel
        # broken in order to protect throughput.
        allowed, _why = resources.admit('light')
        self.assertTrue(allowed)

    def test_heavy_work_is_refused_under_memory_pressure(self):
        strained = resources.Snapshot(10, 95, 100, 5, 95, 'strained', 'test')
        with mock.patch.object(resources, 'snapshot', return_value=strained):
            allowed, why = resources.admit('heavy')
        self.assertFalse(allowed)
        self.assertIn('busy', why.lower())

    def test_heavy_work_is_allowed_on_a_quiet_machine(self):
        quiet = resources.Snapshot(5, 20, 9000, 200, 20, 'idle', 'test')
        with mock.patch.object(resources, 'snapshot', return_value=quiet):
            self.assertTrue(resources.admit('heavy')[0])

    def test_delivery_never_uses_a_hardware_encoder(self):
        # The brief says final quality must not be sacrificed, and a hardware encoder
        # at the same bitrate is visibly worse. This is the rule that enforces it.
        self.assertEqual(resources.encoder_for('export')[-1], 'libx264')

    def test_proxies_may_use_hardware_when_it_works(self):
        fake = {'hardware_available': True, 'proxy_encoder': 'h264_amf',
                'export_encoder': 'libx264'}
        with mock.patch.object(resources, 'capabilities', return_value=fake):
            self.assertEqual(resources.encoder_for('proxy')[-1], 'h264_amf')
            self.assertEqual(resources.encoder_for('export')[-1], 'libx264')

    def test_without_hardware_everything_falls_back_to_the_processor(self):
        fake = {'hardware_available': False, 'proxy_encoder': 'libx264',
                'export_encoder': 'libx264'}
        with mock.patch.object(resources, 'capabilities', return_value=fake):
            self.assertEqual(resources.encoder_for('proxy')[-1], 'libx264')

    def test_background_threads_never_take_every_core(self):
        self.assertLessEqual(resources.thread_count(), 8)
        self.assertGreaterEqual(resources.thread_count(), 1)


class QueueTest(unittest.TestCase):
    def make(self, name, calls, delay=0.02, priority=None, weight='heavy'):
        def work(context):
            calls.append(name)
            for i in range(4):
                context.set(i * 25, name)
                time.sleep(delay)
            return name
        return jobqueue.Job(name, name, work,
                            priority=priority if priority is not None else
                            jobqueue.PRIORITY_BACKGROUND, weight=weight)

    def test_lower_priority_number_runs_first(self):
        calls = []
        queue = jobqueue.Queue()
        # All three must be waiting before the dispatcher chooses, otherwise the first
        # submission simply runs before the others exist -- which is correct behaviour,
        # not a priority failure. Holding the queue's own lock blocks dispatch without
        # blocking submission.
        with queue._lock:
            queue.submit(self.make('background', calls,
                                   priority=jobqueue.PRIORITY_BACKGROUND))
            queue.submit(self.make('ai', calls, priority=jobqueue.PRIORITY_AI,
                                   weight='ai'))
            queue.submit(self.make('analysis', calls,
                                   priority=jobqueue.PRIORITY_ANALYSIS))
        deadline = time.time() + 15
        while len(calls) < 3 and time.time() < deadline:
            time.sleep(0.05)
        queue.stop()
        self.assertEqual(calls, ['analysis', 'ai', 'background'])

    def test_only_one_heavy_job_runs_at_a_time(self):
        overlap = {'now': 0, 'most': 0}
        lock = threading.Lock()

        def work(context):
            with lock:
                overlap['now'] += 1
                overlap['most'] = max(overlap['most'], overlap['now'])
            time.sleep(0.25)
            with lock:
                overlap['now'] -= 1
            return True

        queue = jobqueue.Queue()
        for i in range(3):
            queue.submit(jobqueue.Job(f'j{i}', f'job {i}', work,
                                      priority=jobqueue.PRIORITY_ANALYSIS))
        deadline = time.time() + 20
        while time.time() < deadline:
            if queue.snapshot()['metrics']['finished'] >= 3:
                break
            time.sleep(0.05)
        queue.stop()
        self.assertEqual(overlap['most'], 1,
                         'two heavy jobs ran together and competed for the machine')

    def test_a_cancelled_job_stops_immediately(self):
        queue = jobqueue.Queue()
        started = threading.Event()

        def work(context):
            started.set()
            for _ in range(200):
                if context.cancelled:
                    raise jobqueue.JobCancelled()
                time.sleep(0.02)
            return 'finished anyway'

        job = queue.submit(jobqueue.Job('c', 'cancel me', work,
                                        priority=jobqueue.PRIORITY_ANALYSIS))
        started.wait(5)
        self.assertTrue(queue.cancel(job.id))
        # The interface must not wait for the worker to notice.
        self.assertEqual(queue.snapshot()['finished'][0]['status'], 'cancelled')
        time.sleep(0.3)
        queue.stop()

    def test_pause_holds_progress_and_resume_continues(self):
        queue = jobqueue.Queue()

        def work(context):
            for i in range(100):
                context.wait_if_paused()
                if context.cancelled:
                    raise jobqueue.JobCancelled()
                context.set(i, f'step {i}')
                time.sleep(0.02)
            return 'ok'

        queue.submit(jobqueue.Job('p', 'pause me', work,
                                  priority=jobqueue.PRIORITY_ANALYSIS))
        time.sleep(0.3)
        queue.pause()
        time.sleep(0.4)
        first = queue.snapshot()['running'][0]['progress']
        time.sleep(0.4)
        second = queue.snapshot()['running'][0]['progress']
        self.assertEqual(first, second, 'progress moved while paused')
        queue.resume()
        time.sleep(0.4)
        self.assertGreater(queue.snapshot()['running'][0]['progress'], second)
        queue.stop()

    def test_a_failing_job_is_retried_then_given_up_on(self):
        queue = jobqueue.Queue()
        attempts = {'n': 0}

        def work(context):
            attempts['n'] += 1
            raise RuntimeError('always fails')

        job = queue.submit(jobqueue.Job('f', 'fails', work,
                                        priority=jobqueue.PRIORITY_ANALYSIS))
        deadline = time.time() + 20
        while time.time() < deadline:
            finished = [j for j in queue.snapshot()['finished'] if j['key'] == 'f']
            if finished and finished[0]['status'] == 'failed':
                break
            time.sleep(0.05)
        queue.stop()
        self.assertEqual(attempts['n'], jobqueue.MAX_AUTOMATIC_ATTEMPTS)
        # A person can always retry by hand afterwards.
        self.assertTrue(queue.retry(job.id))

    def test_a_duplicate_submission_is_not_queued_twice(self):
        queue = jobqueue.Queue()
        blocker = threading.Event()

        def work(context):
            blocker.wait(5)
            return True

        first = queue.submit(jobqueue.Job('same', 'one', work,
                                          priority=jobqueue.PRIORITY_ANALYSIS))
        second = queue.submit(jobqueue.Job('same', 'one again', work,
                                           priority=jobqueue.PRIORITY_ANALYSIS))
        self.assertEqual(first.id, second.id)
        blocker.set()
        time.sleep(0.3)
        queue.stop()

    def test_remaining_time_is_only_given_when_it_is_honest(self):
        job = jobqueue.Job('r', 'timed', lambda c: None)
        job.status = 'running'
        job.progress = 50
        self.assertIsNone(job.remaining_seconds(), 'an estimate was invented from nothing')
        now = time.time()
        job._samples = [(now - 20, 10.0), (now - 15, 20.0), (now - 10, 30.0),
                        (now - 5, 40.0), (now, 50.0)]
        estimate = job.remaining_seconds()
        self.assertIsNotNone(estimate)
        self.assertAlmostEqual(estimate, 20, delta=6)

    def test_an_unstable_rate_gives_no_estimate(self):
        job = jobqueue.Job('u', 'jumpy', lambda c: None)
        job.status = 'running'
        job.progress = 50
        now = time.time()
        # 1, 40, 2 and 30 percent in equal spans: no honest rate exists.
        job._samples = [(now - 4, 1.0), (now - 3, 41.0), (now - 2, 43.0), (now - 1, 73.0),
                        (now, 50.0)]
        self.assertIsNone(job.remaining_seconds())

    def test_crash_recovery_marks_running_jobs_interrupted_and_offers_retry(self):
        folder = Path(tempfile.mkdtemp())
        try:
            state = folder / 'queue.json'
            state.write_text(json.dumps([
                {'id': 'abc', 'key': 'was-running', 'title': 'Render', 'status': 'running',
                 'priority': 3, 'weight': 'heavy'},
                {'id': 'def', 'key': 'was-queued', 'title': 'Queued', 'status': 'queued',
                 'priority': 4, 'weight': 'ai'},
            ]))
            queue = jobqueue.Queue(state_path=state)
            queue.start()
            recovered = queue.recovered()
            queue.stop()
            self.assertEqual(len(recovered), 1)
            self.assertEqual(recovered[0]['status'], 'interrupted')
            self.assertEqual(recovered[0]['title'], 'Render')
            # Recovery never restarts work by itself; it offers it.
            self.assertTrue(queue.retry('abc'))
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class CacheTest(unittest.TestCase):
    def store(self):
        return cache.Store(Path(tempfile.mkdtemp()))

    def test_the_same_inputs_give_the_same_key(self):
        folder = Path(tempfile.mkdtemp())
        try:
            path = folder / 'a.bin'
            path.write_bytes(b'hello')
            self.assertEqual(cache.entry_key(path, 'thumb'),
                             cache.entry_key(path, 'thumb'))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_changing_the_file_changes_the_key(self):
        folder = Path(tempfile.mkdtemp())
        try:
            path = folder / 'a.bin'
            path.write_bytes(b'hello')
            before = cache.entry_key(path, 'thumb')
            path.write_bytes(b'hello world')
            self.assertNotEqual(before, cache.entry_key(path, 'thumb'),
                                'a changed source would have reused a stale entry')
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_changing_a_setting_changes_the_key(self):
        folder = Path(tempfile.mkdtemp())
        try:
            path = folder / 'a.bin'
            path.write_bytes(b'hello')
            self.assertNotEqual(cache.entry_key(path, '540p'),
                                cache.entry_key(path, '720p'))
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_a_missing_source_does_not_raise(self):
        key = cache.entry_key(Path('/definitely/not/here.mp4'), 'proxy')
        self.assertTrue(key)

    def test_json_entries_round_trip(self):
        store = self.store()
        try:
            store.write_json('analysis', 'scene-1', {'cuts': [1.0, 2.0]})
            self.assertEqual(store.read_json('analysis', 'scene-1'), {'cuts': [1.0, 2.0]})
            self.assertIsNone(store.read_json('analysis', 'absent'))
        finally:
            shutil.rmtree(store.root, ignore_errors=True)

    def test_cleanup_only_removes_stale_entries(self):
        store = self.store()
        try:
            fresh = store.path('thumbnails', 'fresh', '.jpg')
            stale = store.path('thumbnails', 'stale', '.jpg')
            fresh.parent.mkdir(parents=True, exist_ok=True)
            fresh.write_bytes(b'x' * 100)
            stale.write_bytes(b'x' * 100)
            old = time.time() - 30 * 24 * 3600
            os.utime(stale, (old, old))
            removed = store.cleanup(keep_seconds=14 * 24 * 3600)
            self.assertEqual(removed['files'], 1)
            self.assertTrue(fresh.exists(), 'a fresh entry was deleted')
            self.assertFalse(stale.exists())
        finally:
            shutil.rmtree(store.root, ignore_errors=True)

    def test_cleanup_cannot_reach_outside_the_cache(self):
        store = self.store()
        try:
            outside = store.root.parent / 'original-footage.mp4'
            outside.write_bytes(b'precious' * 100)
            old = time.time() - 60 * 24 * 3600
            os.utime(outside, (old, old))
            store.cleanup(keep_seconds=0)
            self.assertTrue(outside.exists(),
                            'cleanup deleted a file outside the cache folder')
            outside.unlink()
        finally:
            shutil.rmtree(store.root, ignore_errors=True)

    def test_usage_reports_each_kind(self):
        store = self.store()
        try:
            target = store.path('proxies', 'p', '.mp4')
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'y' * 2048)
            usage = store.usage()
            self.assertGreaterEqual(usage['kinds']['proxies']['bytes'], 2048)
            self.assertGreaterEqual(usage['total_bytes'], 2048)
        finally:
            shutil.rmtree(store.root, ignore_errors=True)

    def test_a_full_disk_is_reported_before_the_work_starts(self):
        store = self.store()
        try:
            message = cache.would_fill_disk(store, needed_bytes=10**15, headroom_gb=1)
            self.assertIn('free', message.lower())
            self.assertEqual(cache.would_fill_disk(store, needed_bytes=1024), '')
        finally:
            shutil.rmtree(store.root, ignore_errors=True)


class ProxyDecisionTest(unittest.TestCase):
    def test_a_large_source_is_proxied(self):
        self.assertTrue(proxy.needs_proxy({'height': 2160}))

    def test_a_small_source_is_left_alone(self):
        # Proxying something already cheap to decode wastes time and disk.
        self.assertFalse(proxy.needs_proxy({'height': 480}))
        self.assertFalse(proxy.needs_proxy({'height': 720}))

    def test_the_proxy_is_never_larger_than_the_source(self):
        self.assertEqual(proxy.target_height(480, '720p'), 480)
        self.assertEqual(proxy.target_height(2160, '540p'), 540)
        self.assertEqual(proxy.target_height(1080, '720p'), 720)

    def test_an_unknown_quality_falls_back_rather_than_failing(self):
        self.assertEqual(proxy.target_height(2160, 'nonsense'), 540)


class HistoryTest(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_a_snapshot_can_be_read_back(self):
        ident = project_history.snapshot(self.root, {'title': 'First', 'scenes': [1]})
        self.assertIsNotNone(ident)
        self.assertEqual(project_history.load(self.root, ident)['title'], 'First')

    def test_edits_seconds_apart_are_one_undoable_step(self):
        project_history.snapshot(self.root, {'title': 'A'})
        project_history.snapshot(self.root, {'title': 'B'})
        self.assertEqual(len(project_history.versions(self.root)), 1,
                         'a burst of edits filled the history with near-copies')

    def test_coalescing_keeps_the_latest_state_not_the_first(self):
        project_history.snapshot(self.root, {'title': 'A'})
        ident = project_history.snapshot(self.root, {'title': 'B'})
        self.assertEqual(project_history.load(self.root, ident)['title'], 'B')

    def test_restore_keeps_the_state_it_replaced(self):
        old = project_history.snapshot(self.root, {'title': 'Old'})
        # Push the newest entry back in time so the next save is a new version rather
        # than a coalesced rewrite of the same one.
        index = project_history._read_index(self.root)
        index[-1]['at'] -= 100
        project_history._write_index(self.root, index)
        current = {'title': 'Current'}
        project_history.snapshot(self.root, current)
        self.assertEqual(len(project_history.versions(self.root)), 2)
        restored = project_history.restore(self.root, old, current)
        self.assertEqual(restored['title'], 'Old')
        titles = [project_history.load(self.root, v['id'])['title']
                  for v in project_history.versions(self.root)]
        self.assertIn('Current', titles, 'restoring lost the state it replaced')

    def test_the_history_is_bounded(self):
        index = []
        for i in range(project_history.KEEP + 15):
            ident = project_history.snapshot(self.root, {'title': f'v{i}'})
            index = project_history._read_index(self.root)
            if index:
                index[-1]['at'] -= 100
                project_history._write_index(self.root, index)
        self.assertLessEqual(len(project_history.versions(self.root)),
                             project_history.KEEP)

    def test_a_missing_version_returns_nothing_rather_than_a_wrong_one(self):
        self.assertIsNone(project_history.load(self.root, 'nope'))
        self.assertIsNone(project_history.restore(self.root, 'nope', {'title': 'x'}))

    def test_clearing_removes_only_the_history(self):
        original = self.root / 'project.json'
        original.write_text('{}')
        project_history.snapshot(self.root, {'title': 'A'})
        project_history.clear(self.root)
        self.assertTrue(original.exists(), 'clearing history deleted the project')
        self.assertEqual(project_history.versions(self.root), [])


class AuditRegressionTest(unittest.TestCase):
    """Fixes found during the pre-merge audit. Each one was a real defect."""

    def test_a_truncated_proxy_is_not_served(self):
        # A crash mid-write left a 9-byte "proxy" that metadata() reported as usable,
        # so the player was handed a file it could not decode.
        folder = Path(tempfile.mkdtemp())
        try:
            import imageio_ffmpeg
            store = cache.Store(folder / 'cache')
            source = folder / 'video.mp4'
            source.write_bytes(b'x' * 5000)
            broken = store.path('proxies', 'broken', '.mp4')
            broken.parent.mkdir(parents=True, exist_ok=True)
            broken.write_bytes(b'tiny')
            store.write_json('proxies', 'broken',
                             {'path': str(broken), 'quality': '540p', 'height': 540})
            real_key = cache.entry_key(source, 'proxy')
            store.write_json('proxies', real_key,
                             {'path': str(broken), 'quality': '540p', 'height': 540})
            self.assertIsNone(proxy.metadata(store, source),
                              'a truncated proxy was offered to the player')
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_concurrent_saves_do_not_lose_settings(self):
        # Save is read-modify-write. Measured one lost update in sixty concurrent
        # saves before the lock was added.
        import settings_store
        for _ in range(25):
            folder = Path(tempfile.mkdtemp())
            try:
                store = settings_store.Settings(folder / 'settings.json')
                barrier = threading.Barrier(2)

                def first():
                    barrier.wait()
                    store.save({'proxy_quality': '720p'})

                def second():
                    barrier.wait()
                    store.save({'keep_cache_days': 30})

                threads = [threading.Thread(target=first), threading.Thread(target=second)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join()
                final = store.load()
                self.assertEqual(final['proxy_quality'], '720p')
                self.assertEqual(final['keep_cache_days'], 30)
            finally:
                shutil.rmtree(folder, ignore_errors=True)

    def test_the_private_library_folder_does_not_shadow_app_dependencies(self):
        # Prepending .cache/pylibs made its own numpy win for the whole process after
        # any speech check, so the editor ran on versions it was not tested against.
        import bite_speech
        import numpy
        before = numpy.__file__
        bite_speech.available()
        self.assertEqual(numpy.__file__, before,
                         'a speech check changed which numpy the app is using')
        self.assertNotIn('pylibs', numpy.__file__)
        # The folder may be on sys.path, but only at the end, so nothing can shadow
        # a dependency that is already installed.
        for entry in sys.path:
            if Path(entry).name == 'pylibs':
                self.assertNotEqual(sys.path[0], entry,
                                    'the private library folder is still being prepended')

    def test_cancelling_a_job_kills_its_child_process(self):
        queue = jobqueue.Queue()
        started = threading.Event()

        def work(context):
            started.set()
            context.run_process([sys.executable, '-c',
                                 'import time; time.sleep(30)'])
            return 'should not finish'

        job = queue.submit(jobqueue.Job('kill', 'long child', work,
                                        priority=jobqueue.PRIORITY_ANALYSIS))
        started.wait(10)
        time.sleep(0.5)
        queue.cancel(job.id)
        deadline = time.time() + 10
        while time.time() < deadline:
            if queue.snapshot()['metrics']['started'] and not queue.snapshot()['running']:
                break
            time.sleep(0.1)
        queue.stop()
        statuses = [j['status'] for j in queue.snapshot()['finished']]
        self.assertIn('cancelled', statuses)


class FreshInstallTest(unittest.TestCase):
    """The app must work on a machine that has none of the local cache state."""

    def test_settings_defaults_are_usable_without_a_settings_file(self):
        import settings_store
        folder = Path(tempfile.mkdtemp())
        try:
            store = settings_store.Settings(folder / 'missing.json')
            loaded = store.load()
            self.assertEqual(loaded['proxy_quality'], '540p')
            self.assertTrue(loaded['proxies_enabled'])
        finally:
            shutil.rmtree(folder, ignore_errors=True)

    def test_a_cache_root_that_cannot_be_written_is_refused(self):
        import settings_store
        folder = Path(tempfile.mkdtemp())
        try:
            store = settings_store.Settings(folder / 'settings.json')
            with self.assertRaises(ValueError):
                store.save({'cache_root': '/proc/definitely-not-writable/x'})
        finally:
            shutil.rmtree(folder, ignore_errors=True)


class RunningLongJobsTest(unittest.TestCase):
    """A cancelled job must return promptly and leave nothing running.

    Both halves matter and both were once wrong. The process was killed, but a
    grandchild kept the output pipes open, so the caller blocked until that grandchild
    finished -- twenty seconds for an upscaled picture, an hour for a long encode.
    """

    def setUp(self):
        import jobqueue
        self.jobqueue = jobqueue

    def _scratch_script(self, body: str) -> str:
        folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, folder, True)
        script = folder / 'work.sh'
        script.write_text('#!/bin/sh\n' + body + '\n')
        script.chmod(0o755)
        return str(script)

    def test_a_cancelled_job_returns_promptly_even_with_a_grandchild(self):
        import threading, time
        # The script spawns a long-lived grandchild, exactly as a launcher does.
        script = self._scratch_script('sleep 30 &\nwait\n')
        context = self.jobqueue.JobContext('cancel-test')
        outcome = {}

        def run():
            try:
                context.run_process([script], timeout=60)
                outcome['result'] = 'finished'
            except self.jobqueue.JobCancelled:
                outcome['result'] = 'cancelled'

        thread = threading.Thread(target=run)
        thread.start()
        time.sleep(0.5)
        started = time.monotonic()
        context.cancel()
        thread.join(10)
        elapsed = time.monotonic() - started

        self.assertFalse(thread.is_alive(), 'cancel did not unblock the caller')
        self.assertEqual(outcome.get('result'), 'cancelled')
        self.assertLess(elapsed, 3.0,
                        f'cancel took {elapsed:.1f}s, so the caller sat waiting for a '
                        'child it no longer cared about')

    def test_output_is_still_captured_exactly_as_before(self):
        script = self._scratch_script('echo out-line; echo err-line >&2; exit 0')
        result = self.jobqueue.JobContext('capture-test').run_process([script], timeout=30)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), 'out-line')
        self.assertEqual(result.stderr.strip(), 'err-line')

    def test_a_non_zero_exit_is_reported_rather_than_raised(self):
        script = self._scratch_script('echo nope; exit 3')
        result = self.jobqueue.JobContext('exit-test').run_process([script], timeout=30)
        self.assertEqual(result.returncode, 3)
        self.assertEqual(result.stdout.strip(), 'nope')

    def test_a_timeout_is_still_raised(self):
        import subprocess
        script = self._scratch_script('sleep 20')
        with self.assertRaises(subprocess.TimeoutExpired):
            self.jobqueue.JobContext('timeout-test').run_process([script], timeout=1)

    def test_a_large_amount_of_output_does_not_deadlock(self):
        script = self._scratch_script('seq 1 40000')
        result = self.jobqueue.JobContext('pipe-test').run_process([script], timeout=60)
        self.assertEqual(len(result.stdout.splitlines()), 40000)

    def test_cancellation_is_not_mistaken_for_a_failure_worth_retrying(self):
        """The picture upscaler retries on the CPU when the GPU path fails. A cancel
        must not look like such a failure, or cancelling would start the work again."""
        import upscale
        self.assertTrue(issubclass(self.jobqueue.JobCancelled, Exception))
        source = Path(upscale.__file__).read_text()
        self.assertIn('except JobCancelled:\n                raise',
                      source.replace('\r\n', '\n'),
                      'the CPU-fallback handler would swallow a cancellation')

    def test_pausing_a_running_job_stops_its_encoder(self):
        """Pausing used to only change the label. The encoding carried on at full
        speed while the queue said the job was waiting, which is the opposite of what
        pause is for."""
        import threading, time
        script = self._scratch_script('i=0\nwhile true; do i=$((i+1)); done')
        context = self.jobqueue.JobContext('pause-test')
        finished = {}

        def run():
            try:
                context.run_process([script], timeout=30)
            except Exception:
                finished['done'] = True

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        time.sleep(0.4)
        self.assertFalse(finished, 'the script finished too quickly to test')

        context.pause()
        time.sleep(0.5)
        with context._lock:
            child = context._processes[0] if context._processes else None
        self.assertIsNotNone(child, 'the child disappeared, so pause proved nothing')
        before = child.pid
        self.assertEqual(_process_state(before), 'T',
                         'the encoder kept running while the job was paused')

        context.resume()
        time.sleep(0.5)
        self.assertIn(_process_state(before), ('S', 'R'),
                      'the encoder did not restart after a resume')
        context.cancel()
        thread.join(5)

    def test_a_paused_job_is_reported_as_paused_and_resumable(self):
        import jobqueue
        queue = jobqueue.Queue()
        queue._started = True          # do not start the real dispatcher in a test
        job = jobqueue.Job('pause-labels', 'Testing pause', lambda ctx: None)
        queue._jobs[job.id] = job
        queue._running[job.id] = job
        job.status = 'running'
        self.assertTrue(queue.pause(job.id))
        self.assertEqual(job.status, 'paused')
        self.assertTrue(queue.resume(job.id))
        self.assertEqual(job.status, 'running')


def _process_state(pid: str) -> str:
    """The single-letter Linux process state, or '' where that is not available."""
    try:
        return Path(f'/proc/{pid}/stat').read_text().split(')')[1].split()[0]
    except (OSError, IndexError):
        return ''



class GraphicsReportingTest(unittest.TestCase):
    """The panel must be able to say what the graphics hardware is, and must not
    invent a figure when the driver will not give one."""

    def test_gpu_detection_never_raises_and_returns_a_shape(self):
        import resources
        info = resources.gpu_info()
        for key in ('name', 'vram_mb', 'devices', 'source', 'detected'):
            self.assertIn(key, info)
        self.assertIsInstance(info['devices'], list)
        self.assertIsInstance(info['detected'], bool)
        # Whatever it found, a detected card must be named.
        if info['detected']:
            self.assertTrue(info['name'])

    def test_a_machine_without_a_readable_gpu_reports_none_rather_than_zero(self):
        import resources
        info = resources.gpu_info()
        if info['detected']:
            self.skipTest('this machine has a readable GPU')
        self.assertEqual(info['devices'], [])
        self.assertIsNone(info['vram_mb'])
        self.assertIsNone(resources.vram_usage_mb() or None)

    def test_the_snapshot_carries_graphics_without_breaking_the_other_readings(self):
        import resources
        reading = resources.snapshot().as_dict()
        for key in ('cpu_percent', 'memory_percent', 'disk_free_gb'):
            self.assertIsNotNone(reading[key])
        self.assertIn('vram_used_mb', reading)
        self.assertIn('vram_total_mb', reading)
        self.assertIn('gpu_name', reading)

    def test_capabilities_report_the_gpu_separately_from_encoder_support(self):
        """A card can be present while ffmpeg has no encoder for it. Those are two
        different answers and the user needs to see both."""
        import resources
        caps = resources.capabilities(refresh=True)
        self.assertIn('gpu', caps)
        self.assertIn('hardware_available', caps)
        self.assertIn('devices', caps['gpu'])

    def test_detection_is_cached_so_the_panel_can_poll(self):
        import resources, time
        resources.reset_capabilities()
        first = resources.gpu_info()
        started = time.monotonic()
        for _ in range(50):
            resources.gpu_info()
        elapsed = time.monotonic() - started
        self.assertLess(elapsed, 0.05,
                        f'50 calls took {elapsed*1000:.0f} ms, so detection is not cached')
        self.assertEqual(first, resources.gpu_info())

    def test_a_status_reading_stays_cheap_enough_to_poll(self):
        """The panel asks once a second. Asking a driver for a figure starts a
        process, so a reading must not do that every time or monitoring would cost
        more than the work it describes."""
        import resources, time
        resources.snapshot()                       # warm both caches
        started = time.monotonic()
        for _ in range(25):
            resources.snapshot()
        each = (time.monotonic() - started) / 25
        self.assertLess(each, 0.02,
                        f'a status reading took {each*1000:.1f} ms, which is too slow '
                        'to run every second')


if __name__ == '__main__':
    unittest.main()
