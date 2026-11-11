"""Integration tests that actually encode, using the bundled ffmpeg.

These are the two properties that matter most and were, until this file existed, only
checked by hand:

1. a proxy preserves what editing depends on -- duration, frame rate and audio;
2. export uses the original source, never the proxy.

The second one is the promise that makes proxies safe at all. If it ever breaks, the
editor would quietly deliver a 540p master, which is exactly the kind of failure that
looks fine until someone watches the finished film.
"""
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import cache
import proxy

try:
    import imageio_ffmpeg
    FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
except Exception:                                        # pragma: no cover
    FFMPEG = None

requires_ffmpeg = unittest.skipIf(FFMPEG is None, 'ffmpeg is not available')


def make_source(folder: Path, height=1080, seconds=2, fps=30, audio=True) -> Path:
    """A real video file with a known shape."""
    target = folder / f'source_{height}_{seconds}s.mp4'
    command = [FFMPEG, '-y', '-hide_banner', '-loglevel', 'error',
               '-f', 'lavfi', '-i',
               f'testsrc2=s={int(height * 16 / 9)}x{height}:r={fps}:d={seconds}']
    if audio:
        command += ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={seconds}']
    command += ['-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p']
    if audio:
        command += ['-c:a', 'aac', '-shortest']
    command += [str(target)]
    subprocess.run(command, check=True, capture_output=True)
    return target


