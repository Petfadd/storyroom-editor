import copy
import unittest
from fractions import Fraction
from unittest.mock import patch
from tests.test_documentary_storyboard import StoryboardPreparationTest
from documentary_storyboard import prepare
from documentary_frame_audit import audit, playback_schedule


class FrameAuditTest(StoryboardPreparationTest):
    def prepared(self):
        return prepare(self.p, self.root, [], [], False, lambda _: True)

    @patch('documentary_frame_audit.inspect_video')
    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30000,1001))
    def test_original_ranges_are_snapped_and_verified_without_changes(self, rate, inspect):
        p, plan = self.prepared(); before = copy.deepcopy((p, plan))
        result = audit(p, self.root, plan)
        self.assertTrue(result['verified'], result['errors'])
        # Four slots: narration before the bite, the bite itself, narration after it,
        # and the closing narration Beat. A bite is picture as well as sound.
        self.assertEqual(len(result['parts']), 4)
        self.assertEqual(result['parts'][0]['source_start_frame'], 0)
        self.assertEqual(result['parts'][0]['source_end_frame'], 60)
        self.assertEqual((p, plan), before)
        inspect.assert_called_once()

    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30))
    def test_changed_bite_and_scene_fail_closed(self, rate):
        p, plan = self.prepared()
        plan['bites'][0]['source_in_s'] += 1
        self.assertFalse(audit(p, self.root, plan)['verified'])
        p, plan = self.prepared(); p['scenes'][0]['in_s'] += 1
        self.assertFalse(audit(p, self.root, plan)['verified'])

    @patch('documentary_frame_audit.inspect_video')
    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30))
    def test_editing_the_script_does_not_block_playback(self, rate, inspect):
        """A script edited after the build must still play, with a warning naming it.

        Measured on a real production: 29 of 30 beats were edited after the build, so
        the audit refused every one of the 151 picture slots and the monitor had no
        schedule to show. One storyboard could only ever serve the script it was born
        from, which is the opposite of a reusable editor. The picture is checked
        against the ranges the build recorded; the script is an input that may change.
        """
        p, plan = self.prepared()
        p['production_script']['text'] += '\nEdited after the build.\n'
        result = audit(p, self.root, plan)
        self.assertTrue(result['verified'], result['errors'])
        self.assertEqual(len(result['warnings']), 1)
        self.assertIn('production script has changed', result['warnings'][0])
        self.assertIn('rebuild the beat timeline', result['warnings'][0].lower())

    @patch('documentary_frame_audit.inspect_video')
    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30))
    def test_a_built_storyboard_still_plays_a_different_script(self, rate, inspect):
        """The same Beat timeline serves a re-supplied script without being rebuilt."""
        p, plan = self.prepared()
        self.assertEqual(len(audit(p, self.root, plan)['warnings']), 0)   # unchanged: silent
        p['production_script']['text'] = p['production_script']['text'].replace(
            'On-screen Caption: None', 'On-screen Caption: reworded')
        result = audit(p, self.root, plan)
        self.assertTrue(result['verified'], result['errors'])            # still plays
        self.assertEqual(len(result['warnings']), 1)                     # and says so
        self.assertIn('reworded', result['warnings'][0])                 # names the change

    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30))
    def test_a_changed_bite_names_the_beat_and_both_values(self, rate):
        """The message must say which beat, which field, and both numbers.

        Measured on a real production: one bite's OUT had been clamped because the
        script asked for 94.000s of a 93.660s file. The audit said only "A saved sound
        bite differs from its script", naming no beat, so the reader had to check 15
        bites and 30 beats by hand before the video would play.
        """
        p, plan = self.prepared()
        plan['bites'][0]['source_out_s'] -= 0.5
        errors = audit(p, self.root, plan)['errors']
        self.assertEqual(len(errors), 1)
        message = errors[0]
        self.assertIn('Beat 002', message)          # which beat
        self.assertIn('source OUT', message)        # which field
        self.assertIn('12.000s', message)           # the script's value
        self.assertIn('11.500s', message)           # the storyboard's value
        self.assertIn('Rebuild the Beat timeline', message)  # what to do

    @patch('documentary_frame_audit.inspect_video', side_effect=ValueError('variable timestamps'))
    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30))
    def test_discontinuous_video_fails_closed(self, rate, inspect):
        p, plan = self.prepared(); result = audit(p, self.root, plan)
        self.assertFalse(result['verified'])
        self.assertIn('variable timestamps', result['errors'][0])

    @patch('documentary_frame_audit.inspect_video')
    @patch('documentary_frame_audit.source_rate', return_value=Fraction(30))
    def test_schedule_keeps_bite_and_narration_clock_and_captions(self, rate, inspect):
        self.p['production_script']['text'] = self.p['production_script']['text'].replace('On-screen Caption: None', 'On-screen Caption: Caption one', 1).replace('On-screen Caption: None', 'On-screen Caption: Speaker', 1)
        p, plan = self.prepared()
        events = playback_schedule(p, self.root, plan)
        self.assertEqual([(e['kind'], e['timeline_in_ms'], e['timeline_out_ms'], e['caption']) for e in events], [('narration', 0, 2000, 'Caption one'), ('sound_bite', 2000, 4000, 'Speaker'), ('narration', 4000, 6000, '')])
        p['scenes'][0]['plan_row']['required_caption'] = 'changed'
        with self.assertRaisesRegex(ValueError, 'caption has changed'):
            playback_schedule(p, self.root, plan)
