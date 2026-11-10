"""The exported bed must play unbroken through every bite and all narration.

This file used to assert the opposite: the music was cut for the whole of every
sound bite, and under the narration it either dipped or stopped. Both were
removed, so the guarantee is now continuity end to end, measured on the rendered
WAV rather than on the filter string that produced it.
"""
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import imageio_ffmpeg

from documentary_music import build_bed


def mean_db(ff, path, a, b):
    out = subprocess.run([ff, '-hide_banner', '-nostats', '-i', str(path), '-af',
                          f'atrim=start={a}:end={b},volumedetect', '-f', 'null', '-'],
                         capture_output=True, text=True)
    m = re.search(r'mean_volume:\s*(-?[\d.]+|-inf) dB', out.stderr)
    return float(m.group(1)) if m else None


class MusicCutAtBitesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        for name in ('inputs', 'sources'):
            (self.root / name).mkdir()
        ff = imageio_ffmpeg.get_ffmpeg_exe()
        self.ff = ff
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=220:duration=12', '-ac', '2', '-c:a', 'pcm_s16le',
                        str(self.root/'inputs'/'narration.wav')], check=True)
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'testsrc=size=64x64:rate=30:duration=14', '-f', 'lavfi',
                        '-i', 'sine=frequency=520:duration=14', '-shortest', '-c:v', 'libx264',
                        '-preset', 'ultrafast', '-c:a', 'aac', str(self.root/'sources'/'S01.mp4')], check=True)
        subprocess.run([ff, '-hide_banner', '-loglevel', 'error', '-y', '-f', 'lavfi',
                        '-i', 'sine=frequency=330:duration=5', '-ac', '2', '-ar', '48000',
                        str(self.root/'inputs'/'music.wav')], check=True)
        self.project = {'voiceover': {'duration': 12.0},
                        'documentary_music': {'enabled': True, 'volume_percent': 35}}

    def tearDown(self):
        self.tmp.cleanup()

    def timeline(self):
        # narration 0-4, bite 4-8, narration 8-12 on the finished clock.
        # picture 0-2, narration 2-6, bite 6-10, picture 10-12. The picture
        # spans carry no speech, so they are the reference level.
        return {'duration_s': 12.0, 'segments': [
            {'kind': 'picture', 'timeline_in_s': 0.0, 'timeline_out_s': 2.0},
            {'kind': 'narration', 'timeline_in_s': 2.0, 'timeline_out_s': 6.0},
            {'kind': 'sound_bite', 'timeline_in_s': 6.0, 'timeline_out_s': 10.0},
            {'kind': 'picture', 'timeline_in_s': 10.0, 'timeline_out_s': 12.0}]}

    def spans(self, bed):
        return {'picture': mean_db(self.ff, bed, 0.3, 1.7),
                'narration': mean_db(self.ff, bed, 2.3, 5.7),
                'bite': mean_db(self.ff, bed, 6.3, 9.7),
                'picture_end': mean_db(self.ff, bed, 10.3, 11.7)}

    def test_the_bed_plays_through_the_whole_bite(self):
        timeline = self.timeline()
        bed = self.root/'bed.wav'
        result = build_bed(self.project, self.root/'inputs'/'music.wav', bed,
                           timeline['duration_s'], timeline['segments'], level_percent=35,
                           duck=True, narration_level='duck')
        self.assertEqual(result['narration_level'], 'duck')
        # No window is reported, so the caller cannot think anything was cut.
        self.assertEqual(result['mute_windows'], [])
        levels = self.spans(bed)
        self.assertGreater(levels['bite'], -60, 'the music bed must play through the bite')
        # Every span sits at the picture level: no dip, no cut, no boundary step.
        for name, value in levels.items():
            self.assertAlmostEqual(value, levels['picture'], delta=1.0,
                                   msg=f'{name} is {value} dB against {levels["picture"]} dB')

    def test_every_bite_and_every_narration_span_is_untouched(self):
        timeline = self.timeline()
        bed = self.root/'bed.wav'
        result = build_bed(self.project, self.root/'inputs'/'music.wav', bed,
                           timeline['duration_s'], timeline['segments'], level_percent=35,
                           duck=True, narration_level='mute')
        # `mute` used to silence the narration and every bite. It now changes
        # nothing, so no segment, of either kind, is a window.
        self.assertEqual(result['narration_windows'], [])
        self.assertEqual(result['mute_windows'], [])
        levels = self.spans(bed)
        for name, value in levels.items():
            self.assertGreater(value, -60, f'{name} lost the music')
            self.assertAlmostEqual(value, levels['picture'], delta=1.0,
                                   msg=f'{name} is {value} dB against {levels["picture"]} dB')


if __name__ == '__main__':
    unittest.main()
