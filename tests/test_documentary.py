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
 def test_documentary_upload_does_not_start_review_auto_assembly(self):
  import automatic
  self.p['edit_mode']='documentary';self.p['auto_assemble_uploads']=True;self.p['srt']={'blocks':[{'number':1}]};self.p['production_script']={'text':'test'}
  import types
  result=[];studio=types.SimpleNamespace(folder=lambda pid:self.root,write=lambda path,data:result.append(data))
  with patch.object(automatic.threading,'Thread',side_effect=AssertionError('must not spawn review assembly')):
   automatic.maybe_queue(studio,self.p)
  self.assertEqual(result[0]['status'],'paused');self.assertIn('No review storyboard',result[0]['detail'])

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

 def test_timeline_keeps_reviewed_bite_speech_for_the_live_preview(self):
  # The preview reads spoken words off the bite's own timeline segment, so the
  # planner must pass reviewed speech through unchanged and bite-relative.
  self.p['edit_mode']='documentary';self.p['voiceover']['duration']=20
  data={'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':5}
  one=validate_bite(self.p,self.root,data,lambda p:True)
  spoken=[{'start_s':0.0,'end_s':1.5,'text':'Actual words spoken here.'}]
  one['speech']=spoken
  result=build_timeline(self.p,{'bites':[one]})
  bite=next(s for s in result['segments'] if s['kind']=='sound_bite')
  self.assertEqual(bite['speech'],spoken)
  self.assertEqual(result['segments'][0]['kind'],'narration');self.assertNotIn('speech',result['segments'][0])
  one.pop('speech')
  self.assertEqual(next(s for s in build_timeline(self.p,{'bites':[one]})['segments'] if s['kind']=='sound_bite')['speech'],[])

 def test_workspace_creation_and_documentary_render_guard(self):
  review=self.client.post('/api/projects',json={'title':'Review'}).get_json();doc=self.client.post('/api/projects',json={'title':'Doc','edit_mode':'documentary'}).get_json()
  self.assertEqual(review['edit_mode'],'review');self.assertEqual(doc['edit_mode'],'documentary')
  self.assertEqual(self.client.post('/api/projects',json={'title':'Bad','edit_mode':'other'}).status_code,400)
  for suffix in ('render','continuous-preview','scene-exports'):
   r=self.client.post(f'/api/projects/{doc["id"]}/{suffix}',json={});self.assertEqual(r.status_code,400);self.assertIn('Documentary',r.get_json()['error'])
  projects=self.client.get('/api/projects').get_json()['projects'];self.assertEqual(next(p for p in projects if p['id']==doc['id'])['edit_mode'],'documentary')

 def test_adjusted_srt_download_keeps_uploaded_file_unchanged(self):
  original='1\n00:00:00,000 --> 00:00:02,000\nFirst line\nsecond line\n\n2\n00:00:02,000 --> 00:00:04,000\nNext cue\n'
  self.p['edit_mode']='documentary';self.p['srt']={'name':'original.srt','blocks':app.parse_srt(original)}
  (self.root/'project.json').write_text(json.dumps(self.p));(self.root/'inputs'/'captions.srt').write_text(original)
  bite=validate_bite(self.p,self.root,{'asset_id':'a','pause_s':2,'source_in_s':1,'source_out_s':2.5},lambda p:True)
  (self.root/'documentary_plan.json').write_text(json.dumps({'version':1,'revision':1,'bites':[bite]}))
  r=self.client.get(f'/api/projects/{self.pid}/documentary-subtitles');self.assertEqual(r.status_code,200)
  self.assertIn('00:00:03,500 --> 00:00:05,500',r.get_data(as_text=True));self.assertEqual((self.root/'inputs'/'captions.srt').read_text(),original)
  bite['pause_s']=1;(self.root/'documentary_plan.json').write_text(json.dumps({'version':1,'revision':2,'bites':[bite]}))
  r=self.client.get(f'/api/projects/{self.pid}/documentary-subtitles');self.assertEqual(r.status_code,400);self.assertIn('inside SRT cue',r.get_json()['error'])
  url=f'/api/projects/{self.pid}/documentary-subtitles'
  self.assertEqual(self.client.post(url,json={'revision':1,'splits':[{'cue':1,'pause_ms':1000,'words_before':2}]}).status_code,400)
  r=self.client.post(url,json={'revision':2,'splits':[{'cue':1,'pause_ms':1000,'words_before':2}]});self.assertEqual(r.status_code,200)
  self.assertIn('00:00:02,500 --> 00:00:03,500\nsecond line',r.get_data(as_text=True));self.assertEqual((self.root/'inputs'/'captions.srt').read_text(),original)

 def test_drag_moves_only_narration_pause_with_revision_guard(self):
  self.p['edit_mode']='documentary';(self.root/'project.json').write_text(json.dumps(self.p))
  params={'revision':0,'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':5}
  plan=self.client.post(f'/api/projects/{self.pid}/documentary-bites',json=params).get_json();bite=plan['bites'][0]
  url=f'/api/projects/{self.pid}/documentary-bites/{bite["id"]}'
  moved=self.client.patch(url,json={'revision':1,'pause_s':9.5});self.assertEqual(moved.status_code,200)
  new=moved.get_json()['bites'][0];self.assertEqual(new['pause_s'],9.5);self.assertEqual(new['source_in_s'],2);self.assertEqual(new['source_out_s'],5);self.assertEqual(new['id'],bite['id'])
  self.assertEqual(self.client.patch(url,json={'revision':1,'pause_s':3}).status_code,400)
  self.assertEqual(self.client.patch(url,json={'revision':2,'pause_s':21}).status_code,400)
  self.assertEqual(self.client.get(f'/api/projects/{self.pid}/documentary-bites').get_json()['bites'][0]['pause_s'],9.5)
  (self.root/'inputs'/'voice.mp3').write_bytes(b'changed')
  self.assertEqual(self.client.patch(url,json={'revision':2,'pause_s':5}).status_code,400)

 def test_review_draft_is_only_served_when_current(self):
  self.p['edit_mode']='documentary';(self.root/'project.json').write_text(json.dumps(self.p))
  (self.root/'reviews').mkdir();(self.root/'renders').mkdir()
  name='documentary_review_'+'a'*32+'.mp4';(self.root/'renders'/name).write_bytes(b'draft')
  (self.root/'reviews'/'documentary_review_preview.json').write_text(json.dumps({'output':name,'project_version':1,'plan_revision':0,'audio_processing_version':__import__('documentary_audio').AUDIO_PROCESSING_VERSION}))
  url=f'/api/projects/{self.pid}/documentary-review-preview'
  state=self.client.get(url).get_json();self.assertEqual(state['status'],'ready')
  response=self.client.get(state['url']);self.assertEqual(response.status_code,200);response.close()
  old_manifest={'output':name,'project_version':1,'plan_revision':0,'audio_processing_version':'old'}
  (self.root/'reviews'/'documentary_review_preview.json').write_text(json.dumps(old_manifest))
  self.assertEqual(self.client.get(url).get_json()['status'],'stale')
  old_manifest['audio_processing_version']=__import__('documentary_audio').AUDIO_PROCESSING_VERSION
  (self.root/'reviews'/'documentary_review_preview.json').write_text(json.dumps(old_manifest))
  (self.root/'documentary_plan.json').write_text(json.dumps({'version':1,'revision':1,'bites':[]}))
  self.assertEqual(self.client.get(url).get_json()['status'],'stale')
  self.assertEqual(self.client.get(state['url']).status_code,404)

 def test_prepare_timeline_preview_and_stale_guard(self):
  import documentary_preview,time
  self.p['edit_mode']='documentary';self.p['voiceover']['duration']=2;self.p['scenes']=[{'frame_in':0,'frame_out':60,'asset_id':'a','in_s':0}];(self.root/'project.json').write_text(json.dumps(self.p))
  bite=validate_bite(self.p,self.root,{'asset_id':'a','pause_s':1,'source_in_s':2,'source_out_s':3},lambda path:True)
  (self.root/'documentary_plan.json').write_text(json.dumps({'version':1,'revision':1,'bites':[bite]}));(self.root/'renders').mkdir()
  def build(root,project,plan,out,allow_tail,progress):out.write_bytes(b'review preview');return {'duration_s':3,'picture_tail_s':0,'audio_processing_version':__import__('documentary_audio').AUDIO_PROCESSING_VERSION}
  url=f'/api/projects/{self.pid}/documentary-review-preview'
  with patch.object(documentary_preview,'build',side_effect=build):
   self.assertEqual(self.client.post(url,json={}).status_code,202)
   for _ in range(100):
    status=self.client.get(url).get_json()
    if status['status'] not in ('queued','running'):break
    time.sleep(.01)
   self.assertEqual(status['status'],'ready');response=self.client.get(status['url']);self.assertEqual(response.data,b'review preview');response.close()
   self.assertEqual((self.root/'project.json').read_text(),json.dumps(self.p))
   bite['pause_s']=1.5;(self.root/'documentary_plan.json').write_text(json.dumps({'version':1,'revision':2,'bites':[bite]}))
   self.assertEqual(self.client.get(url).get_json()['status'],'stale');self.assertEqual(self.client.get(status['url']).status_code,404)
  documentary_preview.JOBS.pop(self.pid,None)

 def test_batch_replacement_is_atomic_and_revision_guarded(self):
  self.p['edit_mode']='documentary';(self.root/'project.json').write_text(json.dumps(self.p));url=f'/api/projects/{self.pid}/documentary-bites'
  bite={'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':5}
  plan=self.client.post(url,json={'revision':0,**bite}).get_json()
  r=self.client.put(url,json={'revision':1,'bites':[bite,{**bite,'source_out_s':99}]});self.assertEqual(r.status_code,400)
  self.assertEqual(self.client.get(url).get_json()['bites'][0]['id'],plan['bites'][0]['id'])
  r=self.client.put(url,json={'revision':1,'bites':[bite,{**bite,'pause_s':14}]});self.assertEqual(r.status_code,200);self.assertEqual(len(r.get_json()['bites']),2)
  self.assertEqual(self.client.put(url,json={'revision':1,'bites':[]}).status_code,400)

 def test_bad_values_no_audio_and_replaced_media(self):
  bad={'asset_id':'a','pause_s':7,'source_in_s':2,'source_out_s':11}
  with self.assertRaises(ValueError):validate_bite(self.p,self.root,bad,lambda p:True)
  bad['source_out_s']=5
  with self.assertRaises(ValueError):validate_bite(self.p,self.root,bad,lambda p:False)
  good=validate_bite(self.p,self.root,bad,lambda p:True);self.p['assets'][0]['revision']='v2';self.assertTrue(annotate_plan(self.p,self.root,{'bites':[good]})['bites'][0]['stale_reasons'])
  (self.root/'inputs'/'voice.mp3').write_bytes(b'changed')
  self.assertEqual(len(annotate_plan(self.p,self.root,{'bites':[good]})['bites'][0]['stale_reasons']),2)
