"""The timeline must stay a strip, not take over the window.

The panel grew by a stack of later overrides — 226, 260, 278, 290, 318, 324,
332, 356, 376px — each one patching the previous. The last of them pushed the
picture up and squeezed the monitor on a laptop screen. These tests keep the
sizes in one place, keep them consistent, and keep them small.
"""
import re
import unittest
from pathlib import Path

CSS = Path('static/editor.css').read_text()


def strip_comments(text):
    return re.sub(r'/\*.*?\*/', '', text, flags=re.S)


def variables(text, media_small=False):
    """Read the --doc-* sizes from :root, honouring the short-screen override."""
    values = {}
    for block in re.findall(r':root\{([^}]*)\}', text):
        for name, value in re.findall(r'(--doc-[a-z]+)\s*:\s*([^;]+)', block):
            if media_small or name not in values:
                values[name] = value.strip()
    return values


class TimelineSizingTest(unittest.TestCase):
    def setUp(self):
        self.css = strip_comments(CSS)

    # --- CSS reading helpers -------------------------------------------------
    def bodies(self, css, selector):
        """Every declaration body in the sheet for one selector, in order."""
        found = []
        for match in re.finditer(r'([^{}]+)\{([^{}]*)\}', css):
            key = re.sub(r'/\*.*?\*/', '', match.group(1), flags=re.S).strip()
            keys = [re.sub(r'\s+', ' ', part.strip()) for part in key.split(',')]
            if selector in keys:
                found.append(match.group(2))
        return found

    def flex(self, css, selector):
        """The flex shorthand that finally applies to a selector, as numbers."""
        value = None
        for body in self.bodies(css, selector):
            hit = re.search(r'(?:^|;)\s*flex\s*:\s*([^;]+)', body)
            if hit:
                value = hit.group(1).strip()
        if value is None:
            return None
        parts = [part for part in value.split() if part]
        # A one-value `flex:1` means `1 1 0%`.
        if len(parts) == 1:
            return (float(parts[0]), 1.0, 0.0)
        numbers = [float(part.rstrip('%')) for part in parts]
        return tuple(numbers)

    def var(self, css, name):
        hit = re.search(re.escape(name) + r'\s*:\s*([^;]+)', css)
        return hit.group(1).strip() if hit else None

    def short_screen_block(self, css):
        for block in re.findall(r'@media\(max-height:820px\)\{(.*?)\n\}', css, re.S):
            if '--doc-' in block:
                return block
        return ''


    def test_the_timeline_and_the_monitor_share_the_height(self):
        """An even split, not a monitor with a strip under it.

        The timeline used to be sized from the sum of its own fixed tracks, so it
        could never be taller than ~214px while the monitor took everything else.
        Both are now siblings of the page column with an equal zero basis, so they
        split the leftover height evenly at any window size.
        """
        grid = self.flex(self.css, '.editor-grid')
        timeline = self.flex(self.css, '.timeline-panel')
        monitor = self.flex(self.css, '.monitor-panel')
        self.assertIsNotNone(grid)
        self.assertEqual(grid[:2], (1, 1), 'the editor grid must fill its share')
        self.assertEqual(timeline[2], 0, 'the timeline needs a zero basis to split')
        self.assertEqual(monitor[2], 0, 'the monitor needs a zero basis to split')
        # Equal growth from an equal basis is what makes them halves.
        self.assertEqual(timeline[0], monitor[0],
                         'the timeline and the monitor must get the same share')
        share = timeline[0] / (timeline[0] + monitor[0])
        self.assertAlmostEqual(share, 0.50, places=2)
        # Neither side may be capped to a fixed pixel height.
        for selector in ('.timeline-panel', '.monitor-panel', '.editor-grid'):
            for body in self.bodies(self.css, selector):
                self.assertNotRegex(body, r'(?<![a-z-])height:\s*\d+px',
                                    f'{selector} must not hold a fixed height')

    def test_the_label_column_stacks_its_rows(self):
        """The labels must run down the side, one per track.

        `.track-labels` is a flex container by default through its children's
        needs, but without `flex-direction:column` the six label rows laid out
        in a ROW: they bunched along the top while the tracks beside them
        stacked downwards, so every label pointed at the wrong track.
        """
        css = Path('static/editor.css').read_text()
        column = [b for b in re.findall(r'\.track-labels\{[^}]*\}', css) if 'width' in b]
        self.assertTrue(column, 'the label column has no rule')
        body = column[-1]
        self.assertIn('display:flex', body, 'the label column must be a flex container')
        self.assertIn('flex-direction:column', body,
                      'without a column the rows lay out sideways and misalign')
        # And it may not shrink away beside the tracks.
        self.assertRegex(body, r'flex:0 0 \d+px|flex-basis:\d+px')


    def test_every_label_row_fits_inside_its_track(self):
        """A label row is exactly as tall as its track, so its controls must fit.

        The mute buttons were stacked under their labels, which needs ~50px in a
        34-60px row: the column looked jammed and buttons bled into the row
        below. The label and control now share one line, and the row clips.
        """
        css = Path('static/editor.css').read_text()
        labels = [b for b in re.findall(r'\.track-labels\{[^}]*\}', css) if 'width' in b]
        self.assertTrue(labels, 'the label column has no width')
        width = int(re.search(r'width:(\d+)px', labels[-1]).group(1))
        # Wide enough for the busiest row: "Music" + stepper + readout + stepper.
        self.assertGreaterEqual(width, 100, 'the label column must fit the music row')
        self.assertLessEqual(width, 130, 'the label column must not eat the timeline')
        # Controls sit beside their label, not under it.
        rows = re.findall(r'\.track-labels \.track-with-mute\{([^}]*)\}', css)
        self.assertTrue(rows, 'the mute rows have no layout rule')
        self.assertIn('flex-direction:row', rows[-1])
        # A row may never spill into its neighbour.
        self.assertIn('.track-labels>div{min-width:0;min-height:0;overflow:hidden}', css)
        # And the buttons themselves must be single-line.
        buttons = [b for b in re.findall(r'\.track-with-mute button\{[^}]*\}', css) if 'white-space' in b]
        self.assertTrue(buttons)
        self.assertIn('white-space:nowrap', buttons[-1])


    def test_the_tracks_scroll_rather_than_clip(self):
        """A half is finite; the rows inside it must still be reachable."""
        for body in self.bodies(self.css, '#timeline-scroll'):
            if 'overflow' in body:
                self.assertIn('auto', body, 'the tracks must scroll, not clip')
                break
        else:
            self.fail('#timeline-scroll must set an overflow')

    def test_every_stage_grows_with_the_half(self):
        """Rows share the timeline's half by ratio instead of fixed pixel stages."""
        total = 0.0
        for name in ('srt', 'picture', 'source', 'audio', 'music'):
            value = self.var(self.css, f'--doc-{name}-flex')
            self.assertIsNotNone(value, f'--doc-{name}-flex is missing')
            self.assertGreater(float(value), 0, f'--doc-{name}-flex must be positive')
            total += float(value)
        self.assertAlmostEqual(total, 6.0, places=2, msg='the ratios must stay balanced')
        # The picture is still the tallest stage, as it was before.
        self.assertGreater(float(self.var(self.css, '--doc-picture-flex')),
                           float(self.var(self.css, '--doc-audio-flex')))
        # The ruler is the one fixed row: a time ruler gains nothing from height.
        self.assertIn('height:var(--doc-ruler)', self.css)
        # No stage may still be pinned to a pixel height.
        for name, cls in (('caption-track', 'srt'), ('video-track', 'picture'),
                          ('doc-source-track', 'source'), ('audio-track', 'audio'),
                          ('music-track', 'music')):
            for body in self.bodies(self.css, f'.{cls}'):
                self.assertIn('flex:var(--doc-', body,
                              f'.{cls} must size from its ratio')
                self.assertIn('height:auto', body, f'.{cls} must not hold a height')

    def test_no_later_rule_grows_the_panel(self):
        # Any panel height written outside the single sizing block would win
        # again, which is exactly how it kept growing.
        grown = re.findall(r'\.timeline-panel\{[^}]*height:(\d+)px', self.css)
        self.assertEqual(grown, [], f'panel heights set outside the sizing block: {grown}')
        self.assertNotIn('!important', ' '.join(
            m for m in re.findall(r'\.timeline-panel\{[^}]*\}', self.css)))

    def test_the_short_screen_rule_only_tightens_the_ruler(self):
        """A short window must not re-pin the tracks to pixels.

        The old rule set five pixel stages smaller on a short screen. With the
        halves in place those stages would fight the ratios, so the only thing
        that still tightens is the fixed ruler.
        """
        short = self.short_screen_block(self.css)
        self.assertIn('--doc-ruler:16px', short)
        for name in ('srt', 'picture', 'source', 'audio', 'music'):
            self.assertNotIn(f'--doc-{name}:', short,
                             f'--doc-{name} must not go back to a pixel stage')

    def test_the_monitor_never_squeezes_the_timeline_out(self):
        # The monitor now takes the height left over, so the picture shrinks on a
        # short screen instead of pushing the tracks off the bottom.
        panel = re.search(r'\.monitor-panel\{[^}]*min-height:0[^}]*overflow:hidden', self.css)
        self.assertIsNotNone(panel, 'the monitor panel must be allowed to shrink')
        frame = [r for r in re.findall(r'\.monitor\{[^}]*\}', self.css) if 'flex' in r][0]
        # It fills the row rather than holding a fixed ratio, and must still be
        # allowed to shrink so the tracks are never pushed off screen.
        self.assertIn('flex:1 1 auto', frame)
        self.assertIn('min-height:0', frame)
        self.assertNotIn('height:min(', frame)

    def test_the_monitor_fills_down_to_the_shared_border(self):
        # The picture box grew to 16:9 only, so on a tall screen everything below
        # the transport was empty and the monitor looked detached from the
        # timeline. It must now claim the leftover height.
        frame = [r for r in re.findall(r'\.monitor\{[^}]*\}', self.css) if 'flex' in r][0]
        self.assertIn('flex:1 1 auto', frame, 'the picture must take the leftover height')
        self.assertIn('min-height:0', frame)
        self.assertNotIn('aspect-ratio:16/9', frame,
                         'a fixed ratio is what left the dead space above the border')
        # The timeline owns the single border the monitor sits on; no second rule
        # draws another line between them.
        self.assertEqual(len(re.findall(r'\.timeline-panel\{[^}]*border-top:1px solid', self.css)), 1)
        self.assertNotIn('border-bottom:1px solid var(--line)', 
                         re.findall(r'\.monitor-panel\{[^}]*\}', self.css)[-1])

    def test_the_monitor_matches_the_deck_player_layout(self):
        for needle, label in (('--deck-chrome:#262626', 'chrome'),
                              ('--deck-header:#303030', 'header'),
                              ('--deck-tc:#1a89bb', 'playhead colour'),
                              ('--deck-total:#cfcfcf', 'programme length colour')):
            self.assertIn(needle, self.css, f'missing {label} from the monitor palette')
        # The transport is one row: time, tally, play, then the view controls.
        row = re.search(r'\.transport\{[^}]*\}', self.css).group(0)
        self.assertIn('display:flex', row)
        self.assertNotIn('flex-wrap:wrap', row, 'the transport must stay a single row')

    def test_the_monitor_card_shares_one_side_margin(self):
        """The picture and the transport must sit on the same left and right edge.

        The header band was removed, so the picture now rounds the top of the
        card itself. Its side margin still has to match the transport's or the
        frame steps out at the join.
        """
        css = Path('static/editor.css').read_text()
        monitor = [r for r in re.findall(r'\.monitor\{[^}]*\}', css) if 'margin' in r]
        transport = [r for r in re.findall(r'\.transport\{[^}]*\}', css) if 'margin' in r]
        self.assertTrue(monitor and transport)
        # Both inset by the same amount on the sides.
        self.assertIn('10px 10px', monitor[0])
        self.assertIn('0 10px', transport[0])
        # The removed header must not be laid out any more.
        blocks = [b for b in re.findall(r'@media\(max-height:820px\)\{(.*?)\n\}', css, re.S)
                  if '.transport' in b]
        self.assertEqual(len(blocks), 1)
        self.assertNotIn('.monitor-tabs', blocks[0],
                         'the header is hidden and must not be sized')
        self.assertIn('.monitor{margin:8px 8px 0}', blocks[0])

    def test_the_picture_is_not_forced_wider_than_its_frame(self):
        """width:100% plus a side margin overflows the card by the margin."""
        css = Path('static/editor.css').read_text()
        card = [r for r in re.findall(r'\.monitor\{[^}]*\}', css) if 'margin' in r]
        self.assertTrue(card, 'the monitor has no margin rule')
        self.assertNotIn('width:100%', card[0],
                         'the monitor must size to its inset, not to 100%')
        self.assertIn('margin:10px 10px 0', card[0])

