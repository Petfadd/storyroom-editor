"""The bed plays under everything: it is never stopped and never dipped.

A sound bite used to silence the music for its whole span, and the narration
dipped it to 0.355. Both are gone: the score runs unbroken at the one volume the
user set, in the preview and in the export, so the two cannot disagree.
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
        # picture 0-2, narration 2-5, bite 5-9, picture 9-12 on the finished
        # clock. The picture spans carry no speech at all, so they are the
        # reference level: anything quieter than them is a duck or a cut.
        return [{'kind': 'picture', 'timeline_in_s': 0.0, 'timeline_out_s': 2.0},
                {'kind': 'narration', 'timeline_in_s': 2.0, 'timeline_out_s': 5.0},
                {'kind': 'sound_bite', 'timeline_in_s': 5.0, 'timeline_out_s': 9.0},
                {'kind': 'picture', 'timeline_in_s': 9.0, 'timeline_out_s': 12.0}]

    def spans(self, bed):
        """Measured level of each kind of span, in dB."""
        return {'picture': mean_db(self.ff, bed, 0.3, 1.7),
                'narration': mean_db(self.ff, bed, 2.3, 4.7),
                'bite': mean_db(self.ff, bed, 5.3, 8.7),
                'picture_end': mean_db(self.ff, bed, 9.3, 11.7)}

    def test_the_bite_plays_at_the_same_level_as_silent_picture(self):
        bed = self.root / 'bed.wav'
        result = music.build_bed(self.project, self.music, bed, 12.0, self.segments(),
                                 level_percent=60, duck=True, narration_level='duck')
        self.assertEqual(result['narration_level'], 'duck')
        self.assertEqual(result['mute_windows'], [])
        levels = self.spans(bed)
        # The bite carries the bed at the level of speech-free picture: a dip or
        # a cut anywhere here is the bug this test exists to catch.
        self.assertAlmostEqual(levels['bite'], levels['picture'], delta=1.0,
                               msg=f"bite {levels['bite']} dB vs picture {levels['picture']} dB")
        self.assertAlmostEqual(levels['narration'], levels['picture'], delta=1.0,
                               msg=f"narration {levels['narration']} dB vs picture {levels['picture']} dB")

    def test_the_bed_level_is_flat_from_end_to_end(self):
        bed = self.root / 'flat.wav'
        music.build_bed(self.project, self.music, bed, 12.0, self.segments(), level_percent=60)
        levels = self.spans(bed)
        reference = levels['picture']
        for name, value in levels.items():
            self.assertAlmostEqual(value, reference, delta=1.0,
                                   msg=f'{name} is {value} dB against {reference} dB of picture')

    def test_a_saved_duck_or_mute_opens_as_a_continuous_bed(self):
        # Both old settings must open, and neither may bring the duck or the cut
        # back: the export reads the project, so a stale save would reintroduce
        # exactly what the user asked to remove.
        for saved in ({'speech_mode': 'duck'}, {'duck_under_speech': False},
                      {'narration_level': 'mute'}, {'speech_mode': 'mute'}):
            with self.subTest(saved):
                project = {'voiceover': {'duration': 12.0},
                           'documentary_music': {'enabled': True, 'volume_percent': 60, **saved}}
                bed = self.root / 'old.wav'
                result = music.build_bed(project, self.music, bed, 12.0, self.segments())
                self.assertEqual(result['narration_level'], 'full')
                self.assertFalse(result['ducked_under_speech'])
                levels = self.spans(bed)
                self.assertAlmostEqual(levels['bite'], levels['picture'], delta=1.0,
                                       msg=f'{saved} dipped or cut the bite')

    def test_the_settings_report_a_continuous_bed(self):
        state = music.settings(self.project)
        self.assertNotIn('duck_under_speech', state)
        self.assertFalse(state['bite_cut'])
        self.assertEqual(state['narration_level'], 'full')
        self.assertEqual(state['speech_mode'], 'full')


if __name__ == '__main__':
    unittest.main()
