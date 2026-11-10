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
        self.assertEqual(len(result['parts']), 3)
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