if __name__ == '__main__':
    unittest.main()


class ScheduleCacheTest(unittest.TestCase):
    """The playback schedule costs a full source-frame audit to build (~16s).

    Keeping it only in memory meant every server restart paid for it again.
    """

    def test_the_schedule_survives_a_restart(self):
        import tempfile
        from pathlib import Path
        import app as editor_app

        pid = 'cachescheduleprobe'
        events = [{'kind': 'narration', 'timeline_in_ms': 0, 'timeline_out_ms': 100}]
        key = ('v1', 'r1')
        original = editor_app.folder
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'proj'
            root.mkdir()
            editor_app.folder = lambda p: root
            try:
                self.assertIsNone(editor_app._schedule_from_disk(pid, key))
                editor_app._schedule_to_disk(pid, key, events)
                self.assertEqual(editor_app._schedule_from_disk(pid, key), events)
                # A different project version must never be served stale frames.
                self.assertIsNone(editor_app._schedule_from_disk(pid, ('v2', 'r1')))
            finally:
                editor_app.folder = original

    def test_an_unwritable_cache_never_breaks_playback(self):
        import app as editor_app
        original = editor_app.folder

        def boom(_pid):
            raise OSError('disk full')

        editor_app.folder = boom
        try:
            editor_app._schedule_to_disk('x', ('a', 'b'), [])   # must not raise
            self.assertIsNone(editor_app._schedule_from_disk('x', ('a', 'b')))
        finally:
            editor_app.folder = original


