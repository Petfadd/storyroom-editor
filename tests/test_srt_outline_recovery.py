import unittest
from document_plan import srt_outline_blocks

TABLE = """## Narration blocks

| Block | Narration timeline | Wording |
|---|---|---|
| 1 | 00:00:00,000 → 00:00:04,000 | the first thing the narration says |
| 2 | 00:00:04,000 → 00:00:09,400 | the second thing the narration says |
| 3 | 00:00:09,400 → 00:00:15,000 | the third thing the narration says |
"""


class SrtOutlineRecoveryTest(unittest.TestCase):
    """A narration table inside the script must become the SRT without editing."""

    def test_a_contiguous_table_becomes_blocks_unchanged(self):
        blocks, problems, notes = srt_outline_blocks(TABLE)
        self.assertEqual(problems, [])
        self.assertEqual(notes, [])
        self.assertEqual([b['number'] for b in blocks], [1, 2, 3])
        self.assertEqual((blocks[0]['start_ms'], blocks[0]['end_ms']), (0, 4000))
        self.assertEqual(blocks[1]['text'], 'the second thing the narration says')

    def test_rounding_of_a_few_milliseconds_is_snapped_and_recorded(self):
        # 4 ms is encoder/clock rounding, not a hole in the narration.
        table = TABLE.replace('00:00:04,000 → 00:00:09,400', '00:00:04,004 → 00:00:09,400')
        blocks, problems, notes = srt_outline_blocks(table)
        self.assertEqual(problems, [])
        self.assertEqual(blocks[1]['start_ms'], 4000)
        self.assertEqual(len(notes), 1)
        self.assertIn('4 ms out of step', notes[0])

    def test_a_real_hole_still_refuses_the_table(self):
        table = TABLE.replace('00:00:04,000 → 00:00:09,400', '00:00:06,000 → 00:00:09,400')
        blocks, problems, notes = srt_outline_blocks(table)
        self.assertEqual(blocks, [])
        self.assertTrue(any('contiguous' in p for p in problems))

    def test_no_table_is_silent(self):
        self.assertEqual(srt_outline_blocks('no table here'), ([], [], []))


if __name__ == '__main__':
    unittest.main()
