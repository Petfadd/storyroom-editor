import unittest
from documentary import adjusted_srt

class SubtitleAdjustmentTest(unittest.TestCase):
 def setUp(self):
  self.original='1\n00:00:00,000 --> 00:00:02,000\nFirst line\nsecond line\n\n2\n00:00:02,000 --> 00:00:04,000\nNext cue\n'
  self.project={'voiceover':{'duration':4},'srt':{'blocks':[{'number':1,'start_ms':0,'end_ms':2000,'text':'First line second line'},{'number':2,'start_ms':2000,'end_ms':4000,'text':'Next cue'}]}}
  self.bite={'id':'one','pause_s':2,'created':1,'source_in_s':1,'source_out_s':2.5,'asset_id':'a','asset_name':'a'}
 def test_cue_boundary_is_shifted_without_changing_content(self):
  result=adjusted_srt(self.original,self.project,{'bites':[self.bite]})
  self.assertEqual(result,'1\n00:00:00,000 --> 00:00:02,000\nFirst line\nsecond line\n\n2\n00:00:03,500 --> 00:00:05,500\nNext cue\n')
 def test_pause_inside_cue_is_blocked(self):
  self.bite['pause_s']=1
  with self.assertRaisesRegex(ValueError,'inside SRT cue 1'):adjusted_srt(self.original,self.project,{'bites':[self.bite]})
 def test_multiple_bites_and_stale(self):
  b=dict(self.bite,id='two',created=2)
  out=adjusted_srt(self.original,self.project,{'bites':[self.bite,b]})
  self.assertIn('00:00:05,000 --> 00:00:07,000',out)
  b['stale_reasons']=['changed']
  with self.assertRaises(ValueError):adjusted_srt(self.original,self.project,{'bites':[b]})

 def test_explicit_split_preserves_words_and_inserts_silence(self):
  self.bite['pause_s']=1
  result=adjusted_srt(self.original,self.project,{'bites':[self.bite]},[{'cue':1,'pause_ms':1000,'words_before':2}])
  self.assertIn('1\n00:00:00,000 --> 00:00:01,000\nFirst line',result)
  self.assertIn('2\n00:00:02,500 --> 00:00:03,500\nsecond line',result)
  self.assertIn('3\n00:00:03,500 --> 00:00:05,500\nNext cue',result)
  self.assertNotIn('First line second line',result)
 def test_stale_and_invalid_split_choices_fail_closed(self):
  self.bite['pause_s']=1
  for rule in [{'cue':1,'pause_ms':900,'words_before':2},{'cue':1,'pause_ms':1000,'words_before':4},{'cue':1,'pause_ms':1000,'words_before':0}]:
   with self.assertRaises(ValueError):adjusted_srt(self.original,self.project,{'bites':[self.bite]},[rule])