class MediaPlaybackTest(unittest.TestCase):
    """Large source clips must start and seek without a full download."""

    def test_an_mp4_with_its_index_last_is_detected(self):
        """A moov atom after the media forces the browser to fetch everything."""
        import struct, tempfile
        from pathlib import Path
        import app as editor_app

        def write_moov_at_end(path, media=b'x' * 4000):
            ftyp = struct.pack('>I', 24) + b'ftyp' + b'isom' + b'\x00' * 12
            mdat = struct.pack('>I', len(media) + 8) + b'mdat' + media
            moov = struct.pack('>I', 16) + b'moov' + b'\x00' * 8
            path.write_bytes(ftyp + mdat + moov)

        def write_fast_start(path, media=b'x' * 4000):
            ftyp = struct.pack('>I', 24) + b'ftyp' + b'isom' + b'\x00' * 12
            moov = struct.pack('>I', 16) + b'moov' + b'\x00' * 8
            mdat = struct.pack('>I', len(media) + 8) + b'mdat' + media
            path.write_bytes(ftyp + moov + mdat)

        with tempfile.TemporaryDirectory() as tmp:
            late = Path(tmp) / 'late.mp4'; write_moov_at_end(late)
            fast = Path(tmp) / 'fast.mp4'; write_fast_start(fast)
            self.assertTrue(editor_app.mp4_moov_at_end(late))
            self.assertFalse(editor_app.mp4_moov_at_end(fast))

    def test_a_missing_or_unreadable_file_is_not_reported_as_slow(self):
        import app as editor_app
        from pathlib import Path
        self.assertFalse(editor_app.mp4_moov_at_end(Path('/nonexistent/none.mp4')))

    def test_source_media_is_cacheable(self):
        """50MB clips re-fetched on every seek were why the monitor hung."""
        import app as editor_app
        client = editor_app.app.test_client()
        projects = list((editor_app.PROJECTS).glob('*/project.json'))
        if not projects:
            self.skipTest('no project on disk')
        pid = projects[0].parent.name
        data = client.get(f'/api/projects/{pid}').get_json()
        video = next((a for a in data.get('assets', []) if a.get('kind') == 'video'), None)
        if not video:
            self.skipTest('no video asset')
        response = client.get(f"/api/projects/{pid}/assets/{video['id']}/file")
        cache = response.headers.get('Cache-Control', '')
        self.assertIn('max-age', cache)
        self.assertNotIn('no-cache', cache)
        # The project JSON must stay uncached: it is the live state.
        self.assertEqual(client.get(f'/api/projects/{pid}').headers.get('Cache-Control'), 'no-store')

    def test_the_narration_mix_is_cached_but_a_download_is_not(self):
        """A 5MB mix re-fetched on every play and seek is a needless stall."""
        import app as editor_app
        client = editor_app.app.test_client()
        projects = list(editor_app.PROJECTS.glob('*/project.json'))
        if not projects:
            self.skipTest('no project on disk')
        state = None
        for path in projects:
            pid = path.parent.name
            data = client.get(f'/api/projects/{pid}/documentary-audio').get_json() or {}
            if data.get('url'):
                state = data; break
        if not state:
            self.skipTest('no rendered narration mix')
        played = client.get(state['url'])
        self.assertIn('immutable', played.headers.get('Cache-Control', ''))
        self.assertNotIn('no-store', played.headers.get('Cache-Control', ''))
        # A download must never be served from, or written to, a shared cache.
        grabbed = client.get(state['url'] + '?download=1')
        self.assertEqual(grabbed.headers.get('Cache-Control'), 'no-store')


    def test_the_uploaded_file_is_never_rewritten(self):
        """Preserving source bytes is an existing promise; the copy is separate."""
        import inspect
        import app as editor_app

        source = inspect.getsource(editor_app.perform_upload)
        self.assertNotIn('make_fast_start(dest)', source,
                         'an upload must not be remuxed in place')

    def test_a_slow_source_is_served_from_a_cached_fast_start_copy(self):
        """The index must reach the player first, or every seek re-reads the file."""
        import app as editor_app, subprocess, imageio_ffmpeg, shutil
        from pathlib import Path

        projects = list(editor_app.PROJECTS.glob('*/project.json'))
        if not projects:
            self.skipTest('no project on disk')
        pid = projects[0].parent.name
        client = editor_app.app.test_client()
        data = client.get(f'/api/projects/{pid}').get_json()
        video = next((a for a in data.get('assets', [])
                      if a.get('kind') == 'video'
                      and str(a.get('path', '')).endswith('.mp4')), None)
        if not video:
            self.skipTest('no mp4 asset')
        root = editor_app.folder(pid)
        live = root / video['path']
        if not live.exists():
            self.skipTest('media missing')
        backup = live.with_suffix('.testbak')
        shutil.copy2(live, backup)
        try:
            slow = live.with_suffix('.slow.mp4')
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-y',
                            '-i', str(backup), '-c', 'copy', str(slow)], check=True)
            live.unlink(); slow.replace(live)
            if not editor_app.mp4_moov_at_end(live):
                self.skipTest('this ffmpeg writes fast-start by default')
            cached = root / '.cache/faststart' / (video['id'] + '.mp4')
            cached.unlink(missing_ok=True)
            response = client.get(f"/api/projects/{pid}/assets/{video['id']}/file",
                                  headers={'Range': 'bytes=0-999'})
            self.assertEqual(response.status_code, 206)
            self.assertTrue(cached.exists(), 'a fast-start copy must be produced')
            self.assertFalse(editor_app.mp4_moov_at_end(cached),
                             'the served copy must have its index first')
            self.assertTrue(editor_app.mp4_moov_at_end(live),
                            'the original must be left as it was')
        finally:
            if backup.exists():
                live.unlink(missing_ok=True); backup.replace(live)
            (root / '.cache/faststart' / (video['id'] + '.mp4')).unlink(missing_ok=True)


