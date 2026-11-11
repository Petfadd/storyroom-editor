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
        # The bed itself stays flat and unbroken; the duck is applied by the sidechain
        # when it is mixed with speech, so there are no gain windows in the file.
        self.assertTrue(result['ducked_under_speech'])
        self.assertGreater(result['duck_depth_db'], 0)
        with av.open(str(out)) as media:
            self.assertAlmostEqual(float(media.duration / av.time_base), 4.0, delta=0.05)

    def test_a_plain_bed_has_no_windows(self):
        out = self.root / 'plain.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'], level_percent=20, duck=False)
        # Asking for an un-ducked bed no longer produces one: the mix ducks regardless,
        # because a flat bed across speech is what the direction reversed.
        self.assertTrue(result['ducked_under_speech'])
        self.assertEqual(result['narration_level'], 'duck')
        self.assertEqual(result.get('mute_windows', []), [])

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

    def test_no_saved_mode_can_cut_the_bed(self):
        # The bed is never cut and never muted for a bite: it is one continuous piece
        # that is only ever lowered underneath speech. No saved mode reopens a gap.
        segments = [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 2.0},
                    {'kind': 'sound_bite', 'timeline_in_s': 2.0, 'timeline_out_s': 3.0},
                    {'kind': 'narration', 'timeline_in_s': 3.0, 'timeline_out_s': 6.0}]
        for mode in ('duck', 'mute', 'full'):
            with self.subTest(mode):
                result = music.build_bed(self.project, self.saved, self.root / f'{mode}.wav',
                                         6.0, segments, level_percent=30, duck=True,
                                         narration_level=mode)
                self.assertEqual(result.get('narration_windows', []), [])
                self.assertEqual(result.get('mute_windows', []), [])
                self.assertTrue(result['ducked_under_speech'])
                self.assertEqual(result['narration_level'], 'duck')

    def test_mute_mode_does_not_silence_the_bed(self):
        out = self.root / 'stopped.wav'
        result = music.build_bed(self.project, self.saved, out, 4.0, self.timeline()['segments'],
                                 level_percent=30, duck=True, narration_level='mute')
        # A saved 'mute' from an older project cannot stop the bed; it ducks instead.
        self.assertEqual(result['narration_level'], 'duck')
        self.assertEqual(result.get('mute_windows', []), [])
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
        self.assertTrue(result['music']['ducked_under_speech'])
        self.assertEqual(result['music'].get('mute_windows', []), [])
        self.assertEqual(result['music']['level_percent'], 40)
        plain = documentary_audio.build_audio(self.root, self.project, plan,
                                              self.root / 'renders' / 'plain.wav', music=False)
        self.assertIsNone(plain['music'])


class DuckingTest(unittest.TestCase):
    """The bed must actually drop under speech and come back, measured not assumed."""

    def setUp(self):
        import tempfile
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ff = imageio_ffmpeg.get_ffmpeg_exe()
        subprocess.run([self.ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=440:duration=6:sample_rate=48000',
                        '-af', 'volume=0.5', '-c:a', 'pcm_s16le',
                        str(self.root / 'music.wav')], check=True)
        # Speech-like bursts sitting at 2s..4s inside a 6s stream.
        subprocess.run([self.ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=180:duration=2:sample_rate=48000',
                        '-af', 'volume=0.9', '-c:a', 'pcm_s16le',
                        str(self.root / 'spk.wav')], check=True)
        subprocess.run([self.ff, '-hide_banner', '-loglevel', 'error', '-y',
                        '-i', str(self.root / 'spk.wav'), '-af',
                        'adelay=2000,apad=whole_dur=6', '-c:a', 'pcm_s16le',
                        str(self.root / 'speech.wav')], check=True)

    def tearDown(self):
        self.tmp.cleanup()

    def lufs(self, path, start, duration):
        import re
        probe = subprocess.run([self.ff, '-hide_banner', '-ss', str(start), '-t', str(duration),
                                '-i', str(path), '-af', 'ebur128', '-f', 'null', '-'],
                               capture_output=True, text=True).stderr
        found = re.findall(r'I:\s*(-?[\d.]+) LUFS', probe)
        return float(found[-1]) if found else None

    def test_the_bed_drops_under_speech_and_recovers(self):
        out = self.root / 'ducked.wav'
        music.duck_track(self.root / 'music.wav', self.root / 'speech.wav', out)
        before = self.lufs(out, 0, 2)
        during = self.lufs(out, 2, 2)
        after = self.lufs(out, 4, 2)
        self.assertLess(during, before - 4,
                        'the bed did not duck under speech')
        self.assertGreater(after, during + 3,
                           'the bed did not come back up when the speech stopped')
        self.assertAlmostEqual(after, before, delta=2.0,
                               msg='the bed must return to its own level, not a lower one')

    def test_ducking_never_cuts_the_bed(self):
        out = self.root / 'ducked.wav'
        music.duck_track(self.root / 'music.wav', self.root / 'speech.wav', out)
        # Under speech the bed is quiet, but it must not be silent: a gap would be a
        # cut, and a cut is the behaviour that was explicitly removed.
        self.assertGreater(self.lufs(out, 2, 2), -60,
                           'the bed went silent under speech instead of ducking')
