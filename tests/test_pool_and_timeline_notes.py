"""The pool is a three-up contact sheet, and the timeline carries no raw diagnostics.

Three things the user asked for, after looking at a documentary workspace:

* the media pool puts exactly three tiles on a row and wraps, rather than changing
  its column count with the pane's width;
* the picture track says nothing about internal conditions -- the old note printed
  "DOCUMENTARY TIMELINE FAILED · Narration duration must be a time in seconds." in
  capitals across the track for as long as the project stayed in that state;
* the same sentence must not appear in the middle of the monitor either, which is
  where a second copy of it lived, drawn as the preview's body text.

The tests read the stylesheet and the scripts, because each of these is a rule about
what may be rendered. Where a claim is about the rendered page, `test_header_alignment`
already establishes that a browser can be driven here; the geometry of the pool is
checked there rather than by re-parsing CSS.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CSS = (ROOT / 'static' / 'editor.css').read_text(encoding='utf-8')
TIMELINE_JS = (ROOT / 'static' / 'documentary_timeline.js').read_text(encoding='utf-8')
EDITOR_JS = (ROOT / 'static' / 'editor.js').read_text(encoding='utf-8')
APP = (ROOT / 'app.py').read_text(encoding='utf-8')


def _blocks(selector: str, css: str = CSS):
    """Every rule body for a selector, in source order, media query or not."""
    found = []
    for match in re.finditer(r'([^{}]*?)\{([^{}]*)\}', css):
        selectors, body = match.group(1), match.group(2)
        # A rule's prefix often carries the explanatory comment that documents it, and
        # the block may be split across lines, so strip comments and collapse space
        # before comparing. `.media-list{display:grid...` was matching nothing otherwise.
        selectors = re.sub(r'(?s)/\*.*?\*/', ' ', selectors)
        flat = ' '.join(selectors.split())
        if flat == selector or any(' '.join(part.split()) == selector
                                   for part in flat.split(',')):
            found.append(body)
    return found


def _last_declared_columns(css: str = CSS) -> str:
    bodies = [b for b in _blocks('.media-list', css) if 'grid-template-columns' in b]
    assert bodies, 'the media pool has no grid template at all'
    return re.search(r'grid-template-columns:\s*([^;}]+)', bodies[-1]).group(1).strip()


class PoolGridTest(unittest.TestCase):
    def test_exactly_three_tiles_per_row(self):
        """`repeat(3, ...)`, not `auto-fill`.

        An auto-fill grid picks its column count from the pane's width, so the pool
        showed two tiles on some widths and three on others, and a row could end with
        one lonely tile. Three fixed tracks give every row the same shape.
        """
        columns = _last_declared_columns()
        self.assertIn('repeat(3,', columns.replace(' ', ''),
                      f'the pool does not pin three columns ({columns!r})')
        self.assertNotIn('auto-fill', columns,
                         'auto-fill lets the column count drift with the pane width')
        self.assertNotIn('auto-fit', columns, 'auto-fit collapses empty tracks')

    def test_each_column_may_shrink_so_names_ellipsise(self):
        """`minmax(0, 1fr)`, not `1fr`: a long filename must not widen its column.

        A plain `1fr` track has an automatic minimum of its content, so one tile with
        a long name can push the three columns wider than the pane and force the pool
        to scroll sideways.
        """
        columns = _last_declared_columns().replace(' ', '')
        # `repeat(3, minmax(0, 1fr))` is ONE repeated track that yields three columns,
        # so the minimum is asserted inside the repeat rather than counted three times.
        self.assertRegex(columns, r'repeat\(3,minmax\(0,pp?[a-z]*\)\)'.replace('pp?[a-z]*', '1fr'),
                         f'the repeated track has no zero minimum ({columns!r})')

    def test_the_tiles_wrap_rather_than_scrolling_sideways(self):
        bodies = _blocks('.media-list')
        self.assertTrue(any('display:grid' in b for b in bodies))
        # A grid wraps by default; what would break wrapping is a fixed row count.
        for body in bodies:
            self.assertNotIn('grid-auto-flow:column', body.replace(' ', ''),
                             'tiles would run sideways instead of wrapping to a new row')

    def test_the_older_tile_template_is_not_what_draws_the_pool(self):
        """Two pool templates have existed; only one still renders."""
        self.assertIn('pool-tile', EDITOR_JS)
        self.assertNotIn('media-tile', EDITOR_JS,
                         'the retired template is still being rendered somewhere')


class TimelineDiagnosticsTest(unittest.TestCase):
    """A track is part of the film's surface, not a log file."""

    def test_the_picture_track_never_prints_the_failure_sentence(self):
        self.assertNotIn('DOCUMENTARY TIMELINE FAILED', TIMELINE_JS)
        self.assertNotIn('DOCUMENTARY TIMELINE UNAVAILABLE', TIMELINE_JS)

    def test_the_track_still_says_something_when_it_is_empty(self):
        """The reason the failure text existed: an empty track must not look like a
        load that never finished. Removing the sentence must not remove that."""
        self.assertIn('No picture track yet', TIMELINE_JS)
        self.assertIn('Loading picture track', TIMELINE_JS)

    def test_the_tracks_copy_does_not_interpolate_the_detail(self):
        """`caption-note` text must not be built from `d.detail`."""
        for match in re.finditer(r'caption-note[^`\'"]{0,80}', TIMELINE_JS):
            self.assertNotIn('detail', match.group(0),
                             'the track note still prints the raw diagnostic')

    def test_the_diagnosis_is_kept_somewhere_a_report_can_find_it(self):
        """Removed from the film's surface, not from the program."""
        self.assertIn("console.warn('[documentary timeline]'", TIMELINE_JS)
        self.assertIn("prepare.title=d.detail", TIMELINE_JS)

    def test_the_monitor_does_not_put_the_diagnostic_under_its_heading(self):
        """The monitor drew `d.detail` as its body text, which put the same sentence
        in the middle of the window. A failure now gets one plain line instead."""
        self.assertIn('is not ready to preview yet', TIMELINE_JS)
        # `d.detail` may still be shown, but only on the prepared path.
        self.assertIn('(ready&&d?.detail)', TIMELINE_JS)

    def test_no_internal_condition_names_reach_visible_copy(self):
        """The product constraint: internal identifiers stay out of user-facing text.

        Scoped to the copy this change is responsible for -- the monitor's explanation
        and the track's note. A blanket scan of every string in the file would flag
        query strings like `?v=${a.revision}`, which are URLs rather than sentences.
        """
        monitor_copy = [
            'This project is not ready to preview yet. Prepare it again after its '
            'narration and timeline are complete.',
            'No picture track yet',
            'Loading picture track…',
            'Prepare a timeline preview to play the sound bite with your narration.',
        ]
        for sentence in monitor_copy:
            self.assertIn(sentence, TIMELINE_JS, f'missing copy: {sentence!r}')
            for term in ('status', 'detail', 'revision', 'error', 'failed'):
                self.assertNotIn(term, sentence.lower(),
                                 f'{term!r} appears in user-facing copy: {sentence!r}')


