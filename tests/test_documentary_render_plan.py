import unittest
from unittest.mock import patch
from documentary_render_plan import plan


def event(label, start, end, si, so, *, kind='narration', caption='', scale=100, fps='30'):
    return {'label': label, 'kind': kind, 'asset_id': 'a', 'source': 'S01.mp4',
            'source_start_frame': si, 'source_end_frame': so, 'source_fps': fps,
            'timeline_in_ms': start, 'timeline_out_ms': end, 'caption': caption,
            'picture_scale': scale}


class DocumentaryRenderPlanTest(unittest.TestCase):
    @patch('documentary_render_plan.playback_schedule')
    def test_coalesces_contiguous_cues_without_losing_captions(self, schedule):
        schedule.return_value = [event('a', 0, 1000, 0, 30, caption='Hello'),
                                 event('b', 1000, 2000, 30, 60, caption='Hello'),
                                 event('bite', 2000, 3000, 90, 120, kind='sound_bite', caption='Speaker')]
        result = plan({}, '.', {})
        self.assertTrue(result['picture_compatible'])
        self.assertTrue(result['renderer_implemented'])
        self.assertEqual(len(result['spans']), 2)
        self.assertEqual(result['spans'][0]['labels'], ['a', 'b'])
        self.assertEqual(result['captions'][0], {'text': 'Hello', 'timeline_in_ms': 0, 'timeline_out_ms': 2000})
        # A sound bite carries the caption its Beat asked for, exactly as a narration
        # beat does. The writer's On-screen Caption field is an instruction to show
        # that text, and skipping bites left every named speaker unlabelled.
        self.assertEqual(len(result['captions']), 2)
        self.assertEqual(result['captions'][1],
                         {'text': 'Speaker', 'timeline_in_ms': 2000, 'timeline_out_ms': 3000})

    @patch('documentary_render_plan.playback_schedule')
    def test_never_hides_frame_difference_or_different_scale(self, schedule):
        schedule.return_value = [event('a', 0, 1000, 0, 29), event('b', 1000, 2000, 29, 59, scale=150)]
        result = plan({}, '.', {})
        self.assertFalse(result['picture_compatible'])
        self.assertEqual(len(result['spans']), 2)
        self.assertEqual(result['spans'][0]['frame_difference'], 1)
        self.assertIn('No frames were added, dropped or repeated', result['errors'][0])

    @patch('documentary_render_plan.playback_schedule')
    def test_different_source_rates_fail_closed(self, schedule):
        schedule.return_value = [event('a', 0, 1000, 0, 30), event('b', 1000, 2000, 0, 25, fps='25')]
        result = plan({}, '.', {})
        self.assertFalse(result['picture_compatible'])
        self.assertIn('different frame rates', result['errors'][0])

    @patch('documentary_render_plan.playback_schedule', side_effect=ValueError('Source changed'))
    def test_stale_source_fails_closed(self, schedule):
        self.assertEqual(plan({}, '.', {})['errors'], ['Source changed'])

    @patch('documentary_render_plan.inspect_video')
    @patch('documentary_render_plan.playback_schedule')
    def test_only_exact_explicit_decision_applies(self, schedule, inspect):
        import hashlib, tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'sources').mkdir();source=root/'sources/a.mp4';source.write_bytes(b'original')
            project={'production_script':{'text':'script'},'assets':[{'id':'a','name':'S01.mp4','revision':'r1','path':'sources/a.mp4'}]}
            sound={'revision':1};schedule.return_value=[event('a',0,1000,0,29)]
            change={'label':'a','asset_id':'a','asset_revision':'r1','source':'S01.mp4','source_sha256':hashlib.sha256(b'original').hexdigest(),'source_start_frame':0,'source_end_frame':29,'timeline_in_ms':0,'timeline_out_ms':1000,'delta':1}
            decisions={'version':1,'script_sha256':hashlib.sha256(b'script').hexdigest(),'plan_revision':1,'source_fps':'30','changes':[change]}
            result=plan(project,root,sound,decisions)
            self.assertTrue(result['picture_compatible'],result['errors'])
            self.assertEqual(result['spans'][0]['source_end_frame'],30)
            inspect.assert_called_once_with(source,30)
            source.write_bytes(b'replaced')
            self.assertFalse(plan(project,root,sound,decisions)['picture_compatible'])
            source.write_bytes(b'original');change['timeline_out_ms']=999
            self.assertFalse(plan(project,root,sound,decisions)['picture_compatible'])
            change['timeline_out_ms']=1000;decisions['plan_revision']=2
            self.assertFalse(plan(project,root,sound,decisions)['picture_compatible'])


class BiteCaptionsTest(unittest.TestCase):
    """The caption a Beat asks for is shown, on a bite as much as on narration."""

    @patch('documentary_render_plan.playback_schedule')
    def test_a_bite_shows_its_own_label_and_never_its_transcript(self, schedule):
        bite = event('bite', 1000, 3000, 0, 60, kind='sound_bite', caption='Maria Sharapova — Press Conference')
        bite['speech'] = [{'start_s': 0.2, 'end_s': 1.0, 'text': 'I did fail the test'}]
        schedule.return_value = [event('a', 0, 1000, 0, 30, caption='March 7, 2016'), bite]
        result = plan({}, '.', {})
        self.assertEqual([c['text'] for c in result['captions']],
                         ['March 7, 2016', 'Maria Sharapova — Press Conference'])
        # The spoken words are never burned in; only the writer's caption is.
        self.assertNotIn('I did fail the test', [c['text'] for c in result['captions']])

    @patch('documentary_render_plan.playback_schedule')
    def test_narration_captions_still_render(self, schedule):
        schedule.return_value = [event('a', 0, 1000, 0, 30, caption='Substance: Meldonium')]
        result = plan({}, '.', {})
        self.assertEqual([c['text'] for c in result['captions']], ['Substance: Meldonium'])
