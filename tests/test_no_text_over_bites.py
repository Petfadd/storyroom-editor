"""On-screen text never lands on a sound bite or on ordinary footage.

The monitor drew the caption from the playback schedule but decided "is this a
bite?" from the planning segments. Those are two different clocks, so when they
drifted a bite's own caption (e.g. "March 7, 2016 — Los Angeles, CA") was painted
over the bite footage. The kind and the caption must come from one source.
"""
import re
import unittest
from pathlib import Path

JS = Path('static/documentary_timeline.js').read_text()

def monitor_block():
    """The stretch of docUpdatePosition that decides what the monitor shows."""
    start = JS.index('const captionOn=P?.burn_captions!==false;')
    end = JS.index('fitSrtCaption(cue)', start)
    return JS[start:end]

class OverlayTextTest(unittest.TestCase):
    def test_kind_and_caption_come_from_the_same_clock(self):
        block = monitor_block()
        # One lookup decides both; asking a second clock is the drift that caused
        # a bite caption to be drawn over the bite.
        self.assertEqual(len(re.findall(r'docDirectAt\(', block)), 1,
                         'the monitor must read the schedule once and use it for both')
        self.assertIn('shownKind=scheduled?.kind||seg?.kind', block)

    def test_only_a_narration_beat_may_show_a_caption(self):
        block = monitor_block()
        self.assertIn("narrationBeat=shownKind==='narration'", block)
        self.assertIn("shownKind==='picture_tail'", block)
        # The caption is taken from the same scheduled event, never from `seg`.
        self.assertIn('scheduled?.caption', block)
        self.assertNotIn('docDirectAt(t)?.caption', block,
                         'reading the caption straight from the schedule skips the bite guard')

    def test_the_overlay_is_cleared_when_there_is_nothing_to_show(self):
        """An empty caption must clear the layer, not leave the last text up.

        The separate script echoes under the picture were removed with the rest
        of the circled chrome, so the remaining in-picture layer is the one that
        carries the guard.
        """
        block = monitor_block()
        self.assertIn("editor-captions", block)
        self.assertIn("classList.toggle('captions-off'", block)
        # A removed element must never be written to again.
        for gone in ('doc-script-overlay', 'script-caption-review',
                     'monitor-source-detail'):
            self.assertNotIn(gone, block, f'{gone} was removed from the template')

    def test_no_script_writes_to_a_removed_header(self):
        """A write to a missing node throws and aborts the whole render.

        The monitor header was removed, but renderInspector still wrote to its
        shot label. That threw, renderEditor never finished, and the timeline sat
        on its "LOADING" placeholder even though the data had already arrived --
        it looked like a load that never completed rather than a crash.
        """
        html = ''.join(p.read_text() for p in Path('.').glob('templates/*.html'))
        source = ''.join(p.read_text() for p in Path('.').glob('static/*.js'))
        # Ids that are built by the scripts are legitimate; ids that only ever
        # appear as a write target are not.
        built = set(re.findall(r'id="([A-Za-z0-9_-]+)"', source))
        for gone in ('monitor-shot-label', 'monitor-source-detail',
                     'script-caption-review', 'doc-script-overlay',
                     'transport-tally', 'fit-button', 'aspect-button',
                     'monitor-menu-button'):
            self.assertNotIn(gone, html, f'#{gone} should have been removed')
            self.assertNotIn(f"$('#{gone}').", source,
                             f'#{gone} was removed but is still written to')
            if gone in built:
                self.skipTest(f'#{gone} is rebuilt by a script')

    def test_the_big_play_button_is_gone(self):
        """It was removed from the monitor, so nothing may write to it."""
        html = ''.join(p.read_text() for p in Path('.').glob('templates/*.html'))
        js = ''.join(p.read_text() for p in Path('.').glob('static/*.js'))
        self.assertNotIn('big-play', html)
        self.assertNotIn("$('#big-play')", js,
                         'writes to the removed button would throw')

    def test_a_caption_note_stays_inside_its_own_track(self):
        """A fixed top offset landed on the row below once rows became ratios."""
        css = Path('static/editor.css').read_text()
        notes = re.findall(r'\.caption-note\{([^}]*)\}', css)
        self.assertTrue(notes, 'the caption note has no rule')
        body = notes[-1]
        self.assertIn('bottom:', body, 'pin it to its own row, not a fixed top')
        self.assertNotIn('top:', body)

    def test_a_narrow_slot_hides_its_label(self):
        """Labels spilled out of slots only a few pixels wide.

        At the default zoom most picture slots are under 64px. Their label could
        not fit, so it overprinted the neighbouring slots. The thumbnail is what
        identifies a slot at that size.
        """
        css = Path('static/editor.css').read_text()
        self.assertIn('container-type:inline-size', css)
        self.assertRegex(css, r'@container\s*\(max-width:\s*64px\)')
        # The clip itself still clips whatever it holds.
        clips = [b for b in re.findall(r'\.shot-clip\{[^}]*\}', css) if 'overflow' in b]
        self.assertTrue(clips)
        self.assertIn('overflow:hidden', clips[0])

    def test_no_still_frame_is_shown_in_the_monitor(self):
        """The monitor must show moving picture, never a still standing in for it."""
        js = Path('static/documentary_timeline.js').read_text()
        self.assertNotIn('docShowPoster', js)
        self.assertNotIn('docPosterUrl', js)

    def test_playback_starts_the_audio_before_the_picture(self):
        """Play awaited a 41MB source load before the audio ever started.

        The picture follows the narration clock, so starting audio first means
        the first click responds immediately instead of sitting silent on black.
        """
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index('async function docPlayTimeline(){')
        body = js[start:start + 1600]
        play = body.index('await narrNode.play()')
        picture = body.index('docDirectSwitch(narrNode.currentTime,true,true)')
        self.assertLess(play, picture,
                        'audio must start before the picture is loaded')


    def test_the_schedule_repaints_the_monitor(self):
        """The monitor was black until something else happened to repaint it.

        The picture cannot be loaded before the schedule says which frame belongs
        at the playhead, so the schedule's own arrival must load it. Without this
        the player sat marked active with no source at all.
        """
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index('async function docLoadSchedule')
        body = js[start:js.index('async function', start + 10)]
        apply = body[body.index('const apply='):body.index('if(cached)')]
        self.assertIn('docDirectSwitch(', apply,
                      'a loaded schedule must draw its frame')


    def test_stop_silences_every_picture_element(self):
        """Stop used to pause only the standby, so promoted footage kept running."""
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index('window.cancelDocPlay=function()')
        body = js[start:js.index('window.resetDocTimeline', start)]
        self.assertIn('pause()', body)
        # Every picture element, not just one: after a promote the live picture
        # is whichever element the cut was swapped into.
        self.assertIn('forEach', body)
        self.assertIn('masterNode', body)
        self.assertIn('sourceNode', body)

    def test_the_pre_roll_never_lands_on_the_visible_picture(self):
        """Pre-rolling the next cut into the shown element plays wrong footage.

        After a seek the live picture is master while standbyNodeEl can still be
        master, so the upcoming cut was loaded into the visible element.
        """
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index('function docPremoteNext(')
        body = js[start:js.index('function docPromote(', start)]
        self.assertIn('if(docStandby()===live)', body,
                      'the standby must be steered off the live element')

    def test_a_seek_discards_a_stale_pre_roll(self):
        """A cut pre-rolled for the old position must not survive a seek."""
        js = Path('static/documentary_timeline.js').read_text()
        start = js.index('async function docDirectSwitch')
        body = js[start:start + 700]
        self.assertIn('if(force){docNextEvent=null', body)


    def test_the_schedule_never_blocks_the_first_paint(self):
        # The schedule audits every source frame and took over a minute on a real
        # storyboard. Awaiting it in the timeline loader froze the editor: no
        # picture, no playback and no tracks. It must be fetched in the background.
        # Slice the timeline loader only: awaiting the schedule there is what froze
        # the editor. Inside docLoadSchedule it is correct and must stay.
        loader_start = JS.index('window.reloadDocTimeline=async function()')
        loader_end = JS.index('function docRenderProgram()', loader_start)
        self.assertNotIn('documentary-beat-playback-schedule',
                         JS[loader_start:loader_end],
                         'the schedule must not be awaited in the load path')
        self.assertIn('docLoadSchedule(id,version,plan.revision,token)', JS)
        loader = re.search(r'async function docLoadSchedule\([^)]*\)\{.*?\n\}', JS, re.S)
        self.assertIsNotNone(loader, 'the background loader went missing')
        # A schedule failure is an accuracy regression, never a broken editor.
        self.assertIn('catch(e){', loader.group(0))

    def test_the_schedule_result_is_reused(self):
        # Re-fetching it on every repaint would reintroduce the same stall.
        self.assertIn('docScheduleCache', JS)

if __name__ == '__main__':
    unittest.main()
