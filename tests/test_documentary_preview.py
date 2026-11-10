import unittest
from documentary_preview import picture_plan
class PicturePlanTest(unittest.TestCase):
 def setUp(self):
  self.p={'voiceover':{'duration':4},'assets':[{'id':'a','kind':'video','duration':20}],'scenes':[{'frame_in':0,'frame_out':60,'asset_id':'a','in_s':1},{'frame_in':60,'frame_out':120,'asset_id':'a','in_s':5}]}
  self.b={'id':'b','pause_s':1,'source_in_s':10,'source_out_s':12,'asset_id':'a','asset_name':'a','created':1};self.plan={'bites':[self.b]}
 def test_cut_and_resume_at_original_picture_point(self):
  parts,tail,timeline=picture_plan(self.p,self.plan)
  self.assertEqual([(x['kind'],x['start'],x['frames']) for x in parts],[('base',1,30),('sound_bite',10,60),('base',2,30),('base',5,60)])
  self.assertEqual(timeline['duration_s'],6);self.assertEqual(tail,0)
 def test_tail_requires_explicit_consent(self):
  self.p['voiceover']['duration']=3.9
  with self.assertRaisesRegex(ValueError,'Confirm'):picture_plan(self.p,self.plan)
  self.assertAlmostEqual(picture_plan(self.p,self.plan,True)[1],.1)
 def test_fractional_frame_or_missing_media_blocks(self):
  self.b['pause_s']=1.01
  with self.assertRaisesRegex(ValueError,'align'):picture_plan(self.p,self.plan)
  self.b['pause_s']=1;self.p['scenes'][1]['asset_id']='missing'
  with self.assertRaises(ValueError):picture_plan(self.p,self.plan)

class ActualPreviewTest(unittest.TestCase):
 def test_real_preview_mixes_only_bite_and_resumes_narration(self):
  import tempfile,subprocess,hashlib
  from pathlib import Path
  import imageio_ffmpeg,av,numpy as np
  from documentary_preview import build
  from documentary import validate_bite
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'sources').mkdir();(root/'inputs').mkdir();(root/'renders').mkdir();ff=imageio_ffmpeg.get_ffmpeg_exe()
   voice=root/'inputs/voice.wav';source=root/'sources/video.mp4'
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=2:sample_rate=48000','-f','lavfi','-i','sine=frequency=660:duration=2:sample_rate=48000','-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[a]','-map','[a]','-c:a','pcm_s16le',str(voice)],check=True)
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=6','-f','lavfi','-i','sine=frequency=880:duration=6:sample_rate=48000','-c:v','libx264','-threads','2','-c:a','aac','-shortest',str(source)],check=True)
   p={'voiceover':{'path':'inputs/voice.wav','name':'voice.wav','duration':4},'assets':[{'id':'a','kind':'video','name':'video.mp4','path':'sources/video.mp4','duration':6,'revision':'v'}],'scenes':[{'frame_in':0,'frame_out':120,'asset_id':'a','in_s':0}]}
   b=validate_bite(p,root,{'asset_id':'a','pause_s':1,'source_in_s':3,'source_out_s':4},lambda p:True);plan={'version':1,'revision':1,'bites':[b]};hashes=[hashlib.sha256(x.read_bytes()).hexdigest() for x in (voice,source)];out=root/'renders/out.mp4'
   result=build(root,p,plan,out);self.assertAlmostEqual(result['duration_s'],5)
   with av.open(str(out)) as media:
    self.assertEqual(media.streams.video[0].frames,150);frames=[x.to_ndarray() for x in media.decode(audio=0)]
   # PyAV AAC output uses planar float channels.
   pcm=np.concatenate(frames,axis=1)[0];rate=48000
   def peak(t):
    y=pcm[int(t*rate):int((t+.25)*rate)];return np.fft.rfftfreq(len(y),1/rate)[np.abs(np.fft.rfft(y)).argmax()]
   for t,f in [(0.5,440),(1.3,880),(2.3,440),(3.3,660)]:self.assertAlmostEqual(peak(t),f,delta=6)
   self.assertEqual(hashes,[hashlib.sha256(x.read_bytes()).hexdigest() for x in (voice,source)])
