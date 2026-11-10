"""The music bed must be cut for the whole of every sound bite.

A bite carries the only audio in its span. The bed may dip under narration but it
must be silent under a bite, in the exported mix as well as the browser preview.
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
        return {'duration_s': 12.0, 'segments': [
            {'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 4.0},
            {'kind': 'sound_bite', 'timeline_in_s': 4.0, 'timeline_out_s': 8.0},
            {'kind': 'narration', 'timeline_in_s': 8.0, 'timeline_out_s': 12.0}]}

    def test_the_bed_is_silent_for_the_whole_bite(self):
        timeline = self.timeline()
        bed = self.root/'bed.wav'
        result = build_bed(self.project, self.root/'inputs'/'music.wav', bed,
                           timeline['duration_s'], timeline['segments'], level_percent=35,
                           duck=True, narration_level='duck')
        self.assertEqual(result['narration_level'], 'duck')
        bite = next(w for w in result['mute_windows'])
        # Measure inside the bite and under the narration either side of it.
        inside = mean_db(self.ff, bed, bite['start_s']+0.3, bite['end_s']-0.3)
        before = mean_db(self.ff, bed, 0.5, 3.0)
        after = mean_db(self.ff, bed, 8.5, 11.5)
        self.assertIsNotNone(inside)
        self.assertLess(inside, -80, 'the music bed must be cut under the bite')
        self.assertGreater(before, -60, 'the bed should still be audible under narration')
        self.assertGreater(after, -60)

    def test_no_bite_window_is_left_out_of_the_cut(self):
        timeline = self.timeline()
        bed = self.root/'bed.wav'
        result = build_bed(self.project, self.root/'inputs'/'music.wav', bed,
                           timeline['duration_s'], timeline['segments'], level_percent=35,
                           duck=True, narration_level='mute')
        bites = [s for s in timeline['segments'] if s['kind'] == 'sound_bite']
        self.assertTrue(bites)
        for segment in bites:
            covered = any(w['start_s'] <= segment['timeline_in_s']+1e-6 and
                          w['end_s'] >= segment['timeline_out_s']-1e-6
                          for w in result['mute_windows'])
            self.assertTrue(covered, f"bite at {segment['timeline_in_s']}s is not fully cut")


if __name__ == '__main__':
    unittest.main()
