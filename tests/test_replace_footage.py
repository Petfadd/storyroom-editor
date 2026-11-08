import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import imageio_ffmpeg
import subprocess
import app as server

class ReplaceFootageTest(unittest.TestCase):
 @classmethod
 def setUpClass(cls):
  cls.tmpmedia=tempfile.TemporaryDirectory();cls.video={}
  for seconds in [2,5]:
   path=Path(cls.tmpmedia.name)/f'{seconds}.mp4'
   subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i',f'testsrc2=size=96x64:rate=30:duration={seconds}','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
   cls.video[seconds]=path.read_bytes()
 @classmethod
 def tearDownClass(cls):cls.tmpmedia.cleanup()
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(server,'PROJECTS',Path(self.tmp.name));self.patch.start();self.queue=patch('automatic.maybe_queue');self.queue.start()
  self.c=server.app.test_client();self.pid=self.c.post('/api/projects',json={'title':'Replacement'}).get_json()['id'];self.root=Path(self.tmp.name)/self.pid
 def tearDown(self):self.queue.stop();self.patch.stop();self.tmp.cleanup()
 def upload(self,name,data,stream=True):
  if stream:return self.c.post(f'/api/projects/{self.pid}/media-stream?name={name}',data=data,content_type='application/octet-stream')
  return self.c.post(f'/api/projects/{self.pid}/upload',data={'kind':'asset','file':(io.BytesIO(data),name)},content_type='multipart/form-data')
 def scene(self,aid,frames=90):
  return {'id':'scene1','srt_blocks':[1,1],'frame_in':0,'frame_out':frames,'narration':'words','purpose':'review','emotion':'neutral','focus':'test','category':'general','person':'','asset_id':aid,'in_s':0,'motion':'auto','transition':'cut','context_only':False,'notes':'','approved':True,'profile':'documentary','focus_position':'center','motion_strength':'subtle'}
 def test_replace_preserves_timeline_and_retires_old_source(self):
  first=self.upload('S14.mp4',self.video[5]).get_json();a=first['assets'][0];oldpath=self.root/a['path'];oldurl=a['url'];p=server.load(self.pid);p['scenes']=[self.scene(a['id'])];server.save(p)
  second=self.upload('S14.mp4',self.video[5],False);self.assertEqual(second.status_code,200,second.get_data(as_text=True));p=second.get_json();self.assertEqual(len(p['assets']),1);self.assertEqual(p['assets'][0]['id'],a['id']);self.assertEqual(p['assets'][0]['original_name'],'S14.mp4');self.assertNotEqual(oldurl,p['assets'][0]['url']);self.assertFalse(oldpath.exists());self.assertEqual((self.root/p['assets'][0]['path']).read_bytes(),self.video[5]);self.assertEqual(p['scenes'][0]['asset_id'],a['id']);self.assertFalse(p['scenes'][0]['approved']);self.assertTrue(any('replaced source' in e for e in p['validation']['errors']));self.assertEqual(p['replaced_video']['affected_scenes'],1)
  approved=self.c.patch(f'/api/projects/{self.pid}/scenes/scene1',json={'approved':True});self.assertEqual(approved.status_code,200);self.assertNotIn('source_replacement_pending',approved.get_json()['scenes'][0])
 def test_invalid_upload_does_not_replace_and_short_source_unassigns(self):
  a=self.upload('S15.mp4',self.video[5]).get_json()['assets'][0];p=server.load(self.pid);p['scenes']=[self.scene(a['id'])];server.save(p)
  failed=self.upload('S15.mp4',b'invalid');self.assertEqual(failed.status_code,400);p=server.load(self.pid);self.assertEqual((self.root/p['assets'][0]['path']).read_bytes(),self.video[5]);self.assertEqual(p['scenes'][0]['asset_id'],a['id'])
  result=self.upload('S15.mp4',self.video[2]).get_json();self.assertEqual(result['replaced_video']['unassigned_scenes'],1);self.assertIsNone(result['scenes'][0]['asset_id']);self.assertIn('too short',result['scenes'][0]['replacement_issue'])
 def test_same_number_different_filename_and_existing_duplicates(self):
  first=self.upload('S14.mp4',self.video[5]).get_json()['assets'][0];self.upload('S014.mp4',self.video[5]);p=server.load(self.pid);dup=dict(first,id='duplicate',path='sources/duplicate.mp4');(self.root/dup['path']).write_bytes(self.video[5]);p['assets'].append(dup);p['scenes']=[self.scene('duplicate')];server.save(p)
  result=self.upload('s14.MP4',self.video[5]).get_json();self.assertEqual(len(result['assets']),2);self.assertEqual(result['replaced_video']['replaced_count'],2);self.assertEqual(result['scenes'][0]['asset_id'],first['id']);self.assertFalse((self.root/'sources/duplicate.mp4').exists());self.assertEqual({x['original_name'] for x in result['assets']},{'s14.MP4','S014.mp4'})
 def test_analysis_busy_does_not_delete_current_file(self):
  from local_understanding import _GUARD,_RUNNING
  asset=self.upload('S14.mp4',self.video[5]).get_json()['assets'][0];key=(str(self.root),asset['id'])
  with _GUARD:_RUNNING.add(key)
  try:r=self.upload('S14.mp4',self.video[2]);self.assertEqual(r.status_code,400);self.assertTrue((self.root/asset['path']).exists())
  finally:
   with _GUARD:_RUNNING.discard(key)

if __name__=='__main__':unittest.main()
