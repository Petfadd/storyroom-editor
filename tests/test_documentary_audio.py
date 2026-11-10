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
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','color=c=blue:s=128x72:r=30:d=3','-f','lavfi','-i','sine=frequency=880:sample_rate=48000:duration=3','-c:v','libx264','-filter:a','volume=-12dB','-c:a','aac','-shortest',str(src)],check=True)
   p={'voiceover':{'path':'inputs/voice.wav','name':'voice.wav','duration':4},'assets':[{'id':'a','kind':'video','name':'footage.mp4','path':'sources/footage.mp4','duration':3,'revision':'v'}]}
   def bite(pause,start,end):return validate_bite(p,root,{'asset_id':'a','pause_s':pause,'source_in_s':start,'source_out_s':end},lambda path:True)
   plan={'version':1,'revision':1,'bites':[bite(1.5,.4,1.4),bite(3,1.5,2)]};before=[hashlib.sha256(f.read_bytes()).hexdigest() for f in (voice,src)];result=build_audio(root,p,plan,out);self.assertAlmostEqual(result['duration_s'],5.5,delta=.025);self.assertTrue(all(x['gain_db']>8 for x in result['sound_bite_levels']));self.assertTrue(all(abs(x['result_lufs']-result['narration_lufs'])<1.5 for x in result['sound_bite_levels']))
   with av.open(str(out)) as f:
    frames=[x.to_ndarray() for x in f.decode(audio=0)]
   pcm=np.concatenate(frames,axis=1).reshape(-1,2).astype(float)[:,0];rate=48000
   original_pcm=subprocess.run([ff,'-v','error','-i',str(voice),'-ac','2','-ar','48000','-f','s16le','-'],stdout=subprocess.PIPE,check=True).stdout
   original_left=np.frombuffer(original_pcm,dtype='<i2').reshape(-1,2)[:,0].astype(float)
   np.testing.assert_array_equal(pcm[:48000],original_left[:48000])
   def peak(start):
    values=pcm[int(start*rate):int((start+.3)*rate)];freq=np.fft.rfftfreq(len(values),1/rate);return freq[np.abs(np.fft.rfft(values)).argmax()]
   def rms(start):
    values=pcm[int(start*rate):int((start+.3)*rate)];return float(np.sqrt(np.mean(values**2)))
   self.assertGreater(rms(1.7),rms(1)*.7);self.assertLess(rms(1.7),rms(1)*1.5)
   for t,freq in [(1,440),(1.7,880),(2.7,440),(3.2,660),(4.1,880),(5.0,660)]:self.assertAlmostEqual(peak(t),freq,delta=5)
   self.assertEqual(before,[hashlib.sha256(f.read_bytes()).hexdigest() for f in (voice,src)]);self.assertEqual(plan['bites'][0]['pause_s'],1.5)
   bad=copy.deepcopy(plan);bad['bites'][0]['asset_revision']='changed';self.assertRaises(ValueError,build_audio,root,p,bad,root/'renders'/'bad.wav')
   self.assertFalse((root/'renders'/'bad.wav').exists())


class AudioStalenessTest(unittest.TestCase):
    """A finished mix must stay usable, or the monitor has nothing to play.

    Background music stopped being baked into the preview mix in v2: the browser
    plays the bed as a live layer. The staleness check nevertheless compared the
    bed against the recorded output, so the moment music was enabled every mix
    was reported stale. The editor then had no playable audio at all -- the
    picture never appeared and playback stopped the instant it started.
    """

    def setUp(self):
        self.source = (Path(__file__).resolve().parent.parent / 'app.py').read_text()

    def block(self):
        start = self.source.index("if state.get('status')=='done':")
        stop = self.source.find("return jsonify(state)", start)
        return self.source[start:stop]

    def test_music_being_enabled_does_not_stale_the_mix(self):
        body = self.block()
        # The comparison may only run for a v1 mix, which really does bake the bed in.
        self.assertIn('legacy_baked', body)
        self.assertIn("state.get('audio_processing_version')!=documentary_audio.AUDIO_PROCESSING_VERSION",
                      body)
        # Any other comparison would mark a v2 mix stale forever.
        self.assertIn('if not stale and legacy_baked and expected_music!=mixed_music',
                      body)

    def test_a_current_mix_still_gets_a_playable_url(self):
        body = self.block()
        # Without a URL the editor has nothing to play, which is the blank
        # monitor the user saw.
        self.assertIn("documentary-audio/{state[", body)
        self.assertIn("state['url']=", body)
