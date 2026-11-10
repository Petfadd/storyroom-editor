import unittest
from cue_script import parse_cue_script

BLOCKS=[{'number':1,'start_ms':0,'end_ms':2000,'text':'First words'}, {'number':2,'start_ms':2000,'end_ms':4000,'text':'Second words'}]
SCRIPT='''# Example
## Selected asset register
| Asset | Full source | Source IN → exclusive OUT | Type | Identification / context |
|---|---|---|---|---|
| C001 | S01.mp4 | 00:00:01,000 → 00:00:03,000 | VIDEO | context |
| C002 | S02.mp4 | 00:00:05,000 → 00:00:07,000 | STILL | photograph |
## Srt001
Original SRT cue **1** · Original time **00:00:00,000 --> 00:00:02,000**

**Exact narration**

First words

**Picture placement** 00:00:00,000 → 00:00:02,000 · 2.000 s

**VIDEO · C001 · S01.mp4** · Source IN 00:00:01,000 · OUT 00:00:03,000 exclusive · 2.000 s · 1.0× · loop OFF

**Picture.** Context.

**Editorial caption.** Caption.

**Direction.** Enter on boundary.

## Srt002
Original SRT cue **2** · Original time **00:00:02,000 --> 00:00:04,000**

**Exact narration**

Second words

**Picture placement** 00:00:02,000 → 00:00:04,000 · 2.000 s

**STILL · C002 · S02.mp4** · Source IN 00:00:05,000 · OUT 00:00:07,000 exclusive · 2.000 s · 1.0× · loop OFF

**Picture.** Photograph.

**Editorial caption.** Caption.

**Direction.** Enter on boundary.
'''
class CueScriptTest(unittest.TestCase):
 def test_reads_exact_video_and_native_still_interval(self):
  rows=parse_cue_script(SCRIPT,BLOCKS)
  self.assertEqual([(r['shot_id'],r['source_file'],r['source_in_ms'],r['source_out_ms']) for r in rows],[('Srt001','S01.mp4',1000,3000),('Srt002','S02.mp4',5000,7000)])
  self.assertEqual(rows[1]['requested_treatment'],'STILL HOLD')
 def test_rejects_register_clock_or_order_changes(self):
  for text in [SCRIPT.replace('OUT 00:00:03,000 exclusive','OUT 00:00:03,100 exclusive'),SCRIPT.replace('C001 · S01.mp4','C001 · S03.mp4'),SCRIPT.replace('## Srt002','## Srt003')]:
   with self.assertRaises(ValueError):parse_cue_script(text,BLOCKS)

class SourceOnlyCueTest(unittest.TestCase):
 def test_source_filename_and_times_need_no_clip_label_or_register(self):
  text=SCRIPT[ SCRIPT.index('## Srt001'): ].replace('C001 · ','').replace('C002 · ','')
  rows=parse_cue_script(text,BLOCKS)
  self.assertEqual([r['source_file'] for r in rows],['S01.mp4','S02.mp4'])
  self.assertEqual([r['clip_id'] for r in rows],['',''])
  self.assertEqual((rows[0]['source_in_ms'],rows[0]['source_out_ms']),(1000,3000))
 def test_source_only_still_cannot_hide_a_bad_duration(self):
  text=SCRIPT[SCRIPT.index('## Srt001'):].replace('C001 · ','').replace('C002 · ','').replace('OUT 00:00:03,000 exclusive','OUT 00:00:03,100 exclusive')
  with self.assertRaises(ValueError):parse_cue_script(text,BLOCKS)

class OneCueInstructionTest(unittest.TestCase):
 def test_duplicate_video_instruction_rejected(self):
  bad=SCRIPT.replace('**VIDEO · C001 · S01.mp4**', '**VIDEO · C001 · S01.mp4**\n**VIDEO · C001 · S01.mp4**')
  with self.assertRaisesRegex(ValueError,'exactly one shot'):parse_cue_script(bad,BLOCKS)
