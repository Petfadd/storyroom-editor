import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from documentary_render_job import DocumentaryRenderJob


class DocumentaryRenderJobTest(unittest.TestCase):
 def setup_job(self,root,renderer):
  for name in ('sources','inputs','renders','reviews'):(root/name).mkdir()
  (root/'sources/video.mp4').write_bytes(b'original video');(root/'inputs/voice.wav').write_bytes(b'original voice');(root/'inputs/captions.srt').write_text('original SRT')
  project={'id':'0123456789abcdef','updated':1,'voiceover':{'path':'inputs/voice.wav'},'assets':[{'id':'a','path':'sources/video.mp4'}],'scenes':[{'asset_id':'a'}]};plan={'revision':1,'bites':[]};states=[];published=[]
  def publish(project,plan,candidate,final,record,adjusted):published.append(True);candidate.replace(final)
  job=DocumentaryRenderJob(root,project,plan,160,90,{'enhance_picture':False,'srt_subtitles':False},None,None,lambda *args:None,publish,states.append)
  return job,states,published

 def test_changed_source_does_not_publish(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);job,states,published=self.setup_job(root,None)
   def fake(root,p,plan,dest,*args,**kw):
    dest.write_bytes(b'candidate');(root/'sources/video.mp4').write_bytes(b'changed');return {}
   with patch('documentary_final_renderer.render',side_effect=fake):job.start();job.worker_thread.join(10)
   self.assertEqual(job.snapshot()['status'],'error');self.assertIn('changed',job.snapshot()['error']);self.assertFalse(published);self.assertFalse(list((root/'renders').iterdir()))

 def test_cancel_stops_before_publish(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);job,states,published=self.setup_job(root,None);job.cancel()
   with patch('documentary_final_renderer.render') as render:job.start();job.worker_thread.join(10)
   self.assertEqual(job.snapshot()['status'],'cancelled');render.assert_not_called();self.assertFalse(published)

 def test_changed_project_at_publication_does_not_publish(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);job,states,published=self.setup_job(root,None);checks=[]
   def current(*args):
    checks.append(True)
    if len(checks)>1:raise ValueError('The project changed during rendering.')
   job.validate_current=current
   def fake(root,p,plan,dest,*args,**kw):dest.write_bytes(b'candidate');return {}
   with patch('documentary_final_renderer.render',side_effect=fake):job.start();job.worker_thread.join(10)
   self.assertEqual(job.snapshot()['status'],'error');self.assertIn('project changed',job.snapshot()['error']);self.assertFalse(published);self.assertFalse(list((root/'renders').iterdir()))
