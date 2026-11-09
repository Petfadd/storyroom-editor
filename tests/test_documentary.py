import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import app
from documentary import load_plan,validate_bite,annotate_plan

class DocumentaryPlanTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.pid='0123456789abcdef';self.root=self.base/self.pid;self.root.mkdir();(self.root/'sources').mkdir();(self.root/'inputs').mkdir()
  (self.root/'sources'/'video.mp4').write_bytes(b'video');(self.root/'inputs'/'voice.mp3').write_bytes(b'voice')
  self.p={'id':self.pid,'voiceover':{'path':'inputs/voice.mp3','duration':20,'name':'voice.mp3'},'assets':[{'id':'a','kind':'video','path':'sources/video.mp4','duration':10,'name':'video.mp4','revision':'v1'}]};(self.root/'project.json').write_text(json.dumps(self.p));self.original=(self.root/'project.json').read_bytes()
  self.project_patch=patch.object(app,'PROJECTS',self.base);self.project_patch.start();self.probe=patch.object(app,'probe',return_value={'has_audio':True});self.probe.start();self.client=app.app.test_client()
 def tearDown(self):self.project_patch.stop();self.probe.stop();self.tmp.cleanup()
 def test_plan_save_remove_without_touching_project(self):
  data={'revision':0,'asset_id':'a','pause_s':7.25,'source_in_s':2,'source_out_s':5}
  r=self.client.post(f'/api/projects/{self.pid}/documentary-bites',json=data);self.assertEqual(r.status_code,200);plan=r.get_json();self.assertEqual(plan['revision'],1);self.assertEqual(plan['bites'][0]['pause_s'],7.25);self.assertEqual((self.root/'project.json').read_bytes(),self.original)
  self.assertEqual(self.client.post(f'/api/projects/{self.pid}/documentary-bites',json=data).status_code,400)
  r=self.client.delete(f'/api/projects/{self.pid}/documentary-bites/{plan["bites"][0]["id"]}',json={'revision':1});self.assertEqual(r.status_code,200);self.assertEqual(r.get_json()['bites'],[]);self.assertEqual((self.root/'project.json').read_bytes(),self.original)
 def test_bad_values_no_audio_and_replaced_media(self):
  bad={'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':11}
  with self.assertRaises(ValueError):validate_bite(self.p,self.root,bad,lambda p:True)
  bad['source_out_s']=5
  with self.assertRaises(ValueError):validate_bite(self.p,self.root,bad,lambda p:False)
  good=validate_bite(self.p,self.root,bad,lambda p:True);self.p['assets'][0]['revision']='v2';self.assertTrue(annotate_plan(self.p,self.root,{'bites':[good]})['bites'][0]['stale_reasons'])
  (self.root/'inputs'/'voice.mp3').write_bytes(b'changed')
  self.assertEqual(len(annotate_plan(self.p,self.root,{'bites':[good]})['bites'][0]['stale_reasons']),2)
