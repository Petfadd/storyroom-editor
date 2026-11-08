import unittest
from section_script import parse_section_script
from document_plan import rows_for

SCRIPT='''# Production

## Project instructions
Use the source footage.

## Source clip register
| Clip | Source | Original window or extraction time | Visible content | Treatment and restrictions |
|---|---|---|---|---|
| C02 | `S01.mp4` | Frame 00:00:01.000 | A sign | Keep original frame. |
| C09 | `S02.mp4` | 00:00:02.000–00:00:04.000 (OUT exclusive) | A street | Wide view. |

## Srt01

**Narration timeline** 00:00:00.000–00:00:04.000 · **Duration** 4.000 s

**Exact narration**

> Approved words

**Visual purpose** Context.

### Srt01-A

- **Narration placement** 00:00:00.000–00:00:02.000 (2.000 s).
- **Source** `S01.mp4` · **Clip** C02 · **Treatment** STILL HOLD.
- **Extraction** Source frame 00:00:01.000 · hold for 2.000 s in this block. Source IN/OUT do not apply to this hold.
- **Actually visible** A sign.
- **Edit and continuity** Start this shot. Keep the original frame.
- **Editorial caption** “Context label”
- **Audio** Mute source. Continue narration.
- **Meaning and limits** Not proof.

### Srt01-B

- **Narration placement** 00:00:02.000–00:00:04.000 (2.000 s).
- **Source** `S02.mp4` · **Clip** C09 · **Treatment** NORMAL-SPEED VIDEO.
- **Extraction** Source IN 00:00:02.000 · source OUT 00:00:04.000 (exclusive).
- **Actually visible** A street.
- **Edit and continuity** Start this shot.
- **Editorial caption** “Street context”
- **Audio** Mute source. Continue narration.
- **Meaning and limits** No evidence implied.

## Final production checks
Review everything.
'''
BLOCKS=[{'number':1,'start_ms':0,'end_ms':4000,'text':'Different words'}]

class SectionScriptTest(unittest.TestCase):
    def test_reads_shots_clocks_text_and_blocks_unsupported_work(self):
        rows= parse_section_script(SCRIPT,BLOCKS)
        self.assertEqual([r['clip_id'] for r in rows],['C02','C09'])
        self.assertEqual([r['requested_treatment'] for r in rows],['STILL HOLD','VIDEO'])
        self.assertEqual((rows[1]['source_in_ms'],rows[1]['source_out_ms']),(2000,4000))
        self.assertTrue(all(r['narration_mismatch'] and r['import_blockers'] for r in rows))
        p={'production_script':{'text':SCRIPT},'srt':{'blocks':BLOCKS}}
        self.assertEqual(rows_for(p)[1],'detailed_production_script')
    def test_rejects_conflicting_register_clock_and_unknown_fields(self):
        for bad in [SCRIPT.replace('S02.mp4` · **Clip** C09','S03.mp4` · **Clip** C09'),
                    SCRIPT.replace('00:00:02.000–00:00:04.000 (2.000 s).','00:00:02.100–00:00:04.000 (2.000 s).'),
                    SCRIPT.replace('- **Audio** Mute source. Continue narration.','- **Unknown direction** Change the edit.\n- **Audio** Mute source. Continue narration.',1)]:
            with self.assertRaises(ValueError):parse_section_script(bad,BLOCKS)
    def test_keeps_uploaded_srt_unchanged(self):
        original=BLOCKS[0]['text'];parse_section_script(SCRIPT,BLOCKS);self.assertEqual(BLOCKS[0]['text'],original)

if __name__=='__main__':unittest.main()
