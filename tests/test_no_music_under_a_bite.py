"""No setting may put music under a sound bite.

A bite carries the only audio in its span. Dipping the bed under narration is a
choice; playing it under a bite is not, so there is no mode that does it.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import imageio_ffmpeg

import documentary_music as music


def mean_db(ff, path, a, b):
    out = subprocess.run([ff, '-hide_banner', '-nostats', '-i', str(path), '-af',
                          f'atrim=start={a}:end={b},volumedetect', '-f', 'null', '-'],
                         capture_output=True, text=True)
    m = re.search(r'mean_volume:\s*(-?[\d.]+|-inf) dB', out.stderr)
    return float(m.group(1)) if m else None


class NoMusicUnderABiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'inputs').mkdir()
        self.ff = imageio_ffmpeg.get_ffmpeg_exe()
        self.music = self.root / 'inputs' / 'music.wav'
        subprocess.run([self.ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=300:duration=12', '-ac', '2', '-ar', '48000',
                        str(self.music)], check=True)
        self.project = {'voiceover': {'duration': 12.0},
                        'documentary_music': {'enabled': True, 'volume_percent': 60}}

    def tearDown(self):
        self.tmp.cleanup()

    def segments(self):
        # narration 0-4, bite 4-8, narration 8-12 on the finished clock.
        return [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 4.0},
                {'kind': 'sound_bite', 'timeline_in_s': 4.0, 'timeline_out_s': 8.0},
                {'kind': 'narration', 'timeline_in_s': 8.0, 'timeline_out_s': 12.0}]

    def test_a_ducked_narration_still_leaves_the_bite_silent(self):
        bed = self.root / 'bed.wav'
        result = music.build_bed(self.project, self.music, bed, 12.0, self.segments(),
                                 level_percent=60, duck=True, narration_level='duck')
        self.assertEqual(result['narration_level'], 'duck')
        # The bed stays audible under the narration...
        self.assertGreater(mean_db(self.ff, bed, 0.5, 3.5), -60)
        self.assertGreater(mean_db(self.ff, bed, 8.5, 11.5), -60)
        # ...and is cut for the whole bite, including its first and last frames.
        self.assertLess(mean_db(self.ff, bed, 4.05, 7.95), -80)

    def test_there_is_no_setting_that_plays_music_under_a_bite(self):
        # The old saved setting and the old API argument must not resurrect it.
        for saved in ({'speech_mode': 'duck'}, {'duck_under_speech': False}, {'narration_level': 'duck'}):
            project = {'voiceover': {'duration': 12.0},
                       'documentary_music': {'enabled': True, 'volume_percent': 60, **saved}}
            windows = music.speech_windows(self.segments(), 12.0, padding_s=0)
            gain = music._speech_gain(windows, 12.0, 0.355)
            inside = [r for a, b, r in gain if a < 6.0 < b]
            self.assertEqual(inside, [0.0], f'{saved} left music under the bite')

    def test_the_settings_report_no_duck_under_bites_option(self):
        state = music.settings(self.project)
        self.assertNotIn('duck_under_speech', state)
        self.assertTrue(state['bite_cut'])
        self.assertEqual(state['narration_level'], 'duck')

    def test_a_saved_duck_under_speech_keeps_only_the_narration_choice(self):
        # An old project saved with duck_under_speech must still open, with the
        # bite rule enforced and its narration preference preserved.
        project = {'documentary_music': {'enabled': True, 'duck_under_speech': False,
                                         'speech_mode': 'mute'}}
        state = music.settings(project)
        self.assertEqual(state['narration_level'], 'mute')
        self.assertNotIn('duck_under_speech', state)


if __name__ == '__main__':
    unittest.main()
