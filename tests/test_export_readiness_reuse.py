"""Export readiness must not re-litigate decisions the Beat plan already recorded.

Two real defects motivated these: readiness ignored the saved approved-overlap
list and reported 209 false overlaps, and the capacity check summed slot lengths
instead of measuring distinct coverage, so reused footage was counted against
itself and a fitting plan was reported as a shortage.
"""
import unittest

import app
from automatic import distinct_covered, source_capacity_errors


class ApprovedReuseTest(unittest.TestCase):
    def test_saved_approvals_are_read_back(self):
        p = {'documentary_beat_review': {'approved_overlaps': [[1, 4], [2, 5]]}}
        self.assertEqual(app.beat_approvals(p)[0], {(1, 4), (2, 5)})

    def test_pair_order_does_not_matter(self):
        p = {'documentary_beat_review': {'approved_overlaps': [[4, 1]]}}
        self.assertIn((1, 4), app.beat_approvals(p)[0])

    def test_a_project_without_records_approves_nothing(self):
        self.assertEqual(app.beat_approvals({})[0], set())

    def test_malformed_records_are_ignored_not_crashed(self):
        p = {'documentary_beat_review': {'approved_overlaps': [[1], 'x', None, [2, 3]]}}
        self.assertEqual(app.beat_approvals(p)[0], {(2, 3)})

    def test_beat_label_is_read_from_the_scene(self):
        self.assertEqual(app.scene_beat({'plan_row': {'continuity': 'Documentary beat 004'}}), 4)
        self.assertIsNone(app.scene_beat({'plan_row': {}}))

    def test_an_unapproved_pair_is_not_approved(self):
        p = {'documentary_beat_review': {'approved_overlaps': [[1, 4]]}}
        self.assertNotIn((3, 9), app.beat_approvals(p)[0])


class DistinctCoverageTest(unittest.TestCase):
    def test_reuse_is_counted_once(self):
        self.assertEqual(distinct_covered([(0, 10), (5, 15), (20, 30)]), 25.0)

    def test_contiguous_spans_join(self):
        self.assertEqual(distinct_covered([(0, 5), (5, 10)]), 10.0)

    def test_empty_is_zero(self):
        self.assertEqual(distinct_covered([]), 0.0)


class CapacityTest(unittest.TestCase):
    def _project(self, editor_frames, duration=10.0, scenes=1):
        return {'assets': [{'id': 'a', 'kind': 'video', 'name': 'X.mp4', 'duration': duration}],
                'scenes': [{'asset_id': 'a', 'in_s': 0, 'frame_in': 0, 'frame_out': editor_frames,
                            'transition': 'cut'} for _ in range(scenes)]}

    def test_a_plan_that_fits_is_not_an_error(self):
        self.assertEqual(source_capacity_errors(self._project(300)), [])

    def test_running_past_the_file_still_fails(self):
        errors = source_capacity_errors(self._project(360))
        self.assertEqual(len(errors), 1)
        self.assertIn('past the end', errors[0])

    def test_reusing_the_same_interval_is_not_a_shortage(self):
        # Two 5s beats over the same 5s of a 10s file: 10s of slots, 5s distinct.
        self.assertEqual(source_capacity_errors(self._project(150, scenes=2)), [])

    def test_reuse_that_covers_more_than_the_file_still_fails(self):
        # Two 8s beats over distinct-looking starts still cannot come from 10s.
        p = self._project(240, duration=10.0, scenes=1)
        p['scenes'].append({'asset_id': 'a', 'in_s': 8, 'frame_in': 0, 'frame_out': 90,
                            'transition': 'cut'})
        self.assertEqual(len(source_capacity_errors(p)), 1)


if __name__ == '__main__':
    unittest.main()
