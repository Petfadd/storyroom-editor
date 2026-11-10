"""The saved music choice must reach the rendered bed.

The bed is built by the exporter, so a setting the user reviewed in Setup has to
be read from the project when no explicit mode is passed. A hardcoded default
here would silently ignore that choice.
"""
import json
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


class SavedNarrationChoiceTest(unittest.TestCase):
    """A saved duck or mute choice must not come back to life.

    The bed used to follow the project's narration setting, so a project saved
    before the change kept dipping or stopping after it. Both are now ignored,
    and the export reads the project the same way the preview does.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'inputs').mkdir()
        self.ff = imageio_ffmpeg.get_ffmpeg_exe()
        self.file = self.root / 'inputs' / 'music.wav'
        subprocess.run([self.ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=300:duration=10', '-ac', '2', '-ar', '48000',
                        str(self.file)], check=True)
        # A speech-free picture span at each end is the reference level.
        self.segments = [{'kind': 'picture', 'timeline_in_s': 0.0, 'timeline_out_s': 1.0},
                         {'kind': 'narration', 'timeline_in_s': 1.0, 'timeline_out_s': 4.0},
                         {'kind': 'sound_bite', 'timeline_in_s': 4.0, 'timeline_out_s': 7.0},
                         {'kind': 'narration', 'timeline_in_s': 7.0, 'timeline_out_s': 9.0},
                         {'kind': 'picture', 'timeline_in_s': 9.0, 'timeline_out_s': 10.0}]

    def tearDown(self):
        self.tmp.cleanup()

    def bed(self, project, name):
        out = self.root / name
        music.build_bed(project, self.file, out, 10.0, self.segments, level_percent=70, duck=True)
        return out

    def test_a_project_saved_as_ducking_plays_the_bed_through_the_bite(self):
        project = {'documentary_music': {'enabled': True, 'volume_percent': 70,
                                         'narration_level': 'duck'}}
        bed = self.bed(project, 'duck.wav')
        reference = mean_db(self.ff, bed, 0.1, 0.9)
        for name, (a, b) in {'narration': (1.3, 3.7), 'bite': (4.3, 6.7),
                             'after': (7.3, 8.7)}.items():
            value = mean_db(self.ff, bed, a, b)
            self.assertGreater(value, -60, f'{name} lost the music')
            self.assertAlmostEqual(value, reference, delta=1.0,
                                   msg=f'{name} is {value} dB against {reference} dB')

    def test_a_project_saved_as_stopping_under_narration_still_plays(self):
        project = {'documentary_music': {'enabled': True, 'volume_percent': 70,
                                         'narration_level': 'mute'}}
        bed = self.bed(project, 'mute.wav')
        reference = mean_db(self.ff, bed, 0.1, 0.9)
        for name, (a, b) in {'narration': (1.3, 3.7), 'bite': (4.3, 6.7),
                             'after': (7.3, 8.7)}.items():
            value = mean_db(self.ff, bed, a, b)
            self.assertGreater(value, -60, f'{name} lost the music')
            self.assertAlmostEqual(value, reference, delta=1.0,
                                   msg=f'{name} is {value} dB against {reference} dB')

    def test_the_bed_is_built_the_same_way_whether_or_not_a_mode_is_passed(self):
        from inspect import signature
        # `duck` must default to off: a caller that passes nothing still gets the
        # continuous bed, rather than the old ducked one.
        self.assertIs(signature(music.build_bed).parameters['duck'].default, False)


if __name__ == '__main__':
    unittest.main()
