import copy
import unittest
from plain_script import parse_plain_script
from document_plan import rows_for

BLOCKS=[{'number':1,'start_ms':0,'end_ms':4000,'text':'Original words'}, {'number':2,'start_ms':4000,'end_ms':8000,'text':'Next words'}]
SCRIPT='''Shot: Srt001
Narration timeline: 00:00:00,000 --> 00:00:04,000
Exact narration: Original words
Full source: PROPOSED: First video.mp4
Source IN: 00:00:01,000
Source OUT: 00:00:05,000
Treatment: VIDEO, 1.0×, loop OFF
Visual direction: Street
Continuity/caption: Unverified

Shot: Srt002
Narration timeline: 00:00:04,000 --> 00:00:08,000
Exact narration: Next words
Full source: S02.mp4
Source IN: 00:00:05,000
Source OUT: 00:00:09,000
Treatment: STILL (native static interval)
Visual direction: Document
Continuity/caption: Check rights
'''
class PlainScriptTest(unittest.TestCase):
 def test_exact_cues_and_proposed_full_filename(self):
  rows,mode=rows_for({'production_script':{'text':SCRIPT},'srt':{'blocks':copy.deepcopy(BLOCKS)}})
  self.assertEqual(mode,'detailed_production_script');self.assertEqual([r['source_file'] for r in rows],['First video.mp4','S02.mp4']);self.assertEqual([r['shot_id'] for r in rows],['Srt001','Srt002']);self.assertTrue(rows[0]['source_proposed']);self.assertEqual(rows[1]['requested_treatment'],'STILL HOLD');self.assertEqual(rows[1]['source_out_ms'],9000)
 def test_reject_bad_instructions_without_guessing(self):
  for bad in [SCRIPT.replace('00:00:05,000\nTreatment','00:00:05,100\nTreatment',1),SCRIPT.replace('Shot: Srt002','Shot: Srt001'),SCRIPT.replace('loop OFF','loop ON'),SCRIPT.replace('Visual direction: Street','Visual direction: Street\nUnexpected instruction: crop'),SCRIPT.replace('Full source: S02.mp4','Full source: ../S02.mp4')]:
   with self.subTest(bad=bad):
    with self.assertRaises(ValueError):parse_plain_script(bad,BLOCKS)
 def test_srt_conflict_and_block_map_are_not_ignored(self):
  p={'production_script':{'text':SCRIPT},'srt':{'blocks':copy.deepcopy(BLOCKS)},'srt_block_map':{'text':'map'}}
  with self.assertRaises(ValueError):rows_for(p)
  p.pop('srt_block_map');p['srt']['blocks'][0]['end_ms']=3999
  with self.assertRaises(ValueError):rows_for(p)
