import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from documentary_beat_plan import fit, overlaps, consecutive_bites, proposal
from documentary import build_timeline


def beat(number, kind, start, end, narration=(0, 0), name='S01.mp4'):
    row = {'number': number, 'label': f'{number:03d}', 'kind': kind, 'source_file': name,
           'source_in_ms': start, 'source_out_ms': end, 'caption': 'None', 'direction': ''}
    if kind == 'narration':
        row.update(narration_in_ms=narration[0], narration_out_ms=narration[1])
    else:
        row.update(pause_ms=narration[0], duration_ms=end-start)
    return row


ASSETS = [{'id': 'a1', 'original_name': 'S01.mp4', 'kind': 'video', 'duration': 71.8, 'path': 'sources/a.mp4'},
          {'id': 'a2', 'original_name': 'S03.mp4', 'kind': 'video', 'duration': 831.83, 'path': 'sources/b.mp4'}]


class FitTest(unittest.TestCase):
    def test_range_past_the_file_is_clamped_and_the_shortfall_is_reported(self):
        beats = [beat(1, 'narration', 36000, 115300, (0, 79300))]
        adjusted, changes, issues = fit(beats, ASSETS)
        self.assertEqual(len(issues), 1)
        self.assertIn('only has 71.800s', issues[0])
        self.assertTrue(any('past the end of S01.mp4' in c for c in changes))

    def test_a_range_that_fits_is_trimmed_or_extended_to_the_narration(self):
        beats = [beat(1, 'narration', 10000, 20000, (0, 15000))]
        adjusted, changes, issues = fit(beats, ASSETS)
        self.assertEqual(issues, [])
        self.assertEqual((adjusted[0]['source_in_ms'], adjusted[0]['source_out_ms']), (10000, 25000))
        self.assertTrue(any('shorter than the 15.000s narration' in c for c in changes))

    def test_a_range_that_cannot_fit_is_reported_not_invented(self):
        beats = [beat(1, 'narration', 30000, 65000, (0, 79300))]
        adjusted, changes, issues = fit(beats, ASSETS)
        self.assertTrue(issues and 'cannot cover' in issues[0])
        # The scripted values are left alone so nothing is silently shortened.
        self.assertEqual((adjusted[0]['source_in_ms'], adjusted[0]['source_out_ms']), (30000, 65000))

    def test_an_explicit_choice_repoints_only_that_beat(self):
        beats = [beat(1, 'narration', 36000, 115300, (0, 79300)), beat(2, 'narration', 0, 2000, (0, 2000))]
        adjusted, changes, issues = fit(beats, ASSETS, focus_beat='001', focus_source='S03.mp4')
        self.assertEqual(issues, [])
        self.assertEqual(adjusted[0]['source_file'], 'S03.mp4')
        self.assertEqual((adjusted[0]['source_in_ms'], adjusted[0]['source_out_ms']), (0, 79300))
        self.assertEqual(adjusted[1]['source_file'], 'S01.mp4')
        self.assertTrue(any('re-pointed to your chosen S03.mp4' in c for c in changes))

    def test_a_choice_that_is_too_short_is_still_refused(self):
        beats = [beat(1, 'narration', 0, 2000, (0, 79300))]
        adjusted, changes, issues = fit(beats, ASSETS, focus_beat='001', focus_source='S01.mp4')
        self.assertTrue(issues and 'too short' in issues[0])

    def test_shorter_picture_is_extended_inside_the_same_file(self):
        beats = [beat(1, 'narration', 100000, 117000, (0, 63767), name='S03.mp4')]
        adjusted, changes, issues = fit(beats, ASSETS)
        self.assertEqual(issues, [])
        # Forward first, because the rest of the file can carry it.
        self.assertEqual((adjusted[0]['source_in_ms'], adjusted[0]['source_out_ms']), (100000, 163767))
        self.assertTrue(any('extended forward' in c for c in changes))

    def test_repeats_and_back_to_back_bites_are_surfaced(self):
        beats = [beat(1, 'narration', 0, 4000, (0, 4000)),
                 beat(2, 'sound_bite', 1000, 3000, (4000,)),
                 beat(3, 'sound_bite', 5000, 7000, (4000,))]
        self.assertEqual(consecutive_bites(beats), [[2, 3]])
        self.assertEqual(overlaps(beats), [[1, 2]])
        report = proposal({'assets': ASSETS, 'voiceover': {'duration': 4}}, beats)
        self.assertEqual(report['repeated_ranges'], [[1, 2]])
        self.assertEqual(report['planned_ms'], 4000 + 2000 + 2000)


