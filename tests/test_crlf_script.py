"""A script written on Windows must read exactly like the same script on Unix."""
import io
import tempfile
import unittest
from pathlib import Path

from documentary_beats import read_beats
from documentary_script_diagnostics import diagnose
from production import read_document

LF = """Beat: 001
Type: VOICE OVER
Master timeline: 00:00:00,000 --> 00:00:05,000
Narration clock: 00:00:00,000 --> 00:00:05,000
Narration state: PLAYING
Footage audio: MUTED (0%)
Full source: S01.mp4
Source in: 00:00:10,000
Source out: 00:00:15,000

Beat: 002
Type: DOCUMENTARY SOUND BITE
Master timeline: 00:00:05,000 --> 00:00:09,000 (Duration: 4 sec)
Narration clock: PAUSED at 00:00:05,000
Narration state: PAUSED
Footage audio: UNMUTED (100%)
Full source: S01.mp4
Source in: 00:00:20,000
Source out: 00:00:24,000

Beat: 003
Type: VOICE OVER
Master timeline: 00:00:09,000 --> 00:00:12,000
Narration clock: RESUMES at 00:00:05,000 --> 00:00:08,000
Narration state: RESUMING
Footage audio: MUTED (0%)
Full source: S01.mp4
Source in: 00:00:30,000
Source out: 00:00:33,000
"""


class WindowsLineEndingTest(unittest.TestCase):
    def test_crlf_is_normalised_on_upload(self):
        text = read_document(io.BytesIO(LF.replace('\n', '\r\n').encode()), '.txt')
        self.assertNotIn('\r', text)
        self.assertEqual(text, LF)

    def test_a_crlf_script_reads_the_same_as_an_lf_one(self):
        # Before normalisation this raised "Beat 001: provide exactly one Type field",
        # which named neither Windows nor line endings.
        beats = read_beats(read_document(io.BytesIO(LF.replace('\n', '\r\n').encode()), '.txt'))
        self.assertEqual([b['kind'] for b in beats], ['narration', 'sound_bite', 'narration'])
        self.assertEqual(beats[2]['narration_in_ms'], 5000)

    def test_a_crlf_script_audits_clean(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root/'sources').mkdir(); (root/'sources/a.mp4').write_bytes(b'video')
            project = {'production_script': {'text': read_document(io.BytesIO(LF.replace('\n', '\r\n').encode()), '.txt')},
                       'voiceover': {'duration': 8.0},
                       'assets': [{'id': 'a', 'name': 'S01.mp4', 'original_name': 'S01.mp4', 'kind': 'video',
                                   'path': 'sources/a.mp4', 'duration': 40}]}
            issues = diagnose(project, root)['build_issues']
            self.assertEqual(len(issues), 1, issues)
            self.assertIn('reviewed explicitly', issues[0])


if __name__ == '__main__':
    unittest.main()
