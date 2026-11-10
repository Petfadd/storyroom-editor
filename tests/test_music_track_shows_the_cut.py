"""The timeline music strip must show the cut, not one unbroken bar.

The audio engine cuts the bed for the whole of every sound bite. The strip used
to draw a single bar from 0 to the end of the timeline, so the user saw music
running under a bite the export silences.
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
    def test_the_real_timeline_leaves_a_gap_under_every_bite(self):
        segments = [
            {'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 19.366},
            {'kind': 'sound_bite', 'timeline_in_s': 19.366, 'timeline_out_s': 36.866},
            {'kind': 'narration', 'timeline_in_s': 36.866, 'timeline_out_s': 53.400},
            {'kind': 'sound_bite', 'timeline_in_s': 53.400, 'timeline_out_s': 95.900},
        ]
        script = """
const spans=docMusicSpans(95.9);
assert.deepStrictEqual(spans.map(s=>[+s.start.toFixed(3),+s.end.toFixed(3)]),[[0,19.366],[36.866,53.4]]);
// Every second is either music or a bite, never both. The two bites are 17.5s
// and 42.5s, so 62.2s of the 95.9s timeline carries music.
const music=spans.reduce((a,s)=>a+(s.end-s.start),0);
assert.ok(Math.abs(music+17.5+42.5-95.9)<1e-6, 'music '+music);
console.log('ok');
"""
        result = run(segments, 95.9, {'duck_under_speech': True, 'speech_mode': 'mute'}, script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_a_bite_at_either_end_still_leaves_no_empty_bar(self):
        cases = {
            'bite_at_start': ([{'kind': 'sound_bite', 'timeline_in_s': 0.0, 'timeline_out_s': 5.0},
                               {'kind': 'narration', 'timeline_in_s': 5.0, 'timeline_out_s': 10.0}],
                              [[5.0, 10.0]]),
            'bite_at_end': ([{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 5.0},
                             {'kind': 'sound_bite', 'timeline_in_s': 5.0, 'timeline_out_s': 10.0}],
                            [[0.0, 5.0]]),
            'back_to_back_bites': ([{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 3.0},
                                    {'kind': 'sound_bite', 'timeline_in_s': 3.0, 'timeline_out_s': 6.0},
                                    {'kind': 'sound_bite', 'timeline_in_s': 6.0, 'timeline_out_s': 9.0},
                                    {'kind': 'narration', 'timeline_in_s': 9.0, 'timeline_out_s': 12.0}],
                                   [[0.0, 3.0], [9.0, 12.0]]),
        }
        for name, (segments, expected) in cases.items():
            with self.subTest(name):
                script = f"""
const spans=docMusicSpans({segments[-1]['timeline_out_s']});
assert.deepStrictEqual(spans.map(s=>[+s.start.toFixed(3),+s.end.toFixed(3)]),{json.dumps(expected)});
console.log('ok');
"""
                result = run(segments, segments[-1]['timeline_out_s'],
                             {'duck_under_speech': True, 'speech_mode': 'mute'}, script)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_only_a_ducked_bite_leaves_the_bar_unbroken(self):
        # This is the one state that keeps music under a bite, so the drawing has
        # to be able to show it; the engine no longer offers it as a setting.
        segments = [{'kind': 'narration', 'timeline_in_s': 0.0, 'timeline_out_s': 4.0},
                    {'kind': 'sound_bite', 'timeline_in_s': 4.0, 'timeline_out_s': 8.0}]
        script = """
assert.strictEqual(docMusicSpans(8).length,1);
console.log('ok');
"""
        result = run(segments, 8.0, {'duck_under_speech': True, 'speech_mode': 'duck'}, script)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_the_strip_carries_the_reviewed_spans_and_a_cut_marker(self):
        source = Path('static/documentary_timeline.js').read_text()
        self.assertIn('doc-music-piece', source)
        self.assertIn('doc-music-cut', source)
        # Stopping the bed under the narration too leaves no music at all, and
        # the strip must say that rather than draw an empty bar.
        self.assertIn('docMusicCuts', source)
        # The old copy claimed a global "stops under speech" mode.
        self.assertNotIn('Stops under speech · click to change music', source)


if __name__ == '__main__':
    unittest.main()