class PlaybackResilienceTest(unittest.TestCase):
    """A timeline must survive a dropped request, and a beat its own rounding."""

    def test_one_failed_verification_is_retried_not_fatal(self):
        """A single blip used to stop playback until the page was reloaded."""
        from pathlib import Path
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index("setInterval(async()=>{const d=window.docTimelineData")
        body = js[start:js.index('},5000);', start)]
        self.assertIn('verifyFailures', body)
        self.assertRegex(body, r'verifyFailures\s*>=\s*3',
                         'only repeated failures may mark the timeline broken')

    def test_a_stream_without_a_frame_count_falls_back_to_duration(self):
        """S11.mp4 declared no frame count, which disabled the rounding fix."""
        from pathlib import Path
        source = Path('documentary_render_plan.py').read_text()
        self.assertIn('if not available and container.duration:', source)
        self.assertIn('average_rate', source)


class EncodedRateCheckTest(unittest.TestCase):
    """An export must not be blocked by a correct clip.

    average_rate is frames/duration and drifts off the nominal rate whenever the
    duration does not divide exactly -- a clip trimmed to its own frame count is
    short by one frame, which read as 29.9849 instead of 30000/1001 and failed
    every export at the first scene.
    """

    @classmethod
    def setUpClass(cls):
        try:
            import av, imageio_ffmpeg, subprocess  # noqa: F401
        except Exception:
            raise unittest.SkipTest('av/ffmpeg unavailable')
        import subprocess, tempfile, imageio_ffmpeg
        cls.tmp = tempfile.TemporaryDirectory()
        cls.ff = imageio_ffmpeg.get_ffmpeg_exe()
        cls.src = str(Path(cls.tmp.name) / 'src60.mp4')
        subprocess.run([cls.ff, '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'testsrc=size=1280x720:rate=60000/1001:duration=40',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        cls.src], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _encode(self, name, rate, frames='1007', source=None):
        import subprocess
        out = str(Path(self.tmp.name) / name)
        filters = (f"trim=start_frame=0:end_frame={int(frames) * 2},setpts=N/({rate}*TB),"
                   f"scale=1280:720,setsar=1,format=yuv420p")
        subprocess.run([self.ff, '-v', 'error', '-y', '-i', source or self.src, '-an', '-vf', filters,
                        '-fps_mode', 'passthrough', '-c:v', 'libx264', '-preset', 'ultrafast',
                        '-frames:v', frames, out], check=True)
        return out

    def test_a_correct_ntsc_clip_is_not_rejected(self):
        """The exact case that killed the export at 5%."""
        from fractions import Fraction
        from documentary_final_renderer import _video_info
        clip = self._encode('ntsc.mp4', '30000/1001')
        count, rate = _video_info(clip)
        self.assertEqual(count, 1007, 'frame count must be exact')
        self.assertEqual(rate, Fraction(30000, 1001),
                         'the nominal rate is the timebase, not frames/duration')

    def test_a_wrong_rate_is_still_rejected(self):
        """Relaxing the check must not let a genuinely wrong clip through.

        The source must itself be 30fps: re-timing a 59.94 source with setpts
        keeps the 30000/1001 container timebase, which is not what the renderer
        produces when it is asked for a different output rate.
        """
        import subprocess
        from fractions import Fraction
        from documentary_final_renderer import _video_info
        src30 = str(Path(self.tmp.name) / 'src30.mp4')
        subprocess.run([self.ff, '-v', 'error', '-y', '-f', 'lavfi',
                        '-i', 'testsrc=size=1280x720:rate=30:duration=10',
                        '-c:v', 'libx264', '-preset', 'ultrafast', '-pix_fmt', 'yuv420p',
                        src30], check=True)
        wrong = self._encode('p30.mp4', '30', frames='300', source=src30)
        _, rate = _video_info(wrong)
        self.assertNotEqual(rate, Fraction(30000, 1001),
                            'a 30fps clip is not an NTSC clip')


class PreRollWindowTest(unittest.TestCase):
    """The incoming picture must be loaded before the cut, not after it.

    A source clip is tens of MB and sits on a keyframe interval of several
    seconds, so it needs real time on the standby element. At 1.5s the picture
    arrived after the cut and the monitor was blank while narration continued.
    """

    def test_the_pre_roll_window_covers_a_real_clip_load(self):
        from pathlib import Path
        js = Path('static/documentary_timeline.js').read_text()
        match = __import__('re').search(r'const DOC_PREROLL_S=([0-9.]+)', js)
        self.assertTrue(match, 'the pre-roll window is not declared')
        window = float(match.group(1))
        self.assertGreaterEqual(window, 5,
                                'a 40MB clip needs more than a couple of seconds')

    def test_the_pre_roll_still_targets_the_upcoming_cut(self):
        """A wider window must not pre-load a cut further ahead than the next."""
        from pathlib import Path
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index('function docTick(){')
        body = js[start:start + 1200]
        self.assertIn('docPremoteNext(t)', body)
        self.assertIn('docNextAt(t)', body)


class JoinedTimelineTimestampTest(unittest.TestCase):
    """The final join must lay frames out on one exact grid.

    Clips cut from a 59.94 fps source do not encode to exactly the number of
    frames the plan asked for, so a plain `-c copy` concat inherits a sub-frame
    deficit at every such cut. Left alone that slides every later timestamp and
    the export is rejected by its own timestamp audit.
    """

    def setUp(self):
        import documentary_final_renderer
        self.source = Path(documentary_final_renderer.__file__).read_text()

    def test_concat_rebuilds_pts_from_the_frame_index(self):
        start = self.source.index("joined = work / 'joined.mp4'")
        body = self.source[start:start + 1400]
        self.assertIn('setts=pts=N*{step}:dts=N*{step}', body,
                      'the join must rebuild timestamps from the frame index')
        self.assertIn('-c', body)
        self.assertIn("'copy'", body,
                      'rebuilding timestamps must not force a re-encode')

    def test_timescale_is_the_rate_numerator(self):
        # One frame must be exactly `step` ticks, which only holds when the
        # timescale is the rate's numerator.
        self.assertIn('timescale = fps.numerator', self.source)
        self.assertIn('step = fps.denominator', self.source)

    def test_join_frame_grid_is_exact(self):
        from fractions import Fraction
        fps = Fraction(30000, 1001)
        timescale, step = fps.numerator, fps.denominator
        self.assertEqual(Fraction(step, timescale), 1 / fps)
        for n in (1, 1007, 9544, 22319, 26186):
            self.assertEqual(Fraction(n * step, timescale), Fraction(n, 1) / fps)

    def test_mixed_source_rates_share_one_timescale(self):
        # A 59.94 fps source must still be pinned to the output grid.
        self.assertIn("'-video_track_timescale', str(fps.numerator)", self.source)


class InspectVideoRateSourceTest(unittest.TestCase):
    """`average_rate` is frames over duration, not the layout rate."""

    def test_inspect_video_prefers_base_rate(self):
        source = Path('strict_cuts.py').read_text()
        start = source.index('def inspect_video(')
        body = source[start:start + 2000]
        self.assertIn('stream.base_rate or stream.average_rate', body,
                      'timestamp spacing must be checked against the declared rate')


class PlanningToolsPlacementTest(unittest.TestCase):
    """Project-wide planning links belong in Setup, not the shot inspector.

    The shot inspector edits one shot. A block of project-wide links there
    competed with that job and pushed the shot's own fields down the panel.
    """

    def setUp(self):
        self.editor = Path('static/editor.js').read_text()
        self.studio = Path('static/studio.js').read_text()

    def test_shot_inspector_renders_no_planning_group(self):
        start = self.editor.index('function planToolsHTML()')
        body = self.editor[start:start + 200]
        self.assertIn("return ''", body,
                      'the shot inspector must not render planning links')

    def test_frame_check_lives_in_the_setup_panel(self):
        self.assertIn('<h3>Frame check</h3>', self.studio)
        self.assertIn('openFrameQC(1)', self.studio)

    def test_unbuildable_picture_lists_are_gone(self):
        # Shot index and Feasibility both read reviews/Full_source_analysis.json,
        # which a documentary project never writes, so they could never fill in.
        self.assertNotIn('openPlanDialog', self.studio)
        self.assertNotIn('openPlanDialog', self.editor)

    def test_map_template_only_offered_when_a_map_is_needed(self):
        # Downloading a blank map when a script already drives the cut is noise.
        index = self.studio.index('downloadMapTemplate()')
        self.assertIn("status==='needs_plan'", self.studio[max(0, index - 200):index])

    def test_approve_all_keeps_its_delivery_action(self):
        self.assertIn('onclick="approveReadyScenes()"', self.studio)

    def test_setup_cards_are_numbered_uniquely(self):
        import re
        steps = re.findall(r'setup-step">(\d+)</span><h3>([^<]+)', self.studio)
        numbers = [n for n, _ in steps]
        self.assertEqual(len(numbers), len(set(numbers)),
                         f'duplicate setup step numbers: {steps}')


class SidebarContentsTest(unittest.TestCase):
    """Both sidebars show only things that are true and actionable."""

    def setUp(self):
        self.editor = Path('static/editor.js').read_text()
        self.template = Path('templates/index.html').read_text()
        self.studio = Path('static/studio.js').read_text()

    def test_media_pool_has_one_hint_element(self):
        # Two elements shared `.media-hint`, and the code wrote through the class
        # selector, so the footage-count line was overwritten on every render
        # while the real hint stayed empty forever.
        self.assertEqual(self.template.count('class="media-hint"'), 1)

    def test_no_write_through_the_media_hint_class(self):
        self.assertNotIn("querySelector('.media-hint')", self.editor)

    def test_footage_count_line_hidden_when_nothing_is_missing(self):
        index = self.editor.index("footage-requirements-pool")
        body = self.editor[index:index + 700]
        self.assertIn('hidden', body,
                      'a fully matched pool should not spend a line saying so')

    def test_checks_card_is_not_called_pre_render(self):
        # It is on screen at all times, including after a successful export.
        self.assertNotIn('Pre-render check', self.template)

    def test_documentary_hides_unused_candidate_list(self):
        index = self.editor.index('async function candidateHTMLForScene')
        body = self.editor[index:index + 700]
        self.assertIn("edit_mode==='documentary'", body)

    def test_source_understanding_names_a_real_control(self):
        index = self.editor.index('No local analysis yet')
        text = self.editor[index:index + 160]
        self.assertNotIn('Production setup', text,
                         'that is not a control the reader can find')
        self.assertIn('Case footage', text)

    def test_script_in_line_only_when_it_differs(self):
        index = self.editor.index('function sourceTimingHTML')
        body = self.editor[index:index + 2000]
        self.assertIn('Script asked for', body)
        self.assertNotIn('<p>Script IN: ', body)


class AudioLevelsTest(unittest.TestCase):
    """The three faders must be safe when absent, exact when used, and isolated."""

    def test_absent_levels_render_exactly_as_before(self):
        from documentary_audio import audio_levels
        for project in ({}, {'documentary_audio_levels': {}}):
            self.assertEqual(audio_levels(project),
                             {'narration_percent': 100, 'clip_percent': 100})

    def test_nonsense_levels_fall_back_to_100(self):
        from documentary_audio import audio_levels
        self.assertEqual(
            audio_levels({'documentary_audio_levels': {'narration_percent': 'x', 'clip_percent': True}}),
            {'narration_percent': 100, 'clip_percent': 100})

    def test_levels_are_clamped_to_the_allowed_range(self):
        from documentary_audio import audio_levels
        got = audio_levels({'documentary_audio_levels': {'narration_percent': 900, 'clip_percent': -40}})
        self.assertEqual(got, {'narration_percent': 150, 'clip_percent': 0})

    def test_saving_one_fader_never_sends_the_others(self):
        # Sending all three let a stale in-memory music value overwrite the saved
        # bed level, which silently reduced the music to 1%.
        editor = Path('static/editor.js').read_text()
        start = editor.index('async function saveDocVolume')
        body = editor[start:start + 900]
        self.assertIn('keyName', body)
        self.assertNotIn('narration_percent: docTrackVolume.narration', body)
        self.assertNotIn('volume_percent: Math.max(1, docTrackVolume.music)', body)

    def test_export_applies_the_quieter_speech_level(self):
        audio = Path('documentary_audio.py').read_text()
        self.assertIn("min(levels_config['narration_percent'],levels_config['clip_percent'])", audio)

    def test_levels_are_reported_in_the_render_result(self):
        audio = Path('documentary_audio.py').read_text()
        self.assertIn("'applied_speech_gain'", audio)

    def test_volume_control_sits_under_scale_in_the_inspector(self):
        editor = Path('static/editor.js').read_text()
        scale = editor.index('slot-scale-control')
        volumes = editor.index('docVolumeHTML()')
        self.assertGreater(volumes, scale, 'audio levels belong under the scale control')

    def test_three_faders_in_the_documented_order(self):
        editor = Path('static/editor.js').read_text()
        start = editor.index('function docVolumeHTML')
        body = editor[start:start + 1600]
        narration = body.index("'Narration'")
        clip = body.index("'Clip audio'")
        music = body.index("'Music'")
        self.assertLess(narration, clip)
        self.assertLess(clip, music)


class BackgroundMusicTest(unittest.TestCase):
    """The bed has to reach the browser, and be replaced when the file changes."""

    def setUp(self):
        self.timeline = Path('static/documentary_timeline.js').read_text()
        self.studio = Path('static/studio.js').read_text()
        self.app = Path('app.py').read_text()

    def test_music_url_does_not_come_from_the_timeline_payload(self):
        # It used to read `d.music_url`, which the server never sent, so the layer
        # attached nothing and paused on the first line of every sync.
        start = self.timeline.index('function docSyncMusic(')
        # The function reports a bad URL on its own first line, so the window has
        # to reach past the early return.
        body = self.timeline[start:start + 2400]
        # Match executable code, not the comment that explains the old bug.
        self.assertNotIn('?.music_url', body)
        self.assertNotIn("d.music_url", body)
        self.assertIn('docMusic.url', body,
                      'the bed URL comes from the music settings')

    def test_looping_bed_follows_the_wrapped_clock(self):
        start = self.timeline.index('function docSyncMusic(')
        body = self.timeline[start:start + 2000]
        self.assertIn('want%length', body,
                      'a looping bed must be compared on the wrapped clock')

    def test_music_file_is_not_cached_immutable(self):
        # It is served from one fixed path and replaced in place, so an immutable
        # cache kept the previous file playing for a year after a replacement.
        start = self.app.index('def cache(response):')
        body = self.app[start:start + 1400]
        self.assertIn('music', body)
        self.assertIn('max-age=604800', body)
        self.assertIn('and not music', body)

    def test_music_url_carries_a_version(self):
        start = self.app.index('def _music_state(')
        body = self.app[start:start + 900]
        self.assertIn('?v=', body,
                      'a replaced bed must be a different URL to bypass caches')

    def test_upload_and_read_share_one_reply_shape(self):
        start = self.app.index('def _music_state(')
        tail = self.app[start:]
        upload = tail.index('def upload_documentary_music')
        self.assertIn('_music_state(p,root)', tail[upload:upload + 900],
                      'the upload reply must report name and url, not just settings')
        for key in ("state['name']", "state['size']", "state['duration_s']"):
            self.assertIn(key, tail[:upload])

    def test_music_card_names_the_saved_file(self):
        self.assertIn("docMusicCard.name", self.studio)

    def test_a_new_upload_leaves_one_bed_behind(self):
        # A second upload must replace the first outright, including across file
        # extensions: the extension fallback in `music_path` will happily pick up
        # an old `background_music.wav` when the new bed arrived as `.mp3`.
        music = Path('documentary_music.py').read_text()
        start = music.index('def save_upload(')
        body = music[start:music.index('\n\ndef ', start)]
        self.assertIn("temp.replace(keep)", body)
        self.assertIn("glob('background_music.*')", body)
        # The sweep has to run after the replacement, or it deletes the upload.
        self.assertLess(body.index('temp.replace(keep)'), body.index("glob('background_music.*')"),
                        'stale beds are swept only once the new one is in place')
        self.assertIn("keep.name", body, 'the sweep must spare the file just written')
        self.assertIn("'background_music.name'", body,
                      'the sweep must spare the sidecar that points at the new bed')

    def test_no_explanatory_sentences_on_the_music_card(self):
        start = self.studio.index('Background music or SFX')
        body = self.studio[start:start + 1000]
        self.assertNotIn('A sound bite always carries the only audio', body)
        self.assertNotIn('the same way narration pauses', body)


class AudioFaderLabelsOnlyTest(unittest.TestCase):
    def test_volume_panel_carries_no_sentences(self):
        editor = Path('static/editor.js').read_text()
        start = editor.index('function docVolumeHTML')
        end = editor.index('function previewDocVolume')
        body = editor[start:end]
        for sentence in ('matched to the narration first',
                         'The bed under the film',
                         'still dips or stops',
                         'Preview reflects narration'):
            self.assertNotIn(sentence, body)
        for label in ('Narration', 'Clip audio', 'Music'):
            self.assertIn(label, body)