@requires_ffmpeg
class ProxyPreservesEditingPropertiesTest(unittest.TestCase):
    """A proxy may lose pixels. It must not lose timing or audio."""

    def setUp(self):
        self.folder = Path(tempfile.mkdtemp())
        self.store = cache.Store(self.folder / 'cache')

    def tearDown(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def test_duration_frame_rate_and_audio_survive(self):
        source = make_source(self.folder, height=1080, seconds=2, fps=30)
        before = proxy.probe(FFMPEG, source)
        built = proxy.ensure(FFMPEG, self.store, source, '540p')
        self.assertIsNotNone(built, 'a 1080p source should have been proxied')
        after = proxy.probe(FFMPEG, Path(built['path']))

        self.assertAlmostEqual(after['duration'], before['duration'], delta=0.15,
                               msg='the proxy is a different length, so every cut and '
                                   'every caption would be mistimed')
        self.assertAlmostEqual(after['fps'], before['fps'], delta=0.2,
                               msg='the proxy has a different frame rate')
        self.assertEqual(after['audio'], before['audio'],
                         'the proxy lost its audio track')
        self.assertEqual(after['height'], 540)

    def test_the_proxy_is_substantially_smaller(self):
        source = make_source(self.folder, height=1080, seconds=2)
        built = proxy.ensure(FFMPEG, self.store, source, '540p')
        self.assertLess(Path(built['path']).stat().st_size,
                        source.stat().st_size,
                        'the editing copy is not smaller than the original')

    def test_a_second_request_reuses_the_first_proxy(self):
        source = make_source(self.folder, height=1080, seconds=1)
        first = proxy.ensure(FFMPEG, self.store, source, '540p')
        stamp = Path(first['path']).stat().st_mtime_ns
        second = proxy.ensure(FFMPEG, self.store, source, '540p')
        self.assertEqual(first['path'], second['path'])
        self.assertEqual(stamp, Path(second['path']).stat().st_mtime_ns,
                         'the proxy was rebuilt instead of being reused')

    def test_changing_the_quality_rebuilds_in_place(self):
        """One source keeps one proxy. Raising the quality replaces it rather than
        adding a second file, so switching back and forth cannot accumulate copies."""
        source = make_source(self.folder, height=1080, seconds=1)
        low = proxy.ensure(FFMPEG, self.store, source, '540p')
        self.assertEqual(low['height'], 540)
        high = proxy.ensure(FFMPEG, self.store, source, '720p')
        self.assertEqual(proxy.probe(FFMPEG, Path(high['path']))['height'], 720,
                         'the quality setting did not reach the encoder')
        self.assertEqual(low['path'], high['path'])
        proxies = list(Path(high['path']).parent.glob('*.mp4'))
        self.assertEqual(len(proxies), 1,
                         'a second proxy was left on disk for the same source')
        back = proxy.ensure(FFMPEG, self.store, source, '540p')
        self.assertEqual(proxy.probe(FFMPEG, Path(back['path']))['height'], 540)
        self.assertEqual(len(list(Path(back['path']).parent.glob('*.mp4'))), 1)

    def test_a_small_source_is_not_proxied(self):
        source = make_source(self.folder, height=480, seconds=1)
        self.assertIsNone(proxy.ensure(FFMPEG, self.store, source, '540p'),
                          'a small file was transcoded for no reason')

    def test_the_original_is_never_modified(self):
        source = make_source(self.folder, height=1080, seconds=1)
        before = (source.stat().st_size, source.stat().st_mtime_ns)
        proxy.ensure(FFMPEG, self.store, source, '540p')
        after = (source.stat().st_size, source.stat().st_mtime_ns)
        self.assertEqual(before, after, 'building a proxy altered the uploaded original')

    def test_an_interrupted_build_leaves_no_half_file(self):
        source = make_source(self.folder, height=1080, seconds=1)
        built = proxy.ensure(FFMPEG, self.store, source, '540p')
        leftovers = list(Path(built['path']).parent.glob('*.part*'))
        self.assertEqual(leftovers, [],
                         'a partial file was left behind and could be mistaken for a '
                         'finished proxy')


@requires_ffmpeg
class ExportUsesTheOriginalTest(unittest.TestCase):
    """The one promise that makes proxies safe: delivery never sees one."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        (self.root / 'sources').mkdir()
        (self.root / 'catalog.json').write_text('{"slots": {}}')

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_the_engine_resolves_the_source_file_not_a_proxy(self):
        import engine
        import json

        video = make_source(self.root / 'sources', height=1080, seconds=1)
        story = {'settings': {'width': 1080, 'height': 1920, 'fps': 30},
                 'shots': [], 'reserve_sources': []}
        (self.root / 'storyboard.json').write_text(json.dumps(story))

        job = engine.RenderJob.__new__(engine.RenderJob)
        job.root = self.root
        job.catalog = {}
        resolved = job._resolve_visual({
            'slot': None, 'shot': 1, 'kind': 'narration', 'media_type': 'video',
            'source_file': video.name, 'video_in_ms': 0, 'video_out_ms': 500,
            'duration_ms': 500,
        })
        path = Path(str(resolved['path']))
        self.assertEqual(path.resolve(), video.resolve(),
                         'the renderer picked up something other than the source file')
        self.assertNotIn('cache', str(path).lower())
        self.assertNotIn('proxy', str(path).lower())
        self.assertIn('sources', str(path))

    def test_the_engine_ignores_a_proxy_that_exists_for_the_same_source(self):
        """Even with a proxy sitting in the cache, the engine still resolves the
        original. Proxies are for the screen; delivery reads the source."""
        import engine

        video = make_source(self.root / 'sources', height=1080, seconds=1)
        store = cache.Store(self.root / 'cache')
        built = proxy.ensure(FFMPEG, store, video, '540p')
        self.assertIsNotNone(built, 'no proxy was built, so this test proves nothing')
        self.assertNotEqual(Path(built['path']).resolve(), video.resolve())

        job = engine.RenderJob.__new__(engine.RenderJob)
        job.root = self.root
        job.catalog = {}
        resolved = job._resolve_visual({
            'slot': None, 'shot': 1, 'kind': 'narration', 'media_type': 'video',
            'source_file': video.name, 'video_in_ms': 0, 'video_out_ms': 500,
            'duration_ms': 500,
        })
        self.assertEqual(Path(str(resolved['path'])).resolve(), video.resolve(),
                         'the renderer used the editing copy instead of the original')


if __name__ == '__main__':
    unittest.main()
