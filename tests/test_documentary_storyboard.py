import copy,tempfile,unittest
from pathlib import Path
from documentary_storyboard import prepare,verified_source_interval
from tests.test_documentary_beats import SCRIPT

class StoryboardPreparationTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'sources').mkdir();(self.root/'inputs').mkdir();(self.root/'sources'/'video.mp4').write_bytes(b'video');(self.root/'inputs'/'voice.wav').write_bytes(b'voice')
  self.p={'profile':'documentary','scenes':[],'production_script':{'text':SCRIPT},'voiceover':{'path':'inputs/voice.wav','name':'voice.wav','duration':4},'srt':{'blocks':[{'number':1,'start_ms':0,'end_ms':2000,'text':'Original one'},{'number':2,'start_ms':2000,'end_ms':4000,'text':'Original two'}]},'assets':[{'id':'a','name':'S01.mp4','original_name':'S01.mp4','kind':'video','path':'sources/video.mp4','duration':20,'revision':'v1'}]}
 def tearDown(self):self.tmp.cleanup()
 def test_places_original_narration_and_bite_without_mutating_inputs(self):
  before=copy.deepcopy(self.p);p,plan=prepare(self.p,self.root,[],[],False,lambda _:True)
  self.assertEqual(self.p,before);self.assertEqual(len(p['scenes']),2);self.assertEqual([(s['frame_in'],s['frame_out']) for s in p['scenes']],[(0,60),(60,120)]);self.assertTrue(all(not s['approved'] for s in p['scenes']));self.assertEqual([(b['pause_s'],b['source_in_s'],b['source_out_s']) for b in plan['bites']],[(2,10,12)]);self.assertTrue(p['review_only_draft']);self.assertEqual((self.root/'inputs'/'voice.wav').read_bytes(),b'voice')
 def test_only_unmodified_script_or_approved_gap_uses_exact_interval(self):
  p,_=prepare(self.p,self.root,[],[],False,lambda _:True);a=self.p['assets'][0];scene=p['scenes'][0]
  self.assertEqual(verified_source_interval(p,scene,a),(0.0,2.0))
  changed=copy.deepcopy(scene);changed['in_s']=0.1;self.assertIsNone(verified_source_interval(p,changed,a))
  changed=copy.deepcopy(scene);changed['plan_row']['source_out_ms']=1900;self.assertIsNone(verified_source_interval(p,changed,a))
  changed=copy.deepcopy(p);changed['production_script']['text']+=' edit';self.assertIsNone(verified_source_interval(changed,scene,a))
  changed=copy.deepcopy(scene);changed['plan_row']['source_file']='other.mp4';self.assertIsNone(verified_source_interval(p,changed,a))

 def test_fails_closed_on_overlaps_missing_files_or_existing_work(self):
  self.p['production_script']['text']=SCRIPT.replace('00:00:10,000','00:00:01,000').replace('00:00:12,000','00:00:03,000')
  with self.assertRaisesRegex(ValueError,'overlap'):prepare(self.p,self.root,[],[],False,lambda _:True)
  self.p['production_script']['text']=SCRIPT;self.p['assets'][0]['name']='SO1.mp4';self.p['assets'][0]['original_name']='SO1.mp4'
  with self.assertRaisesRegex(ValueError,'missing'):prepare(self.p,self.root,[],[],False,lambda _:True)
  self.p['scenes']=[{'existing':True}]
  with self.assertRaisesRegex(ValueError,'existing'):prepare(self.p,self.root,[],[],False,lambda _:True)
 def test_gap_requires_explicit_matching_approval(self):
  self.p['production_script']['text']=SCRIPT.replace('00:00:00,000 --> 00:00:02,000','00:00:00,100 --> 00:00:02,000').replace('Source OUT: 00:00:02,000','Source OUT: 00:00:01,900',1)
  with self.assertRaisesRegex(ValueError,'gap'):prepare(self.p,self.root,[],[],False,lambda _:True)
  gap={'narration_in_ms':0,'narration_out_ms':100,'source_in_ms':15000,'source_out_ms':15100,'source_file':'S01.mp4'}
  p,_=prepare(self.p,self.root,[gap],[],False,lambda _:True);self.assertEqual((p['scenes'][0]['frame_in'],p['scenes'][0]['frame_out']),(0,3));self.assertEqual(p['scenes'][0]['in_s'],15)