class SoundBiteFitTest(unittest.TestCase):
    """A bite past the end of its file used to survive fitting untouched."""

    def test_a_bite_past_the_end_is_clamped_and_says_the_real_new_length(self):
        beats = [beat(1, 'sound_bite', 70000, 75000, (0,))]
        adjusted, changes, issues = fit(beats, ASSETS)
        self.assertEqual(issues, [])
        self.assertEqual((adjusted[0]['source_in_ms'], adjusted[0]['source_out_ms']), (70000, 71800))
        self.assertEqual(adjusted[0]['duration_ms'], 1800)
        self.assertIn('plays 1.800s instead of 5.000s', changes[0])
        self.assertNotIn('5.000s instead of 5.000s', changes[0])

    def test_a_bite_with_no_room_left_blocks_and_names_the_unused_stretch(self):
        beats = [beat(1, 'sound_bite', 75000, 80000, (0,))]
        adjusted, changes, issues = fit(beats, ASSETS)
        self.assertEqual(len(issues), 1)
        self.assertIn('only 0.000s left after source IN 75.000s', issues[0])
        self.assertIn('cannot cover this 5.000s bite', issues[0])
        self.assertIn('unused stretch of this upload is 0.000–71.800s', issues[0])
        # The scripted value is left exactly as written.
        self.assertEqual((adjusted[0]['source_in_ms'], adjusted[0]['source_out_ms']), (75000, 80000))

    def test_a_fully_used_file_says_a_longer_source_is_needed(self):
        beats = [beat(1, 'narration', 0, 71800, (0, 71800))]
        adjusted, changes, issues = fit(beats + [beat(2, 'sound_bite', 75000, 80000, (0,))], ASSETS)
        self.assertIn('needs a longer source file or one more video', ' '.join(issues))

    def test_replaying_the_same_frames_is_reported_not_offered_for_approval(self):
        beats = [beat(1, 'narration', 5000, 7000, (0, 2000)),
                 beat(2, 'narration', 5000, 7000, (2000, 4000))]
        report = proposal({'assets': ASSETS, 'voiceover': {'duration': 70}}, beats)
        self.assertIn('exactly the same frames', ' '.join(report['issues']))
        self.assertNotIn([1, 2], report['repeated_ranges'])
        self.assertNotIn([1, 2], report['consecutive_bites'])

    def test_a_real_partial_overlap_is_still_a_choice(self):
        beats = [beat(1, 'narration', 0, 4000, (0, 4000)),
                 beat(2, 'sound_bite', 1000, 3000, (4000,))]
        report = proposal({'assets': ASSETS, 'voiceover': {'duration': 4}}, beats)
        self.assertEqual(report['repeated_ranges'], [[1, 2]])
        self.assertFalse(any('exactly the same frames' in x for x in report['issues']))


class BackToBackTimelineTest(unittest.TestCase):
    def test_two_bites_at_one_pause_play_in_order(self):
        project = {'voiceover': {'duration': 10}}
        plan = {'revision': 1, 'bites': [
            {'id': 'b1', 'pause_s': 4, 'source_in_s': 0, 'source_out_s': 2, 'created': 1,
             'asset_id': 'a1', 'asset_name': 'S01.mp4'},
            {'id': 'b2', 'pause_s': 4, 'source_in_s': 5, 'source_out_s': 8, 'created': 2,
             'asset_id': 'a1', 'asset_name': 'S01.mp4'}]}
        timeline = build_timeline(project, plan)
        kinds = [(s['kind'], s['timeline_in_s'], s['timeline_out_s']) for s in timeline['segments']]
        self.assertEqual(kinds, [('narration', 0, 4), ('sound_bite', 4, 6), ('sound_bite', 6, 9), ('narration', 9, 15)])
        self.assertEqual(timeline['duration_s'], 15)


