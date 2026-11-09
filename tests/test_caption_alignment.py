import copy,unittest,tempfile
from pathlib import Path
from caption_alignment import align,caption_phrases,fingerprint,state
class CaptionAlignmentTest(unittest.TestCase):
 def test_matches_words_and_uses_audio_times_not_proportions(self):
  blocks=[{'number':1,'start_ms':0,'end_ms':4000,'text':'One two three four five six seven'}];words=[{'text':w,'start_s':a,'end_s':b} for w,a,b in [('One',.1,.3),('two',.4,.6),('three',.7,1),('four',1.1,1.4),('five',1.5,1.7),('six',1.8,2),('seven',3.1,3.7)]];result=align(blocks,{'segments':[{'words':words}]})
  self.assertEqual(result['status'],'complete');self.assertEqual([x['text'] for x in result['phrases']],['One two three four five six','seven']);self.assertEqual([(x['start_ms'],x['end_ms']) for x in result['phrases']],[(100,2000),(3100,3700)])
 def test_mismatch_and_extra_spoken_words_withheld(self):
  cue=[{'number':5,'start_ms':0,'end_ms':3000,'text':'wrong name'}];t={'segments':[{'words':[{'text':'wrong','start_s':.1,'end_s':.3},{'text':'person','start_s':.4,'end_s':.6}]}]};r=align(cue,t);self.assertEqual(r['status'],'needs_review');self.assertEqual(r['phrases'],[]);self.assertEqual(r['issues'][0]['cue'],5)
  cue[0]['text']='wrong';self.assertEqual(align(cue,t)['status'],'needs_review')
 def test_signature_changes_with_input(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'a.wav').write_bytes(b'abc');p={'voiceover':{'path':'a.wav'},'srt':{'blocks':[{'number':1,'start_ms':0,'end_ms':1000,'text':'words'}]}};old=fingerprint(root,p);self.assertEqual(state(root,p)['status'],'not_started');p2=copy.deepcopy(p);p2['srt']['blocks'][0]['text']='other';self.assertNotEqual(old,fingerprint(root,p2))
