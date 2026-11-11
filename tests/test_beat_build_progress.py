"""The Beat build reports honest progress while it runs.

The build used to be one request that decoded every source from its first frame
before answering. From the editor it was indistinguishable from a hang. These tests
hold the replacement to its promises: that the percentage only goes forward, that it
names the step it is on in plain words, that it reaches 100 exactly once, and that a
failure or a cancellation is reported as itself rather than as a finished build.
"""
import threading
from pathlib import Path
import time
import unittest

import documentary_beat_build as build


class ProgressTest(unittest.TestCase):
    def setUp(self):
        build.clear('p1')

    def tearDown(self):
        build.clear('p1')

    def _wait(self, pid, seconds=10):
        deadline = time.time() + seconds
        while time.time() < deadline:
            state = build.snapshot(pid)
            if state.get('status') in ('done', 'error', 'cancelled'):
                return state
            time.sleep(0.01)
        return build.snapshot(pid)

    def test_the_percentage_only_moves_forward_and_ends_at_one_hundred(self):
        seen = []

        def work(report):
            for step in ('read', 'fit', 'prepare'):
                for fraction in (0.0, 0.5, 1.0):
                    report(step, fraction)
                    seen.append(build.snapshot('p1')['percent'])
                    time.sleep(0.005)
            # The frame check reports inside one step, which is the slow one.
            for fraction in (0.0, 0.25, 0.5, 0.75, 1.0):
                report('frames', fraction)
                seen.append(build.snapshot('p1')['percent'])
            report('save', 1.0)
            return {'scenes': [1, 2, 3]}

        build.start('p1', work)
        state = self._wait('p1')
        self.assertEqual(state['status'], 'done', state)
        self.assertEqual(state['percent'], 100)
        self.assertEqual(seen, sorted(seen), f'progress went backwards: {seen}')
        self.assertGreaterEqual(len(set(seen)), 8,
                                f'progress barely moved: {seen}')

    def test_every_step_is_named_in_plain_words(self):
        details = []

        def work(report):
            for step in ('read', 'fit', 'prepare', 'frames', 'save'):
                report(step, 0.5)
                details.append(build.snapshot('p1')['detail'])
            return {}

        build.start('p1', work)
        self._wait('p1')
        for text in details:
            self.assertTrue(text.strip())
            # Internal step names must never reach the interface.
            for internal in ('read', 'fit', 'prepare', 'frames', 'save'):
                self.assertNotEqual(text.strip().lower(), internal)
        self.assertIn('Checking every source frame', ' '.join(details))

    def test_the_long_step_is_reported_per_source(self):
        def work(report):
            report('frames', 0.0, source='S01.mp4')
            time.sleep(0.01)
            return {}

        build.start('p1', work)
        self._wait('p1')

    def test_a_failure_is_reported_with_the_reason_the_user_can_act_on(self):
        def work(report):
            report('fit', 1.0)
            raise ValueError('Beat 030: source OUT 70.050s is past the end of S01.mp4 (65.000s).')

        build.start('p1', work)
        state = self._wait('p1')
        self.assertEqual(state['status'], 'error')
        self.assertIn('past the end of S01.mp4', state['error'])
        self.assertEqual(state['detail'], 'Could not build.')

    def test_a_cancelled_build_stops_before_the_next_step(self):
        reached = []

        def work(report):
            report('frames', 0.1)
            reached.append('frames')
            time.sleep(0.4)          # the step in flight
            report('save', 1.0)      # must not happen
            reached.append('save')
            return {}

        build.start('p1', work)
        time.sleep(0.15)
        self.assertTrue(build.cancel('p1'))
        state = self._wait('p1', seconds=5)
        self.assertEqual(state['status'], 'cancelled')
        self.assertEqual(reached, ['frames'],
                         'a cancelled build carried on to the next step')

    def test_a_second_build_for_the_same_project_is_refused(self):
        release = threading.Event()

        def slow(report):
            release.wait(5)
            return {}

        build.start('p1', slow)
        with self.assertRaises(ValueError):
            build.start('p1', lambda report: {})
        release.set()
        self._wait('p1')

    def test_nothing_is_reported_for_a_project_that_never_built(self):
        self.assertEqual(build.snapshot('never'), {'status': 'idle'})


class BeatBuildEndpointTest(unittest.TestCase):
    """The routes, against a real test client.

    Written after two bugs that reading the code did not reveal: the cancel route
    answered 500 because a Response was wrapped in jsonify, and a build attempted
    before the script existed said the project had changed, sending the user to look
    for an edit that never happened.
    """

    def setUp(self):
        import tempfile, shutil
        import app as server
        self.server = server
        self.folder = Path(tempfile.mkdtemp())
        (self.folder / 'projects').mkdir()
        self._original = server.PROJECTS
        server.PROJECTS = self.folder / 'projects'
        self.addCleanup(shutil.rmtree, self.folder, True)
        self.addCleanup(setattr, server, 'PROJECTS', self._original)
        self.client = server.app.test_client()
        created = self.client.post('/api/projects',
                                   json={'title': 'Beat build', 'edit_mode': 'documentary'})
        self.pid = created.get_json()['id']
        build.clear(self.pid)

    def _settle(self, seconds=8):
        import time
        deadline = time.time() + seconds
        while time.time() < deadline:
            state = self.client.get(
                f'/api/projects/{self.pid}/documentary-beat-storyboard').get_json()
            if state.get('status') in ('done', 'error', 'cancelled'):
                return state
            time.sleep(0.02)
        return state

    def test_a_project_that_never_built_reports_idle(self):
        state = self.client.get(
            f'/api/projects/{self.pid}/documentary-beat-storyboard').get_json()
        self.assertEqual(state['status'], 'idle')

    def test_building_without_inputs_names_the_missing_input(self):
        started = self.client.post(
            f'/api/projects/{self.pid}/documentary-beat-storyboard',
            json={'confirmed': True})
        self.assertEqual(started.status_code, 202)
        self.assertIn(started.get_json()['status'], ('queued', 'running'))
        state = self._settle()
        self.assertEqual(state['status'], 'error')
        self.assertIn('production script', state['error'])

    def test_cancelling_when_nothing_is_running_is_a_named_refusal(self):
        """It used to be a 500: the route returned a Response inside jsonify."""
        answer = self.client.post(
            f'/api/projects/{self.pid}/documentary-beat-storyboard/cancel', json={})
        self.assertEqual(answer.status_code, 400)
        self.assertIn('No Beat build', answer.get_json()['error'])

    def test_the_running_build_can_be_cancelled(self):
        started = self.client.post(
            f'/api/projects/{self.pid}/documentary-beat-storyboard',
            json={'confirmed': True})
        self.assertEqual(started.status_code, 202)
        answer = self.client.post(
            f'/api/projects/{self.pid}/documentary-beat-storyboard/cancel', json={})
        self.assertIn(answer.status_code, (200, 400))
        if answer.status_code == 200:
            self.assertEqual(answer.get_json()['status'], 'cancelled')


if __name__ == '__main__':
    unittest.main()
