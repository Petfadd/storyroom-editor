import unittest

import reframe_plan as P
import reframe_track as T
from reframe_geometry import Box


def walker(count=4, step=520, width=230):
    """One person walking left to right, sampled once per second."""
    return [(float(i), [Box(60 + i * step, 260, width, 600, subject=0)]) for i in range(count)]


class WindowStaysInBoundsTest(unittest.TestCase):
    """Each of these was a real bug, so each gets a test."""

    def test_subject_at_the_left_edge_never_makes_the_window_negative(self):
        # place_window alone returns -129 here. An unclamped left pushed the source
        # right inside the overlay window, leaving a black gap and sliding the subject
        # out of frame: a measured render lost it for 75 of 120 frames.
        samples = [(float(i), [Box(60, 260, 230, 600, subject=0)]) for i in range(3)]
        result = T.plan_scene(samples, 0, 608, 1920)
        for key in result.keyframes:
            self.assertGreaterEqual(key.left, 0)
            self.assertLessEqual(key.left + key.width, 1920 + 0.001)

    def test_subject_at_the_right_edge_never_runs_off(self):
        samples = [(float(i), [Box(1650, 260, 230, 600, subject=0)]) for i in range(3)]
        result = T.plan_scene(samples, 0, 608, 1920)
        for key in result.keyframes:
            self.assertLessEqual(key.left + key.width, 1920 + 0.001)

    def test_a_long_crossing_keeps_the_subject_visible_everywhere(self):
        result = T.plan_scene(walker(count=8, step=230), 0, 608, 1920)
        for key in result.keyframes:
            self.assertGreaterEqual(key.left, 0, 'camera left the source')
            self.assertLessEqual(key.left + key.width, 1920 + 0.001)


class SmoothingTest(unittest.TestCase):
    def test_smoothing_does_not_lag_the_subject(self):
        # A causal filter trailed by more than 500px at one sample per second. The
        # camera must sit where the subject actually is, not where it was.
        samples = walker(count=4, step=520)
        result = T.plan_scene(samples, 0, 608, 1920)
        subject_centre = samples[-1][1][0].cx
        last_key = result.keyframes[-1]
        # The subject ends nearer the right edge than a 608px window can centre, so the
        # target is the best position geometry allows, not a perfect centring.
        best_possible = min(max(subject_centre - 608 / 2, 0.0), 1920 - 608)
        self.assertAlmostEqual(last_key.left, best_possible, delta=60,
                               msg='the frame is chasing the subject from behind')

    def test_no_frame_moves_faster_than_the_pan_limit(self):
        samples = walker(count=5, step=470)
        result = T.plan_scene(samples, 0, 608, 1920)
        self.assertGreater(result.pan_per_second, 0)
        for a, b in zip(result.keyframes, result.keyframes[1:]):
            limit = result.pan_per_second * (b.t - a.t) + 0.001
            self.assertLessEqual(abs(b.left - a.left), limit)

    def test_the_pan_limit_never_falls_below_the_subject_speed(self):
        # A cap slower than the subject makes following impossible; this is the guard.
        samples = walker(count=5, step=470)
        result = T.plan_scene(samples, 0, 608, 1920)
        self.assertGreaterEqual(result.pan_per_second, 470 * 0.99)

    def test_a_still_subject_gets_one_fixed_position(self):
        jitter = [0, 4, -6, 2, -3, 5, 1]
        samples = [(float(i), [Box(800 + d, 200, 220, 700, subject=0)])
                   for i, d in enumerate(jitter)]
        result = T.plan_scene(samples, 0, 608, 1920)
        self.assertTrue(result.still)
        self.assertEqual(len({round(k.left, 6) for k in result.keyframes}), 1)

    def test_a_moving_subject_is_not_declared_still(self):
        result = T.plan_scene(walker(count=6), 0, 608, 1920)
        self.assertFalse(result.still)
        self.assertGreater(len({round(k.left, 3) for k in result.keyframes}), 1)


class SubjectChoiceTest(unittest.TestCase):
    def test_the_persistent_person_beats_a_brief_passer_by(self):
        samples = []
        for i in range(6):
            boxes = [Box(300, 200, 260, 700, kind='body', subject=0)]
            if i == 2:                     # someone walks through once
                boxes.append(Box(1500, 300, 120, 350, kind='body', subject=1))
            samples.append((float(i), boxes))
        ranked = T.rank_subjects(samples, 1920)
        self.assertEqual(ranked[0], 0, 'the passer-by was chosen over the main subject')

    def test_a_larger_face_wins_a_tie(self):
        samples = [(0.0, [Box(200, 200, 120, 120, kind='face', subject=0),
                          Box(900, 200, 300, 300, kind='face', subject=1)]),
                   (1.0, [Box(200, 200, 120, 120, kind='face', subject=0),
                          Box(900, 200, 300, 300, kind='face', subject=1)])]
        self.assertEqual(T.rank_subjects(samples, 1920)[0], 1)

    def test_follow_override_is_respected(self):
        samples = []
        for i in range(4):
            samples.append((float(i), [Box(200, 200, 240, 640, subject=0),
                                       Box(1400, 200, 240, 640, subject=1)]))
        result = T.plan_scene(samples, 0, 608, 1920, follow=1)
        # The note reaches the screen, so it must be words rather than an internal id.
        self.assertIn('following the main person', result.notes)
        self.assertFalse(any('subject 1' in n for n in result.notes))
        self.assertTrue(all(k.left > 900 for k in result.keyframes),
                        'the frame ignored the requested subject')

    def test_the_second_person_is_offered_not_discarded(self):
        samples = []
        for i in range(4):
            samples.append((float(i), [Box(200, 200, 240, 640, subject=0),
                                       Box(1500, 200, 240, 640, subject=1)]))
        result = T.plan_scene(samples, 0, 608, 1920)
        self.assertIsNotNone(result.alternative)
        self.assertEqual(result.alternative['subject'], 1)
        self.assertFalse(result.alternative['fits'],
                         'two people 1000px apart cannot fit a 608px window')
        self.assertEqual(result.alternative['suggest'], 'blurred',
                         'a second composition must be offered, not an arbitrary crop')
        self.assertTrue(result.notes)

    def test_no_subject_detected_holds_the_middle_without_crashing(self):
        result = T.plan_scene([(0.0, []), (1.0, [])], 0, 608, 1920)
        self.assertEqual(result.keyframes[0].left, (1920 - 608) / 2)
        self.assertTrue(any('middle' in n for n in result.notes))


