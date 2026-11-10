"""The shipped template must be a valid script, not just readable prose."""
import tempfile
import unittest
from pathlib import Path

from document_plan import srt_outline_blocks
from documentary_beats import read_beats
from documentary_script_diagnostics import diagnose

TEMPLATE = Path(__file__).resolve().parent.parent/'production-script-templates'/'documentary-production-script-template.txt'


class TemplateTest(unittest.TestCase):
    def setUp(self):
        text = TEMPLATE.read_text()
        # The worked example is a complete script; everything above it instructs.
        self.example = text[text.index('PROJECT: Worked Example'):]
        self.tmp = tempfile.TemporaryDirectory(); self.root = Path(self.tmp.name)
        (self.root/'sources').mkdir()
        (self.root/'sources/a.mp4').write_bytes(b'video')
        (self.root/'sources/b.mp4').write_bytes(b'video')
        self.project = {'production_script': {'text': self.example}, 'voiceover': {'duration': 38.0},
                        'assets': [{'id': 'a', 'name': 'S01.mp4', 'original_name': 'S01.mp4', 'kind': 'video',
                                    'path': 'sources/a.mp4', 'duration': 60},
                                   {'id': 'b', 'name': 'S02.mp4', 'original_name': 'S02.mp4', 'kind': 'video',
                                    'path': 'sources/b.mp4', 'duration': 30}]}

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_worked_example_reads_without_errors(self):
        beats = read_beats(self.example)
        self.assertEqual([b['kind'] for b in beats], ['narration', 'sound_bite', 'narration'])
        self.assertEqual(beats[1]['pause_ms'], 32000)
        self.assertEqual(beats[2]['narration_in_ms'], 32000)
        self.assertEqual(beats[2]['narration_out_ms'], 38000)

    def test_the_worked_example_has_nothing_to_fix(self):
        issues = diagnose(self.project, self.root)['build_issues']
        self.assertEqual(len(issues), 1, issues)
        self.assertIn('reviewed explicitly', issues[0])

    def test_its_narration_table_becomes_the_srt_unchanged(self):
        blocks, problems, notes = srt_outline_blocks(self.example)
        self.assertEqual((problems, notes), ([], []))
        self.assertEqual([b['number'] for b in blocks], [1, 2, 3, 4])
        self.assertEqual(blocks[0]['start_ms'], 0)
        self.assertEqual(blocks[-1]['end_ms'], 38000)

    def test_its_picture_equals_its_timeline_for_every_beat(self):
        """The arithmetic the RULES section asks the writer to check."""
        for beat in read_beats(self.example):
            source = beat['source_out_ms']-beat['source_in_ms']
            if beat['kind'] == 'narration':
                self.assertEqual(source, beat['narration_out_ms']-beat['narration_in_ms'], beat['label'])
            else:
                self.assertEqual(source, beat['duration_ms'], beat['label'])

    def test_the_four_template_blocks_add_up(self):
        """A template with bad arithmetic teaches bad arithmetic."""
        import re
        text = TEMPLATE.read_text()
        parts = re.split(r'TEMPLATE ([A-D]) — [^\n]*\n-+\n', text)
        self.assertEqual(len(parts), 9, 'all four template blocks must be present')
        previous_master = None
        narration_out = None
        for i in range(1, len(parts), 2):
            letter, body = parts[i], parts[i+1]
            source = re.search(r'Source in:\s*(\S+)\s*\nSource out:\s*(\S+)', body)
            master = re.search(r'Master timeline:\s*(\S+)\s*-->\s*(\S+)', body)
            clock = re.search(r'Narration clock:\s*([^\n]+)', body).group(1)
            sspan = self._ms(source.group(2)) - self._ms(source.group(1))
            mspan = self._ms(master.group(2)) - self._ms(master.group(1))
            self.assertEqual(sspan, mspan, f'Template {letter}: source span must equal master span')
            if previous_master is not None:
                self.assertEqual(self._ms(master.group(1)), previous_master, f'Template {letter}: master clock must be continuous')
            span = re.search(r'(\d\d:\d\d:\d\d,\d\d\d)\s*-->\s*(\d\d:\d\d:\d\d,\d\d\d)', clock)
            if span:
                self.assertEqual(self._ms(span.group(2)) - self._ms(span.group(1)), mspan, f'Template {letter}: narration span must equal the master span')
                if narration_out is not None:
                    self.assertEqual(self._ms(span.group(1)), narration_out, f'Template {letter}: narration must resume where it paused')
                narration_out = self._ms(span.group(2))
            else:
                pause = re.search(r'PAUSED at (\S+)', clock)
                self.assertIsNotNone(pause, f'Template {letter}: a bite must name its pause')
                if narration_out is not None:
                    self.assertEqual(self._ms(pause.group(1)), narration_out, f'Template {letter}: the pause must match narration OUT')
            previous_master = self._ms(master.group(2))

    @staticmethod
    def _ms(value):
        import re
        h, m, s, ms = map(int, re.split(r'[:,]', value))
        return ((h*60+m)*60+s)*1000+ms

    def test_no_beat_asks_for_footage_past_its_file(self):
        limits = {'S01.mp4': 60000, 'S02.mp4': 30000}
        for beat in read_beats(self.example):
            self.assertLessEqual(beat['source_out_ms'], limits[beat['source_file']], beat['label'])


if __name__ == '__main__':
    unittest.main()
