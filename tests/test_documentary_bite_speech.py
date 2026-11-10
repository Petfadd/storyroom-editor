import json
import tempfile
import unittest
from pathlib import Path

import documentary_frame_audit
from documentary_speech import for_bite, save


class BiteSpeechStorageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'reviews').mkdir()
        self.project = {'assets': [{'id': 'a1', 'revision': 'r1'}]}
        self.plan = {'revision': 3}
        self.bite = {'id': 'b1', 'asset_id': 'a1', 'source_in_s': 1.0, 'source_out_s': 5.0,
                     'script_beat': '002', 'script_caption': 'Press Conference'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_saved_speech_is_bound_to_the_exact_bite(self):
        asset = self.project['assets'][0]
        save(self.root, self.plan, self.bite, asset,
             [{'start_s': 0.0, 'end_s': 2.0, 'text': 'Spoken words.'}], {'engine': 'test'})
        self.assertEqual(len(for_bite(self.root, self.plan, self.bite, asset)), 1)

    def test_changed_interval_or_revision_drops_stale_speech(self):
        asset = self.project['assets'][0]
        save(self.root, self.plan, self.bite, asset,
             [{'start_s': 0.0, 'end_s': 2.0, 'text': 'Spoken words.'}], {'engine': 'test'})
        moved = dict(self.bite, source_out_s=6.0)
        self.assertEqual(for_bite(self.root, self.plan, moved, asset), [])
        self.assertEqual(for_bite(self.root, {'revision': 4}, self.bite, asset), [])
        replaced = dict(asset, revision='r2')
        self.assertEqual(for_bite(self.root, self.plan, self.bite, replaced), [])

    def test_speech_outside_the_bite_is_rejected(self):
        asset = self.project['assets'][0]
        with self.assertRaises(ValueError):
            save(self.root, self.plan, self.bite, asset,
                 [{'start_s': 0.0, 'end_s': 9.0, 'text': 'Too long.'}], {})
        self.assertFalse((self.root / 'reviews' / 'documentary_bite_speech.json').exists())

    def test_unreadable_file_never_becomes_caption_text(self):
        (self.root / 'reviews' / 'documentary_bite_speech.json').write_text('{not json')
        with self.assertRaises(ValueError):
            for_bite(self.root, self.plan, self.bite, self.project['assets'][0])


class BiteCaptionPlanTest(unittest.TestCase):
    def setUp(self):
        base = {'source_fps': '30000/1001', 'source': 'S01.mp4', 'asset_id': 'a1', 'picture_scale': 100}
        self.events = [dict(base, kind='narration', label='Scene 1', caption='Opening', timeline_in_ms=0, timeline_out_ms=1000,
                            source_start_frame=0, source_end_frame=30),
                       dict(base, kind='sound_bite', label='Beat 002', caption='Label only', timeline_in_ms=1000, timeline_out_ms=4000,
                            source_start_frame=30, source_end_frame=120,
                            speech=[{'start_s': 0.5, 'end_s': 2.0, 'text': 'Spoken in the footage.'}]),
                       dict(base, kind='sound_bite', label='Beat 004', caption='Scripted label', timeline_in_ms=4000, timeline_out_ms=6000,
                            source_start_frame=120, source_end_frame=180, speech=[])]

    def test_bite_speech_becomes_its_own_timed_caption(self):
        import documentary_render_plan as module
        def fake_schedule(project, root, plan):
            return self.events

        original = module.playback_schedule
        module.playback_schedule = fake_schedule
        try:
            result = module.plan({'assets': [], 'scenes': [], 'production_script': {'text': ''}}, Path('/tmp'), {'revision': 1})
        finally:
            module.playback_schedule = original
        texts = [(c['text'], c['timeline_in_ms'], c['timeline_out_ms']) for c in result['captions']]
        # A bite carries picture and its own sound, never burned-in words. The
        # transcribed speech is kept on the bite for review and subtitles, but it
        # is not drawn over the picture, and neither is the caption label.
        self.assertNotIn(('Spoken in the footage.', 1500, 3000), texts)
        self.assertNotIn('Label only', [c['text'] for c in result['captions']])
        # Only the narration beat contributes text; both bites contribute none.
        self.assertEqual([c['text'] for c in result['captions']], ['Opening'])


class PlaybackScheduleSpeechTest(unittest.TestCase):
    def test_schedule_carries_saved_speech_without_changing_clock(self):
        import inspect
        source = inspect.getsource(documentary_frame_audit.playback_schedule)
        self.assertIn("speech=list(bite.get('speech') or [])", source)


if __name__ == '__main__':
    unittest.main()