if __name__ == '__main__':
    unittest.main()


class FrameConversionTest(unittest.TestCase):
    """A 59.94 fps source in a 29.97 fps documentary keeps every other frame."""

    def _event(self, label, start, end, si, so, rate='382365000/6379123'):
        return {'label': label, 'kind': 'narration', 'asset_id': 'a1', 'source': 'S05.mp4',
                'source_start_frame': si, 'source_end_frame': so, 'source_fps': rate,
                'output_fps': '30000/1001', 'frame_conversion': 'halve' if rate != '30000/1001' else 'none',
                'timeline_in_ms': start, 'timeline_out_ms': end, 'caption': '', 'picture_scale': 100}

    @patch('documentary_frame_audit.add_part')
    def test_output_rate_is_taken_from_the_slot_not_the_source(self, add_part):
        """Mixed rates must not be reported when the sources convert exactly."""
        from fractions import Fraction
        from documentary_render_plan import plan
        events = [self._event('a', 0, 1000, 0, 60), self._event('b', 1000, 2000, 0, 30, rate='30000/1001')]
        with patch('documentary_render_plan.playback_schedule', return_value=events):
            # No files exist, so no frame count is available and no correction is made.
            result = plan({'assets': []}, '.', {})
        self.assertEqual(result['output_fps'], '30000/1001')
        self.assertNotIn('different frame rates', ' '.join(result['errors']))
        self.assertTrue(result['picture_compatible'], result['errors'])

    def test_odd_frame_count_rounds_up_to_the_same_output_duration(self):
        """59 source frames at 59.94 fps is 30 output frames at 29.97, not 29.5."""
        from documentary_render_plan import plan
        events = [self._event('a', 0, 1000, 0, 59)]
        with patch('documentary_render_plan.playback_schedule', return_value=events):
            result = plan({'assets': []}, '.', {})
        self.assertTrue(result['picture_compatible'], result['errors'])
        self.assertEqual(result['spans'][0]['source_frame_count'], 30)
        self.assertEqual(result['spans'][0]['output_frame_count'], 30)

    def test_a_real_frame_difference_still_blocks(self):
        """One source frame per output frame is still enforced when no conversion applies."""
        from documentary_render_plan import plan
        events = [self._event('a', 0, 1000, 0, 30, rate='30000/1001')]
        with patch('documentary_render_plan.playback_schedule', return_value=events):
            result = plan({'assets': []}, '.', {})
        events = [self._event('a', 0, 2000, 0, 30, rate='30000/1001')]
        with patch('documentary_render_plan.playback_schedule', return_value=events):
            result = plan({'assets': []}, '.', {})
        self.assertFalse(result['picture_compatible'])
        self.assertIn('No frames were added, dropped or repeated', ' '.join(result['errors']))


class NarrationGapTest(unittest.TestCase):
    """The build needs one explicit range per hole; it used to be left blank."""

    def test_an_uncovered_narration_hole_gets_a_proposed_range(self):
        # Beat 001 covers 0-4s; the narration runs to 9s, and 20-25s of the file is unused.
        beats = [beat(1, 'narration', 0, 4000, (0, 4000))]
        report = proposal({'assets': ASSETS, 'voiceover': {'duration': 9}}, beats)
        self.assertEqual(report['issues'], [])
        self.assertEqual(len(report['narration_gaps']), 1)
        gap = report['narration_gaps'][0]
        self.assertEqual((gap['narration_in_ms'], gap['narration_out_ms']), (4000, 9000))
        self.assertEqual(gap['source_out_ms'] - gap['source_in_ms'], 5000)
        self.assertGreaterEqual(gap['source_in_ms'], 4000)

    def test_no_spare_footage_is_reported_not_invented(self):
        beats = [beat(1, 'narration', 0, 4000, (0, 4000))]
        report = proposal({'assets': ASSETS, 'voiceover': {'duration': 80}}, beats)
        self.assertEqual(report['narration_gaps'], [])
        self.assertIn('Nothing was invented', ' '.join(report['issues']))
        self.assertIn('no unused 76.000s stretch', ' '.join(report['issues']))