class NarrationUploadGuardTest(unittest.TestCase):
    """A narration whose length cannot be read must be refused at upload.

    Storing duration 0 makes the file look present but zero seconds long. Every later
    step that builds an edit clock then rejects it, which is how a project ends up
    permanently showing "Narration duration must be a time in seconds." instead of
    offering a way forward.
    """

    def _voiceover_block(self):
        start = APP.index("kind=='voiceover'")
        end = APP.index("kind=='asset'", start)
        return APP[start:end]

    def test_an_unreadable_length_is_refused(self):
        block = self._voiceover_block()
        self.assertIn("if not info['duration']", block.replace('"', "'"),
                      'a narration file with no readable length is still stored')
        self.assertIn('could not be read', block)

    def test_the_file_is_removed_when_it_is_refused(self):
        block = self._voiceover_block()
        guard = block[block.index("if not info['duration']"):]
        self.assertIn('dest.unlink()', guard.split('raise')[0],
                      'the rejected upload is left on disk to be picked up again')

    def test_the_message_tells_the_user_what_to_do(self):
        block = self._voiceover_block()
        guard = block[block.index("if not info['duration']"):].split('raise')[1]
        self.assertIn('MP3 or M4A', guard,
                      'the refusal must name a format that will work')


if __name__ == '__main__':
    unittest.main()
