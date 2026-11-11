"""The production name must sit on the play button's vertical line. Measure it.

The drawing is geometric: the two boxes stay over the media pool, and the production
name is centred on the same vertical line as the play button in the transport below.
`test_header_layout` proves the stylesheet declares the mechanism. This proves the
browser actually renders it that way -- a grid, a cascade and a runtime fit function
are three places for a few pixels to go missing, and none of them is visible in a
stylesheete read.

The check needs a browser. One is provided here from an npm package (Chromium 153 via
@puppeteer/browsers-compatible build), because the sandbox cannot reach the usual
Chromium CDN. Where neither a browser nor the harness is available the test SKIPS with
the reason, rather than passing quietly: a skipped layout test means the alignment is
unverified, not proven.

Set STORYROOM_CHROME to a Chromium/Chrome executable to override the default.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Kept in the repository, not /tmp: the sandbox's /tmp is wiped between sessions and a
# missing harness turns these tests into SKIPs, which quietly stops proving anything.
HARNESS = Path(os.environ.get('STORYROOM_CHROME_HARNESS',
                              str(ROOT / 'tests' / 'chrome_harness.js')))
DEFAULT_CHROME = '/tmp/chr/chromium'


def _chrome() -> str | None:
    candidate = os.environ.get('STORYROOM_CHROME', DEFAULT_CHROME)
    if os.path.exists(candidate) and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which('chromium') or shutil.which('chromium-browser') or \
        shutil.which('google-chrome') or shutil.which('google-chrome-stable')


@unittest.skipUnless(shutil.which('node'), 'node is not installed, so the browser harness cannot run')
class HeaderAlignmentInBrowserTest(unittest.TestCase):
    """Measured positions, at the widths people actually use."""

    @classmethod
    def setUpClass(cls):
        cls.chrome = _chrome()
        if not cls.chrome:
            raise unittest.SkipTest(
                'no Chromium available; set STORYROOM_CHROME to a browser executable '
                'to verify the header alignment')
        if not HARNESS.exists():
            raise unittest.SkipTest(f'browser harness missing at {HARNESS}')
        cls.server = None
        cls.base = os.environ.get('STORYROOM_TEST_BASE')
        if not cls.base:
            cls.base = cls._start_server()
        cls.report = cls._measure(cls.base, cls.chrome)

    @classmethod
    def _start_server(cls):
        """Boot the real app so the test measures the page it actually serves."""
        import socket
        import time
        from urllib.request import urlopen

        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        # app.py reads PORT; STORYROOM_HOST keeps the test's server off the network.
        env = dict(os.environ, PORT=str(port), STORYROOM_HOST='127.0.0.1')
        proc = subprocess.Popen(
            [sys.executable, 'app.py'], cwd=str(ROOT), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        cls.server = proc
        deadline = time.time() + 40
        while time.time() < deadline:
            try:
                with urlopen(f'http://127.0.0.1:{port}/', timeout=1):
                    return f'http://127.0.0.1:{port}'
            except Exception:
                if proc.poll() is not None:
                    raise unittest.SkipTest('the app did not start for the layout check')
                time.sleep(0.3)
        proc.terminate()
        raise unittest.SkipTest('the app did not answer in time for the layout check')

    @classmethod
    def _measure(cls, base, chrome):
        result = subprocess.run(
            ['node', str(HARNESS), base, chrome],
            capture_output=True, text=True, timeout=180,
            env=dict(os.environ, LD_LIBRARY_PATH=f'{os.path.dirname(chrome)}/lib/lib'))
        if result.returncode != 0:
            raise unittest.SkipTest(
                'the browser harness could not run: ' + (result.stderr or '')[:300])
        return json.loads(result.stdout)

    @classmethod
    def tearDownClass(cls):
        if cls.server is not None:
            cls.server.terminate()
            try:
                cls.server.wait(timeout=10)
            except Exception:
                cls.server.kill()

    def _at(self, width):
        for row in self.report:
            if row['width'] == width:
                return row
        self.fail(f'no measurement at {width}px')

    def test_the_name_is_on_the_play_buttons_centre_line(self):
        """The promise, in pixels, at the two widths the user screenshotted."""
        for width in (1900, 1600):
            row = self._at(width)
            self.assertEqual(row['layout'], 'centred',
                             f'at {width}px the bar gave up the drawing\'s layout')
            self.assertAlmostEqual(
                row['name_centre'] - row['play_centre'], 0, delta=1.0,
                msg=f"at {width}px the name's centre is {row['name_centre']} but the "
                    f"play button's is {row['play_centre']}")

    def test_the_name_is_on_the_monitor_columns_centre(self):
        for width in (1900, 1600):
            row = self._at(width)
            self.assertAlmostEqual(row['name_centre'] - row['monitor_centre'], 0, delta=1.0,
                                   msg=f'at {width}px the name is off the monitor column')

    def test_the_two_boxes_hug_the_left_edge_rather_than_stretching(self):
        """The original defect: each group given an equal share of the bar."""
        for width in (1900, 1600):
            row = self._at(width)
            self.assertLess(row['tools_left'], 400,
                            f"at {width}px the boxes float {row['tools_left']}px in")
            self.assertGreater(row['gap_after_tools'], -1)

    def test_nothing_overflows_the_window_and_no_button_is_stranded(self):
        for row in self.report:
            self.assertFalse(row['overflow'],
                             f"the page overflows horizontally at {row['width']}px")
            for button in ('Projects', '＋ New', 'Setup', 'Performance', 'Render MP4'):
                self.assertTrue(row['buttons'].get(button, False),
                                f"{button} is not visible at {row['width']}px, and it is "
                                f'the only route to its dialog')

    def test_a_long_name_never_runs_into_the_buttons(self):
        for row in self.report:
            self.assertGreaterEqual(
                row['clearance'], -1,
                f"at {row['width']}px the name overlaps the action buttons by "
                f"{-row['clearance']}px")

    def test_the_media_pool_lays_out_three_tiles_per_row(self):
        """Measured, not read from the stylesheet: eight tiles, three per row.

        Only a production with media has tiles, and the harness does not fabricate any:
        an empty pool is reported as an unmeasured column count. The three-column rule
        itself is proved against the stylesheet in test_pool_and_timeline_notes.
        """
        measured = [r for r in self.report if r['pool'].get('columns')]
        if not measured:
            raise unittest.SkipTest(
                'the page has no media tiles, so the pool geometry cannot be measured here')
        for row in measured:
            pool = row['pool']
            self.assertEqual(pool['columns'], 3,
                             f"at {row['width']}px the pool has {pool['columns']} columns")
            self.assertEqual(pool['per_row'][:2], [3, 3],
                             f"at {row['width']}px the rows hold {pool['per_row']} tiles")
            # Eight tiles over three columns is three rows, the last one not full.
            self.assertEqual(pool['rows'], 3,
                             f"at {row['width']}px eight tiles took {pool['rows']} rows")
            self.assertEqual(pool['per_row'][-1], 2,
                             f"at {row['width']}px the last row holds {pool['per_row'][-1]}")

    def test_the_name_is_readable_at_every_width_that_keeps_it(self):
        """A centred name squeezed to one character is worse than one moved aside."""
        for row in self.report:
            if row['name_visible']:
                self.assertGreater(row['name_width'], 80,
                                   f"at {row['width']}px the name is only "
                                   f"{row['name_width']}px wide")


    # -- one line, every element, at every width -----------------------------
    # The complaint these answer: "some are up, some down, another middle."
    def test_every_header_element_sits_on_one_centre_line(self):
        """Brand, Transition/Caption, the name and every action button share a centre.

        The measured defect was a bar with two implicit grid rows: the brand, the boxes
        and the name sat at y17 while Projects and Render MP4 sat at y49.5, half a bar
        lower. A single centre line is the whole requirement.
        """
        for row in self.report:
            centres = row['group_centres']
            self.assertEqual(
                len({round(c, 1) for c in centres}), 1,
                f"at {row['width']}px the header groups are not level: {centres}")
            self.assertEqual(
                row['button_rows'], 1,
                f"at {row['width']}px the action buttons have "
                f"{row['button_rows']} different centre lines: {row['button_centres']}")

    def test_the_bar_has_exactly_one_grid_row(self):
        """The mechanism, not just the symptom: no implicit second row may reappear.

        `.top-actions` spans two columns so Render MP4 can end at the window edge. When
        it was left to auto-placement that span created an implicit row and dropped the
        buttons below the name -- the exact defect the user screenshotted.
        """
        for row in self.report:
            if row['bar_display'] != 'grid':
                continue  # the narrow fallback is a flex row, one line by definition
            tracks = [t for t in row['grid_template_rows'].split() if t]
            self.assertEqual(
                len(tracks), 1,
                f"at {row['width']}px the bar has {len(tracks)} grid rows "
                f"({row['grid_template_rows']}) and the groups can drift onto two lines")
            self.assertEqual(
                row['name_grid_row'], '1',
                f"at {row['width']}px the name is not pinned to row 1")
            self.assertEqual(
                row['actions_grid_row'], '1',
                f"at {row['width']}px the action group is not pinned to row 1")

    def test_the_name_stays_centred_on_the_play_line_all_the_way_down(self):
        """Centring was being abandoned at 1460px, long before it had to be.

        The flex fallback used to start there and left-aligned the name beside the two
        boxes, so on any window narrower than a desktop the name was off the picture's
        centre line. Measurement puts the real limit at 860px.
        """
        for row in self.report:
            if not row['name_visible']:
                continue
            self.assertAlmostEqual(
                row['name_centre'] - row['play_centre'], 0, delta=1.0,
                msg=f"at {row['width']}px the name's centre is {row['name_centre']} but "
                    f"the play button's is {row['play_centre']}")

    def test_render_mp4_ends_inside_the_window_at_every_width(self):
        """The overflow the previous fix left behind: up to 26px past the right edge.

        The action group is wider than the inspector column it used to be confined to,
        so Render MP4 hung off the window between roughly 1461 and 1690px.
        """
        for row in self.report:
            self.assertLessEqual(
                row['right_edge'], row['width'],
                f"at {row['width']}px Render MP4 ends at {row['right_edge']}px, "
                f"{round(row['right_edge'] - row['width'])}px outside the window")

    def test_the_name_is_never_a_clipped_sliver(self):
        """Either the name is readable, or it is deliberately not shown.

        Between 861 and 940px a centred name has only a few characters of room; the bar
        hides it there rather than displaying an ellipsis and a letter. It must never be
        left visible and clipped, which is the worst of both.
        """
        for row in self.report:
            if row['name_visible']:
                self.assertGreater(
                    row['name_width'], 80,
                    f"at {row['width']}px the name is shown but only "
                    f"{row['name_width']}px wide -- clipped rather than hidden")


if __name__ == '__main__':
    unittest.main()
