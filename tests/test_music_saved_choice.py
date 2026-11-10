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
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / 'inputs').mkdir()
        self.ff = imageio_ffmpeg.get_ffmpeg_exe()
        self.file = self.root / 'inputs' / 'music.wav'
        subprocess.run([self.ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=300:duration=10', '-ac', '2', '-ar', '48000',
                        str(self.file)], check=True)
        self.segments = [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 4.0},
                         {'kind': 'sound_bite', 'timeline_in_s': 4.0, 'timeline_out_s': 7.0},
                         {'kind': 'narration', 'timeline_in_s': 7.0, 'timeline_out_s': 10.0}]

    def tearDown(self):
        self.tmp.cleanup()

    def bed(self, project, name):
        out = self.root / name
        music.build_bed(project, self.file, out, 10.0, self.segments, level_percent=70, duck=True)
        return out

    def test_a_project_that_dips_under_narration_keeps_its_music_audible(self):
        project = {'documentary_music': {'enabled': True, 'volume_percent': 70,
                                         'narration_level': 'duck'}}
        bed = self.bed(project, 'duck.wav')
        self.assertGreater(mean_db(self.ff, bed, 0.5, 3.5), -60)
        self.assertGreater(mean_db(self.ff, bed, 7.5, 9.5), -60)
        self.assertLess(mean_db(self.ff, bed, 4.05, 6.95), -80)

    def test_a_project_that_stops_under_narration_stays_silent(self):
        project = {'documentary_music': {'enabled': True, 'volume_percent': 70,
                                         'narration_level': 'mute'}}
        bed = self.bed(project, 'mute.wav')
        self.assertLess(mean_db(self.ff, bed, 0.5, 3.5), -80)
        self.assertLess(mean_db(self.ff, bed, 7.5, 9.5), -80)

    def test_no_argument_reads_the_project_instead_of_a_fixed_default(self):
        from inspect import signature
        parameter = signature(music.build_bed).parameters['narration_level']
        self.assertIsNone(parameter.default,
                          'build_bed must read the saved choice when no mode is passed')


if __name__ == '__main__':
    unittest.main()
