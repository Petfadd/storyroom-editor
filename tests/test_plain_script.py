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

class NativeStaticReviewTest(unittest.TestCase):
 def test_preview_uses_original_interval_and_upgrades_old_gap(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  import app as server
  with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Synthetic'}).get_json()['id'];root=Path(tmp)/pid
   p=server.load(pid);p['srt']={'blocks':copy.deepcopy(BLOCKS)};p['production_script']={'text':SCRIPT};
   for name in ('First video.mp4','S02.mp4'):
    path=root/'sources'/name;path.write_bytes(b'placeholder');p['assets'].append({'id':name,'name':name,'original_name':name,'kind':'video','duration':20,'path':'sources/'+name,'verified':False,'source':'','people':[],'category':'general','rights':'unreviewed','notes':''})
   server.save(p)
   response=c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True});self.assertEqual(response.status_code,200,response.get_data(as_text=True))
   saved=server.load(pid);native=saved['scenes'][1];self.assertEqual(native['asset_id'],'S02.mp4');self.assertEqual(native['in_s'],5);self.assertTrue(native['plan_row']['native_static_interval']);self.assertTrue(saved['review_only_draft']);self.assertFalse(server.check(saved)['can_export']);self.assertEqual(len(saved['assets']),2)
   native['asset_id']=None;native['in_s']=0;native['missing_video_out']=True;native['plan_row'].pop('native_static_interval');native['plan_row'].pop('native_static_review');saved['script_application']['issues'].append('Beat Srt002: omitted');server.save(saved)
   response=c.post(f'/api/projects/{pid}/native-static-review');self.assertEqual(response.status_code,200,response.get_data(as_text=True));result=response.get_json();self.assertEqual(result['native_static_update']['placed'],['Srt002']);saved=server.load(pid);self.assertEqual(saved['scenes'][1]['asset_id'],'S02.mp4');self.assertFalse(saved['scenes'][1]['missing_video_out']);self.assertEqual(len(saved['assets']),2)
   self.assertEqual(saved['production_script']['text'],SCRIPT);self.assertEqual(saved['srt']['blocks'],BLOCKS)

class HeadedScriptTest(unittest.TestCase):
 def test_headed_script_and_native_format(self):
  text='# Project\n## Editor Assembly Instructions\nReview original sources.\n## Storyboard Production Script\n\n'+SCRIPT.replace('STILL (native static interval)','STILL — native static interval in source video')+'\n---\n\n## Final Coverage Summary\nClaims need review.\n'
  rows,mode=rows_for({'production_script':{'text':text},'srt':{'blocks':copy.deepcopy(BLOCKS)}})
  self.assertEqual(mode,'detailed_production_script');self.assertTrue(rows[1]['native_static_interval']);self.assertEqual(rows[1]['source_out_ms'],9000)
  with self.assertRaises(ValueError):parse_plain_script('Shot: Srt999\n'+text,BLOCKS)
 def test_review_rebuild_preserves_manual_edits(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  import app as server
  with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Test'}).get_json()['id'];root=Path(tmp)/pid;p=server.load(pid)
   p['srt']={'blocks':copy.deepcopy(BLOCKS)};p['production_script']={'text':SCRIPT}
   for name in ('First video.mp4','S02.mp4'):
    (root/'sources'/name).write_bytes(b'test');p['assets'].append({'id':name,'name':name,'original_name':name,'kind':'video','duration':20,'path':'sources/'+name,'verified':False,'source':'','people':[],'category':'general','rights':'unreviewed','notes':''})
   server.save(p)
   self.assertEqual(c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True}).status_code,200)
   self.assertEqual(c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True}).status_code,400)
   self.assertEqual(c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True,'replace_review':True}).status_code,200)
   saved=server.load(pid);saved['manual_scene_edits']=True;server.save(saved)
   before=server.load(pid)['scenes'];self.assertEqual(c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True,'replace_review':True}).status_code,400);self.assertEqual(server.load(pid)['scenes'],before)

class UploadedPhotoTest(unittest.TestCase):
 def test_photo_syntax_is_explicit_and_has_no_source_clock(self):
  photo=SCRIPT.replace('Full source: S02.mp4','Full source: Victim Photo.jpg').replace('Source IN: 00:00:05,000\nSource OUT: 00:00:09,000\nTreatment: STILL (native static interval)','Source IN: N/A\nSource OUT: N/A\nTreatment: PHOTO')
  row=parse_plain_script(photo,BLOCKS)[1];self.assertTrue(row['uploaded_photo']);self.assertFalse(row['native_static_interval']);self.assertEqual(row['source_out_ms'],4000)
  for bad in [photo.replace('Victim Photo.jpg','S02.mp4'),photo.replace('Source IN: N/A','Source IN: 00:00:01,000'),photo.replace('Treatment: PHOTO','Treatment: VIDEO, 1.0×, loop OFF')]:
   with self.assertRaises(ValueError):parse_plain_script(bad,BLOCKS)
 def test_draft_assigns_only_original_exact_photo_and_reports_budget(self):
  import tempfile
  from pathlib import Path
  from unittest.mock import patch
  import app as server
  from PIL import Image
  photo=SCRIPT.replace('Full source: S02.mp4','Full source: Victim Photo.jpg').replace('Source IN: 00:00:05,000\nSource OUT: 00:00:09,000\nTreatment: STILL (native static interval)','Source IN: N/A\nSource OUT: N/A\nTreatment: PHOTO')
  with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Photo test'}).get_json()['id'];root=Path(tmp)/pid;p=server.load(pid);p['srt']={'blocks':copy.deepcopy(BLOCKS)};p['production_script']={'text':photo}
   (root/'sources/video.mp4').write_bytes(b'video');Image.new('RGB',(30,20),'red').save(root/'assets/photo.jpg')
   p['assets']=[{'id':'v','name':'First video.mp4','original_name':'First video.mp4','kind':'video','duration':20,'path':'sources/video.mp4','verified':False,'source':'','people':[],'category':'general','rights':'unreviewed','notes':''},{'id':'i','name':'Victim Photo.jpg','original_name':'Victim Photo.jpg','kind':'image','duration':0,'path':'assets/photo.jpg','verified':False,'source':'','people':[],'category':'general','rights':'unreviewed','notes':''}];server.save(p)
   result=c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True});self.assertEqual(result.status_code,200,result.get_data(as_text=True));p=server.load(pid);scene=p['scenes'][1]
   self.assertEqual(scene['asset_id'],'i');self.assertEqual(scene['script_camera']['move'],'static');self.assertNotIn('missing_video_out',scene);self.assertTrue(any('above the 10%' in e for e in server.check(p)['errors']));self.assertEqual(p['production_script']['text'],photo);self.assertEqual(p['srt']['blocks'],BLOCKS);self.assertEqual(len(p['assets']),2)
   p['assets'].append(dict(p['assets'][1],id='duplicate'));server.save(p);result=c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True,'replace_review':True});self.assertEqual(result.status_code,200);self.assertIsNone(server.load(pid)['scenes'][1]['asset_id'])
   p=server.load(pid);p['assets'].pop();p['assets'][1]['derived_from']={'kind':'image'};server.save(p);c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True,'replace_review':True});self.assertIsNone(server.load(pid)['scenes'][1]['asset_id'])