class SceneIsolationTest(unittest.TestCase):
    def test_samples_split_at_a_boundary(self):
        samples = [(t, []) for t in (0.0, 0.5, 2.0, 2.5)]
        groups = T.group_by_scene(samples, [1.8])
        self.assertEqual(len(groups), 2)
        self.assertEqual([t for t, _ in groups[0]], [0.0, 0.5])
        self.assertEqual([t for t, _ in groups[1]], [2.0, 2.5])

    def test_no_boundaries_means_one_scene(self):
        self.assertEqual(len(T.group_by_scene([(0.0, [])], [])), 1)

    def test_each_scene_is_framed_from_its_own_content(self):
        scene_a = T.plan_scene([(0.0, [Box(100, 200, 240, 640, subject=0)])], 0, 608, 1920)
        scene_b = T.plan_scene([(0.0, [Box(1600, 200, 240, 640, subject=0)])], 1, 608, 1920)
        self.assertLess(scene_a.keyframes[0].left, 300)
        self.assertGreater(scene_b.keyframes[0].left, 900,
                           "the second scene inherited the first scene's crop")


class ExpressionTest(unittest.TestCase):
    def test_a_static_path_has_no_ramps(self):
        self.assertEqual(P.ramp_expression([0.0, 5.0], [100.0, 100.0]), '100.0000')

    def test_ramps_are_clamped_piecewise(self):
        expr = P.ramp_expression([0.0, 1.0], [0.0, 10.0])
        self.assertIn('clip((t-0.0000)/1.0000,0,1)', expr)
        self.assertIn('10.0000', expr)

    def test_zero_length_segments_are_skipped(self):
        expr = P.ramp_expression([0.0, 0.0, 1.0], [5.0, 9.0, 20.0])
        self.assertNotIn('nan', expr.lower())
        self.assertNotIn('/0.0000', expr)

    def test_a_static_scene_still_covers_its_whole_duration(self):
        # Without a trailing keyframe the crop snaps back to its default mid-scene.
        scene = P.ScenePlan(index=0, start=0.0, duration=3.0,
                            keyframes=[P.Keyframe(0.0, 100.0, 608)])
        self.assertEqual(scene.expression(), '100.0000')

    def test_a_path_that_starts_late_is_anchored_to_zero(self):
        scene = P.ScenePlan(index=0, start=0.0, duration=4.0,
                            keyframes=[P.Keyframe(1.0, 200.0, 608)])
        self.assertEqual(scene.expression(), '200.0000')

    def test_an_empty_scene_does_not_raise(self):
        scene = P.ScenePlan(index=0, start=0.0, duration=2.0, keyframes=[])
        self.assertIsInstance(scene.expression(), str)

    def test_mismatched_lengths_are_refused(self):
        with self.assertRaises(ValueError):
            P.ramp_expression([0.0, 1.0], [0.0])


class PlanSerialisationTest(unittest.TestCase):
    def build(self):
        result = T.plan_scene(walker(), 0, 608, 1920)
        return P.plan_from_scenes([result], 'smart_crop', 1080, 1920, 1920, 1080, 'opencv')

    def test_round_trip_preserves_keyframes(self):
        plan = self.build()
        again = P.ReframePlan.from_dict(plan.as_dict())
        self.assertEqual(len(again.scenes), len(plan.scenes))
        self.assertEqual([k.as_dict() for k in again.scenes[0].keyframes],
                         [k.as_dict() for k in plan.scenes[0].keyframes])

    def test_round_trip_preserves_manual_edits(self):
        plan = self.build()
        plan.scenes[0].manual = True
        plan.scenes[0].locked = True
        again = P.ReframePlan.from_dict(plan.as_dict())
        self.assertTrue(again.scenes[0].manual)
        self.assertTrue(again.scenes[0].locked)

    def test_a_corrupt_keyframe_is_skipped_not_fatal(self):
        data = {'mode': 'smart_crop', 'width': 1080, 'height': 1920,
                'scenes': [{'scene': 0, 'duration': 2.0,
                            'keyframes': [{'t': 'x'}, {'t': 0.0, 'x': 10.0, 'w': 608}]}]}
        plan = P.ReframePlan.from_dict(data)
        self.assertEqual(len(plan.scenes[0].keyframes), 1)

    def test_an_empty_payload_is_survivable(self):
        plan = P.ReframePlan.from_dict({})
        self.assertEqual(plan.mode, 'smart_crop')
        self.assertFalse(plan.as_dict()['usable'])


if __name__ == '__main__':
    unittest.main()
