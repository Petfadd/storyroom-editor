"""The music strip is one unbroken bar, because the bed is never cut.

The strip used to draw a gap under every sound bite and the engine silenced the
music there. Both are gone: the bed runs unbroken, so the strip shows one bar for
the whole film and must not promise a gap that no longer exists.
"""
import json
import subprocess
import unittest
from pathlib import Path


def strip_function():
    source = Path('static/documentary_timeline.js').read_text()
    start = source.index('function docMusicCuts(duration)')
    end = source.index('window.docMusicSpans=docMusicSpans;')
    return source[start:end]


def run(segments, duration, music, script):
    harness = f"""
const assert=require('assert');
global.window={{docTimelineData:{{segments:{json.dumps(segments)}}}}};
let docMusic={json.dumps(music)};
{strip_function()}
{script}
"""
    return subprocess.run(['node', '-e', harness], capture_output=True, text=True)


class MusicTrackShowsTheCutTest(unittest.TestCase):
    def test_the_real_timeline_is_one_span(self):
        segments = [
            {'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 19.366},
            {'kind': 'sound_bite', 'timeline_in_s': 19.366, 'timeline_out_s': 36.866},
            {'kind': 'narration', 'timeline_in_s': 36.866, 'timeline_out_s': 53.400},
            {'kind': 'sound_bite', 'timeline_in_s': 53.400, 'timeline_out_s': 95.900},
        ]
        script = """
const spans=docMusicSpans(95.9);
assert.strictEqual(spans.length,1,'the bed is never cut, so there is one span');
assert.deepStrictEqual([+spans[0].start.toFixed(3),+spans[0].end.toFixed(3)],[0,95.9]);
// No cut is reported anywhere, whatever the saved narration mode says.
assert.deepStrictEqual(docMusicCuts(95.9),[]);
console.log('ok');
"""
        result = run(segments, 95.9, {'speech_mode': 'mute'}, script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_old_saved_modes_do_not_draw_a_gap(self):
        segments = [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 4.0},
                    {'kind': 'sound_bite', 'timeline_in_s': 4.0, 'timeline_out_s': 8.0},
                    {'kind': 'narration', 'timeline_in_s': 8.0, 'timeline_out_s': 12.0}]
        for saved in ({'speech_mode': 'mute'}, {'narration_level': 'mute'},
                      {'speech_mode': 'duck'}, {'duck_under_speech': False}, {}):
            with self.subTest(saved):
                script = """
const spans=docMusicSpans(12);
assert.strictEqual(spans.length,1);
assert.strictEqual(spans[0].start,0);
assert.strictEqual(spans[0].end,12);
console.log('ok');
"""
                result = run(segments, 12.0, saved, script)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_preview_never_silences_or_dips_the_bed(self):
        source = Path('static/documentary_timeline.js').read_text()
        start = source.index('function docSpeechMode(')
        body = source[start:start + 400]
        self.assertIn("return 'full';", body)
        # The duck constant and the stop branch must not survive anywhere.
        self.assertNotIn('DOC_DUCK_LEVEL', source)
        self.assertNotIn("docMusic.narration_level==='mute'", source)
        self.assertNotIn("docMusic.narration_level==='duck'", source)

    def test_the_strip_carries_no_cut_marker_or_silent_state(self):
        source = Path('static/documentary_timeline.js').read_text()
        self.assertIn('doc-music-piece', source)
        self.assertNotIn('doc-music-cut', source)
        self.assertNotIn('· silent', source)
        self.assertNotIn('cut under bites', source)
        # The old copy claimed a global "stops under speech" mode.
        self.assertNotIn('Stops under speech · click to change music', source)


if __name__ == '__main__':
    unittest.main()
