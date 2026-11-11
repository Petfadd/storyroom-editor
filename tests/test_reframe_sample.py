import unittest

import reframe_analyse as A


class SampleTimesTest(unittest.TestCase):
    """Sampling is what keeps detection off most frames, so it has to be right."""

    def test_a_short_clip_is_looked_at_about_once_a_second(self):
        times = A.sample_times([0.0], 4.0)
        self.assertLessEqual(len(times), 7, 'too many detector passes for 4 seconds')
        self.assertLessEqual(times[0], 0.001)
        self.assertGreater(times[-1], 3.5)

    def test_every_scene_boundary_is_sampled(self):
        times = A.sample_times([0.0, 1.0, 2.0, 3.0], 4.0)
        for boundary in (0.0, 1.0, 2.0, 3.0):
            self.assertTrue(any(abs(t - boundary) < 1e-6 for t in times),
                            f'boundary {boundary} was not sampled')

    def test_the_cost_stays_proportional_to_length_not_frames(self):
        # 30 seconds at 30fps is 900 frames; sampling must not be anywhere near that.
        times = A.sample_times([0.0], 30.0)
        self.assertLess(len(times), 45)
        self.assertGreater(len(times), 20)

    def test_times_are_ordered_and_unique(self):
        times = A.sample_times([0.0, 2.0, 5.0], 8.0)
        self.assertEqual(times, sorted(set(times)))

    def test_a_zero_length_clip_still_yields_one_sample(self):
        self.assertEqual(A.sample_times([0.0], 0.0), [0.0])

    def test_a_sliver_of_a_scene_at_the_end_is_dropped(self):
        # Under the minimum scene length, a cut is more likely noise than a scene.
        times = A.sample_times([0.0, 3.9], 4.0)
        self.assertFalse(any(3.85 < t < 3.9 for t in times),
                         'a 0.1s sliver was treated as its own scene')


class KeepMeaningfulTest(unittest.TestCase):
    def test_short_scenes_are_merged_away(self):
        kept = A._keep_meaningful([0.0, 0.2, 2.0, 2.3])
        self.assertEqual(kept, [0.0, 2.0])

    def test_an_empty_or_single_list_is_safe(self):
        self.assertEqual(A._keep_meaningful([]), [0.0])
        self.assertEqual(A._keep_meaningful([0.0]), [0.0])


class SceneIsolationOnRealFootageTest(unittest.TestCase):
    """The rule that matters most: never carry a crop across a scene change."""

    def test_scenes_with_a_subject_in_different_places_get_different_frames(self):
        # Reproduces the measured four-scene clip: the subject stands left, then right,
        # then centre-left, then right again. Each scene must be framed on its own.
        import reframe_track
        from reframe_geometry import Box

        samples = []
        for index, x in enumerate((300, 1500, 700, 1200)):
            for offset in (0.0, 0.5):
                samples.append((index + offset, [Box(x, 260, 230, 620, subject=0)]))
        cuts = [1.0, 2.0, 3.0]
        groups = reframe_track.group_by_scene(samples, cuts)
        self.assertEqual(len(groups), 4)
        lefts = [reframe_track.plan_scene(g, i, 608, 1920).keyframes[0].left
                 for i, g in enumerate(groups)]
        self.assertEqual(len(set(round(x) for x in lefts)), 4,
                         'two scenes were given the same framing')
        self.assertLess(lefts[0], 300)
        self.assertGreater(lefts[1], 900)


if __name__ == '__main__':
    unittest.main()
