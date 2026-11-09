import copy, hashlib, subprocess, tempfile, unittest
from pathlib import Path
import numpy as np
import av
import imageio_ffmpeg
from documentary_audio import build_audio
from documentary import validate_bite

class DocumentaryAudioTest(unittest.TestCase):
 def test_actual_handoffs_keep_narration_and_source_files_intact(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'sources').mkdir();(root/'inputs').mkdir();(root/'renders').mkdir();ff=imageio_ffmpeg.get_ffmpeg_exe()
   voice=root/'inputs'/'voice.wav';src=root/'sources'/'footage.mp4';out=root/'renders'/'preview.wav'
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:sample_rate=48000:duration=2','-f','lavfi','-i','sine=frequency=660:sample_rate=48000:duration=2','-filter_complex','[0:a][1:a]concat=n=2:v=0:a=1[out]','-map','[out]','-c:a','pcm_s16le',str(voice)],check=True)
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','color=c=blue:s=128x72:r=30:d=3','-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=3','-c:v','libx264','-c:a','aac','-shortest',str(src)],check=True)
   p={'voiceover':{'path':'inputs/voice.wav','name':'voice.wav','duration':4},'assets':[{'id':'a','kind':'video','name':'footage.mp4','path':'sources/footage.mp4','duration':3,'revision':'v'}]}
   def bite(pause,start,end):return validate_bite(p,root,{'asset_id':'a','pause_s':pause,'source_in_s':start,'source_out_s':end},lambda path:True)
   plan={'version':1,'revision':1,'bites':[bite(1.5,.4,1.4),bite(3,1.5,2)]};before=[hashlib.sha256(f.read_bytes()).hexdigest() for f in (voice,src)];result=build_audio(root,p,plan,out);self.assertAlmostEqual(result['duration_s'],5.5,delta=.025)
   with av.open(str(out)) as f:
    frames=[x.to_ndarray() for x in f.decode(audio=0)]
   pcm=np.concatenate(frames,axis=1).reshape(-1,2).astype(float)[:,0];rate=48000
   def peak(start):
    values=pcm[int(start*rate):int((start+.3)*rate)];freq=np.fft.rfftfreq(len(values),1/rate);return freq[np.abs(np.fft.rfft(values)).argmax()]
   for t,freq in [(1,440),(1.7,880),(2.7,440),(3.2,660),(4.1,880),(5.0,660)]:self.assertAlmostEqual(peak(t),freq,delta=5)
   self.assertEqual(before,[hashlib.sha256(f.read_bytes()).hexdigest() for f in (voice,src)]);self.assertEqual(plan['bites'][0]['pause_s'],1.5)
   bad=copy.deepcopy(plan);bad['bites'][0]['asset_revision']='changed';self.assertRaises(ValueError,build_audio,root,p,bad,root/'renders'/'bad.wav')
   self.assertFalse((root/'renders'/'bad.wav').exists())
