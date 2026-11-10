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

    def test_the_bed_has_no_speech_windows(self):
        # The duck/cut helpers are gone, so no speech span can change the gain.
        for name in ('speed_windows', 'speech_windows', '_speech_gain'):
            self.assertFalse(hasattr(music, name), f'{name} would reintroduce a cut')

    def test_bed_is_exact_length_and_unbroken(self):
        out = self.root / 'bed.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'], level_percent=30, duck=True)
        self.assertAlmostEqual(result['duration_s'], 4.0, delta=0.05)
        self.assertFalse(result['ducked_under_speech'])
        self.assertEqual(result['narration_windows'], [])
        with av.open(str(out)) as media:
            self.assertAlmostEqual(float(media.duration / av.time_base), 4.0, delta=0.05)

    def test_a_plain_bed_has_no_windows(self):
        out = self.root / 'plain.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'], level_percent=20, duck=False)
        self.assertFalse(result['ducked_under_speech'])
        self.assertEqual(result['narration_windows'], [])
        self.assertEqual(result['mute_windows'], [])

    def test_missing_music_fails_closed(self):
        with self.assertRaises(ValueError):
            music.build_bed(self.project, self.root / 'inputs' / 'nope.mp3', self.root / 'x.wav', 4.0,
                            self.timeline()['segments'], level_percent=20)

    def test_settings_defaults_describe_a_continuous_bed(self):
        # Nothing is ducked or cut now, so the only settings left are on/off and
        # the one volume.
        self.assertEqual(music.settings(self.project), {'enabled': False, 'volume_percent': 35,
                                                        'narration_level': 'full', 'speech_mode': 'full',
                                                        'bite_cut': False, 'present': False})

    def test_the_bed_is_not_dipped_or_cut_by_any_saved_mode(self):
        # A sound bite and the narration both used to change the gain. Neither
        # does now: the rendered bed comes back with no windows at all.
        segments = [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 2.0},
                    {'kind': 'sound_bite', 'timeline_in_s': 2.0, 'timeline_out_s': 3.0},
                    {'kind': 'narration', 'timeline_in_s': 3.0, 'timeline_out_s': 6.0}]
        for mode in ('duck', 'mute', 'full'):
            with self.subTest(mode):
                result = music.build_bed(self.project, self.saved, self.root / f'{mode}.wav',
                                         6.0, segments, level_percent=30, duck=True,
                                         narration_level=mode)
                self.assertEqual(result['narration_windows'], [])
                self.assertEqual(result['mute_windows'], [])
                self.assertFalse(result['ducked_under_speech'])

    def test_mute_mode_does_not_silence_the_bed(self):
        out = self.root / 'stopped.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'],
                                 level_percent=30, duck=True, narration_level='mute')
        # The saved mode is still reported for older clients, but it changes nothing.
        self.assertEqual(result['narration_level'], 'mute')
        self.assertEqual(result['mute_windows'], [])
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

    def test_documentary_audio_includes_a_continuous_music_bed(self):
        from documentary import annotate_plan
        import documentary_audio
        plan = annotate_plan(self.project, str(self.root), json.loads(json.dumps(self.plan)))
        self.assertEqual(plan['bites'][0]['stale_reasons'], [])
        result = documentary_audio.build_audio(self.root, self.project, plan,
                                               self.root / 'renders' / 'mixed.wav', music=True)
        self.assertAlmostEqual(result['duration_s'], 6.0, delta=0.05)
        # The bed joins the mix unbroken: nothing ducks it or cuts it.
        self.assertFalse(result['music']['ducked_under_speech'])
        self.assertEqual(result['music']['mute_windows'], [])
        self.assertEqual(result['music']['level_percent'], 40)
        plain = documentary_audio.build_audio(self.root, self.project, plan,
                                              self.root / 'renders' / 'plain.wav', music=False)
        self.assertIsNone(plain['music'])
