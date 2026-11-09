import copy,subprocess,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import cv2
import app as server

SCRIPT='''Shot: Srt01
Narration timeline: 00:00:00.000 --> 00:00:09.000
Exact narration: First words
Full source: Source.mp4
Source IN: 00:00:01.000
Source OUT: 00:00:10.000
Treatment: VIDEO, 1.0×, loop OFF
Visual direction: Synthetic video
Continuity: Review only
On-screen caption: On screen text
Shot: Srt02
Narration timeline: 00:00:09.000 --> 00:00:10.000
Exact narration: Last words
Photograph: Person.jpg
Shows: Test Person (victim). Identification basis: synthetic test.
Treatment: STILL — uploaded photograph, static, no motion
Visual direction: Synthetic photo
Continuity: Review only
On-screen caption: None
'''
BLOCKS=[{'number':1,'start_ms':0,'end_ms':9000,'text':'First words'},{'number':2,'start_ms':9000,'end_ms':10000,'text':'Last words'}]

class ReviewDraftRenderTest(unittest.TestCase):
 def test_complete_strict_review_renders_without_unblocking_final(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Test','picture_policy':'mixed','size':'320x180'}).get_json()['id'];root=Path(tmp)/pid;p=server.load(pid);p['srt']={'name':'test.srt','blocks':copy.deepcopy(BLOCKS)};p['production_script']={'name':'test.txt','text':SCRIPT};(root/'inputs/captions.srt').write_text('1\n00:00:00,000 --> 00:00:09,000\nFirst words\n\n2\n00:00:09,000 --> 00:00:10,000\nLast words\n')
   ff=server.FFMPEG
   subprocess.run([ff,'-loglevel','error','-y','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=12','-c:v','libx264','-pix_fmt','yuv420p',str(root/'sources/video.mp4')],check=True)
   subprocess.run([ff,'-loglevel','error','-y','-f','lavfi','-i','sine=frequency=440:duration=10','-c:a','libmp3lame',str(root/'inputs/voiceover.mp3')],check=True);Image.new('RGB',(120,80),(220,10,10)).save(root/'assets/photo.jpg')
   p['voiceover']={'name':'fixture.mp3','duration':10,'path':'inputs/voiceover.mp3'};p['assets']=[{'id':'v','name':'Source.mp4','original_name':'Source.mp4','path':'sources/video.mp4','kind':'video','duration':12,'source':'synthetic','people':[],'category':'general','verified':True,'rights':'unreviewed','notes':''},{'id':'i','name':'Person.jpg','original_name':'Person.jpg','path':'assets/photo.jpg','kind':'image','duration':0,'source':'synthetic','people':['Test Person'],'category':'person','verified':True,'rights':'unreviewed','notes':''}];server.save(p)
   res=c.post(f'/api/projects/{pid}/script-draft',json={'moving_only':True,'require_ready':True});self.assertEqual(res.status_code,200,res.get_data(as_text=True));p=server.load(pid)
   for s in p['scenes']:s['approved']=True
   server.save(p);self.assertTrue(server.check(p)['can_export']);self.assertFalse(server.check(p,True)['can_export'])
   broken=copy.deepcopy(p);broken['scenes'][0]['asset_id']=None;self.assertFalse(server.check(broken)['can_export'])
   broken=copy.deepcopy(p);broken['voiceover']['duration']=10.2;self.assertTrue(any('must not trim' in e for e in server.check(broken)['errors']))
   res=c.post(f'/api/projects/{pid}/render',json={'final':False});self.assertEqual(res.status_code,202,res.get_data(as_text=True));job=server.JOBS[pid];job.worker_thread.join(timeout=90);state=job.snapshot();self.assertEqual(state['status'],'done',state)
   cap=cv2.VideoCapture(str(root/'renders'/state['output']));count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT));fps=cap.get(cv2.CAP_PROP_FPS);cap.set(cv2.CAP_PROP_POS_FRAMES,30);ok,clean=cap.read();cap.release();self.assertTrue(ok);self.assertEqual((count,fps),(300,30));self.assertFalse(state['captions'])
   res=c.post(f'/api/projects/{pid}/render',json={'final':False,'srt_subtitles':True});self.assertEqual(res.status_code,400)
   aligned={'status':'complete','phrases':[{'text':'First words','start_ms':0,'end_ms':9000},{'text':'Last words','start_ms':9000,'end_ms':10000}]}
   with patch('caption_alignment.state',return_value=aligned):res=c.post(f'/api/projects/{pid}/render',json={'final':False,'srt_subtitles':True})
   self.assertEqual(res.status_code,202,res.get_data(as_text=True));job=server.JOBS[pid];job.worker_thread.join(timeout=90);state=job.snapshot();self.assertEqual(state['status'],'done',state)
   cap=cv2.VideoCapture(str(root/'renders'/state['output']));cap.set(cv2.CAP_PROP_POS_FRAMES,30);ok,srt_frame=cap.read();cap.release();self.assertTrue(ok);self.assertGreater(float(abs(clean.astype('float32')-srt_frame.astype('float32')).mean()),0.05);self.assertTrue(state['captions']);self.assertEqual((root/'renders'/state['output']).with_suffix('.srt').read_text(),(root/'inputs/captions.srt').read_text())
   res=c.post(f'/api/projects/{pid}/render',json={'final':False,'script_captions':True});self.assertEqual(res.status_code,202,res.get_data(as_text=True));job=server.JOBS[pid];job.worker_thread.join(timeout=90);state=job.snapshot();self.assertEqual(state['status'],'done',state)
   cap=cv2.VideoCapture(str(root/'renders'/state['output']));cap.set(cv2.CAP_PROP_POS_FRAMES,30);ok,burned=cap.read();cap.release();self.assertTrue(ok);self.assertGreater(float(abs(clean.astype('float32')-burned.astype('float32')).mean()),0.05);self.assertTrue(state['captions']);self.assertEqual((root/'renders'/state['output']).with_suffix('.srt').read_text(),(root/'inputs/captions.srt').read_text())
   display=(root/'reviews'/(Path(state['output']).stem+'_script_captions.srt')).read_text();self.assertIn('On screen text',display);self.assertNotIn('Review only',display);self.assertNotIn('None',display)
