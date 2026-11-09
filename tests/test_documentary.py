import json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import app
from documentary import load_plan,validate_bite,annotate_plan,build_timeline

class DocumentaryPlanTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name);self.pid='0123456789abcdef';self.root=self.base/self.pid;self.root.mkdir();(self.root/'sources').mkdir();(self.root/'inputs').mkdir()
  (self.root/'sources'/'video.mp4').write_bytes(b'video');(self.root/'inputs'/'voice.mp3').write_bytes(b'voice')
  self.p={'id':self.pid,'title':'Fixture','profile':'documentary','format':'landscape','updated':1,'scenes':[],'voiceover':{'path':'inputs/voice.mp3','duration':20,'name':'voice.mp3'},'assets':[{'id':'a','kind':'video','path':'sources/video.mp4','duration':10,'name':'video.mp4','revision':'v1'}]};(self.root/'project.json').write_text(json.dumps(self.p));self.original=(self.root/'project.json').read_bytes()
  self.project_patch=patch.object(app,'PROJECTS',self.base);self.project_patch.start();self.probe=patch.object(app,'probe',return_value={'has_audio':True});self.probe.start();self.client=app.app.test_client()
 def tearDown(self):self.project_patch.stop();self.probe.stop();self.tmp.cleanup()
 def test_plan_save_remove_without_touching_project(self):
  data={'revision':0,'asset_id':'a','pause_s':7.25,'source_in_s':2,'source_out_s':5}
  r=self.client.post(f'/api/projects/{self.pid}/documentary-bites',json=data);self.assertEqual(r.status_code,200);plan=r.get_json();self.assertEqual(plan['revision'],1);self.assertEqual(plan['bites'][0]['pause_s'],7.25);self.assertEqual((self.root/'project.json').read_bytes(),self.original)
  self.assertEqual(self.client.post(f'/api/projects/{self.pid}/documentary-bites',json=data).status_code,400)
  r=self.client.delete(f'/api/projects/{self.pid}/documentary-bites/{plan["bites"][0]["id"]}',json={'revision':1});self.assertEqual(r.status_code,200);self.assertEqual(r.get_json()['bites'],[]);self.assertEqual((self.root/'project.json').read_bytes(),self.original)
 def test_separate_timeline_maps_all_narration_without_modifying_it(self):
  self.p['edit_mode']='documentary'
  self.p['voiceover']['duration']=20
  data={'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':5}
  one=validate_bite(self.p,self.root,data,lambda p:True)
  data.update(pause_s=14,source_in_s=0,source_out_s=2);two=validate_bite(self.p,self.root,data,lambda p:True)
  result=build_timeline(self.p,{'bites':[two,one]});self.assertEqual(result['duration_s'],25);self.assertEqual([s['kind'] for s in result['segments']],['narration','sound_bite','narration','sound_bite','narration'])
  self.assertEqual([(s['source_in_s'],s['source_out_s']) for s in result['segments'] if s['kind']=='narration'],[(0,7),(7,14),(14,20)])
  self.assertEqual([s['narration_resume_s'] for s in result['segments'] if s['kind']=='sound_bite'],[7,14]);self.assertFalse(result['export_available']);self.assertEqual((self.root/'project.json').read_bytes(),self.original)
  one['stale_reasons']=['changed'];self.assertRaises(ValueError,build_timeline,self.p,{'bites':[one]})

 def test_workspace_creation_and_documentary_render_guard(self):
  review=self.client.post('/api/projects',json={'title':'Review'}).get_json();doc=self.client.post('/api/projects',json={'title':'Doc','edit_mode':'documentary'}).get_json()
  self.assertEqual(review['edit_mode'],'review');self.assertEqual(doc['edit_mode'],'documentary')
  self.assertEqual(self.client.post('/api/projects',json={'title':'Bad','edit_mode':'other'}).status_code,400)
  for suffix in ('render','continuous-preview','scene-exports'):
   r=self.client.post(f'/api/projects/{doc["id"]}/{suffix}',json={});self.assertEqual(r.status_code,400);self.assertIn('Documentary',r.get_json()['error'])
  projects=self.client.get('/api/projects').get_json()['projects'];self.assertEqual(next(p for p in projects if p['id']==doc['id'])['edit_mode'],'documentary')

 def test_bad_values_no_audio_and_replaced_media(self):
  bad={'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':11}
  with self.assertRaises(ValueError):validate_bite(self.p,self.root,bad,lambda p:True)
  bad['source_out_s']=5
  with self.assertRaises(ValueError):validate_bite(self.p,self.root,bad,lambda p:False)
  good=validate_bite(self.p,self.root,bad,lambda p:True);self.p['assets'][0]['revision']='v2';self.assertTrue(annotate_plan(self.p,self.root,{'bites':[good]})['bites'][0]['stale_reasons'])
  (self.root/'inputs'/'voice.mp3').write_bytes(b'changed')
  self.assertEqual(len(annotate_plan(self.p,self.root,{'bites':[good]})['bites'][0]['stale_reasons']),2)
