import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import av
import imageio_ffmpeg

import documentary_music as music
from documentary import build_timeline


class MusicBedTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'inputs').mkdir()
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        self.music = self.root / 'inputs' / 'background_music.wav'
        self.saved = self.root / 'inputs' / 'background_music'
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=3', '-ac', '2', '-ar', '48000', str(self.music)], check=True)
        self.music.replace(self.saved)
        self.project = {'voiceover': {'duration': 4}}

    def tearDown(self):
        self.tmp.cleanup()

    def timeline(self):
        return {'segments': [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 2.0},
                             {'kind': 'sound_bite', 'timeline_in_s': 2.0, 'timeline_out_s': 3.0},
                             {'kind': 'narration', 'timeline_in_s': 3.0, 'timeline_out_s': 4.0}]}

    def test_duck_windows_cover_all_speech(self):
        windows = music.speed_windows(self.timeline()['segments'], 4.0, padding_s=0)
        self.assertEqual(windows, [(0.0, 4.0)])

    def test_bed_is_exact_length_and_ducked(self):
        out = self.root / 'bed.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'], level_percent=30, duck=True)
        self.assertAlmostEqual(result['duration_s'], 4.0, delta=0.05)
        self.assertTrue(result['ducked_under_speech'])
        with av.open(str(out)) as media:
            self.assertAlmostEqual(float(media.duration / av.time_base), 4.0, delta=0.05)

    def test_unducked_bed_has_no_windows(self):
        out = self.root / 'plain.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'], level_percent=20, duck=False)
        self.assertFalse(result['ducked_under_speech'])
        self.assertEqual(result['narration_windows'], [])
        self.assertEqual(result['mute_windows'], [])

    def test_missing_music_fails_closed(self):
        with self.assertRaises(ValueError):
            music.build_bed(self.project, self.root / 'inputs' / 'nope.mp3', self.root / 'x.wav', 4.0,
                            self.timeline()['segments'], level_percent=20)

    def test_settings_defaults_are_conservative(self):
        # A bite is always silent, so 'bite_cut' is not a setting. Only the
        # narration behaviour is a choice, and its default keeps the bed audible.
        self.assertEqual(music.settings(self.project), {'enabled': False, 'volume_percent': 35,
                                                        'narration_level': 'duck', 'speech_mode': 'duck',
                                                        'bite_cut': True, 'present': False})

    def test_music_stops_under_a_bite_but_only_dips_under_narration(self):
        # Continuous narration must never silence the bed for the whole runtime;
        # only a footage sound bite does that, and no setting can change it.
        segments = [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 2.0},
                    {'kind': 'sound_bite', 'timeline_in_s': 2.0, 'timeline_out_s': 3.0},
                    {'kind': 'narration', 'timeline_in_s': 3.0, 'timeline_out_s': 6.0}]
        windows = music.speech_windows(segments, 6.0, padding_s=0)
        # Narration either side of the bite is ducked, never stopped; the bite is
        # the only window that silences the bed.
        self.assertEqual(windows['narration'], [(0.0, 2.0), (3.0, 6.0)])
        self.assertEqual(windows['bite'], [(2.0, 3.0)])
        # Stopping under the narration silences the bed for the whole runtime, and
        # the neighbouring zero-gain slices merge into one cut.
        stopped = music._speech_gain(windows, 6.0, 0.0)
        self.assertEqual([(round(a, 3), round(b, 3), r) for a, b, r in stopped], [(0.0, 6.0, 0.0)])
        # The narration may dip instead of stopping, but the bite is still cut:
        # its gain is fixed at zero and is not a parameter.
        ducked = music._speech_gain(windows, 6.0, 0.355)
        self.assertEqual([r for _, _, r in ducked], [0.355, 0.0, 0.355])
        self.assertEqual({a for a, b, r in ducked if r == 0.0}, {2.0})

    def test_mute_mode_silences_the_bite_in_the_rendered_bed(self):
        out = self.root / 'stopped.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'],
                                 level_percent=30, duck=True, narration_level='mute')
        self.assertEqual(result['narration_level'], 'mute')
        # One footage sound bite in this timeline, so exactly one stopped window.
        self.assertEqual(len(result['mute_windows']), 1)
        self.assertAlmostEqual(result['mute_windows'][0]['start_s'], 2.0, delta=0.15)
        self.assertAlmostEqual(result['duration_s'], 4.0, delta=0.05)


if __name__ == '__main__':
    unittest.main()


class MusicMixIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('inputs', 'sources', 'renders'):
            (self.root / name).mkdir()
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        narration = self.root / 'inputs' / 'narration.wav'
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=220:duration=4', '-ac', '2', '-c:a', 'pcm_s16le', str(narration)], check=True)
        source = self.root / 'sources' / 'source.mp4'
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'testsrc=size=64x64:rate=30:duration=6', '-f', 'lavfi',
                        '-i', 'sine=frequency=520:duration=6', '-shortest', '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-c:a', 'aac', str(source)], check=True)
        music = self.root / 'inputs' / 'background_music.wav'
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=300:duration=4', '-ac', '2', '-c:a', 'pcm_s16le', str(music)], check=True)
        stat = narration.stat()
        self.project = {'id': 'p', 'voiceover': {'path': 'inputs/narration.wav', 'duration': 4, 'name': 'n',
                                                'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns},
                        'assets': [{'id': 'a1', 'name': 'source.mp4', 'original_name': 'source.mp4',
                                    'path': 'sources/source.mp4', 'kind': 'video', 'duration': 6}],
                        'documentary_music': {'enabled': True, 'name': 'background_music.wav',
                                              'volume_percent': 40, 'duck_under_speech': True, 'present': True}}
        self.plan = {'version': 1, 'revision': 1, 'bites': [
            {'id': 'b1', 'pause_s': 2.0, 'asset_id': 'a1', 'asset_name': 'source.mp4', 'asset_revision': None,
             'source_in_s': 1.0, 'source_out_s': 3.0, 'narration_name': 'n', 'narration_size': stat.st_size,
             'narration_mtime_ns': stat.st_mtime_ns, 'created': 1.0}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_documentary_audio_includes_a_ducked_music_bed(self):
        from documentary import annotate_plan
        import documentary_audio
        plan = annotate_plan(self.project, str(self.root), json.loads(json.dumps(self.plan)))
        self.assertEqual(plan['bites'][0]['stale_reasons'], [])
        result = documentary_audio.build_audio(self.root, self.project, plan,
                                               self.root / 'renders' / 'mixed.wav', music=True)
        self.assertAlmostEqual(result['duration_s'], 6.0, delta=0.05)
        self.assertTrue(result['music']['ducked_under_speech'])
        self.assertEqual(result['music']['level_percent'], 40)
        plain = documentary_audio.build_audio(self.root, self.project, plan,
                                              self.root / 'renders' / 'plain.wav', music=False)
        self.assertIsNone(plain['music'])
