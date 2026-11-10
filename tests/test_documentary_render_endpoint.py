import copy, subprocess, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
import imageio_ffmpeg
import app as server
from documentary_storyboard import prepare
from tests.test_documentary_beats import SCRIPT


class DocumentaryRenderEndpointTest(unittest.TestCase):
 def test_safe_documentary_mp4_and_optional_adjusted_srt(self):
  with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
   client=server.app.test_client();pid=client.post('/api/projects',json={'title':'Fixture','edit_mode':'documentary','size':'160x90'}).get_json()['id'];root=Path(tmp)/pid;p=server.load(pid)
   ff=imageio_ffmpeg.get_ffmpeg_exe();voice=root/'inputs/voice.wav';source=root/'sources/video.mp4'
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=4:sample_rate=48000','-c:a','pcm_s16le',str(voice)],check=True)
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=15','-f','lavfi','-i','sine=frequency=880:duration=15:sample_rate=48000','-c:v','libx264','-threads','2','-c:a','aac','-shortest',str(source)],check=True)
   original='1\n00:00:00,000 --> 00:00:02,000\nOriginal one\n\n2\n00:00:02,000 --> 00:00:04,000\nOriginal two\n';(root/'inputs/captions.srt').write_text(original)
   p.update(production_script={'name':'test.txt','text':SCRIPT},voiceover={'path':'inputs/voice.wav','name':'voice.wav','duration':4},srt={'name':'test.srt','blocks':server.parse_srt(original)},assets=[{'id':'a','name':'S01.mp4','original_name':'S01.mp4','kind':'video','path':'sources/video.mp4','duration':15,'revision':'v','reuse_review':{'decision':'approved','attested':True,'details':'Synthetic footage'}}])
   p,plan=prepare(p,root,[],[],False,lambda _:True)
   for s in p['scenes']:s['approved']=True
   server.write(root/'project.json',p);server.write(root/'documentary_plan.json',plan);before=(root/'project.json').read_bytes()
   body={'final':True,'omit_fade':True,'strict_checked_version':p['updated'],'enhance_picture':False,'srt_subtitles':True,'subtitle_splits':[]}
   try:
    with patch.object(server,'check',return_value={'errors':[],'notes':[],'can_export':True}):
     self.assertEqual(client.post(f'/api/projects/{pid}/render',json={**body,'strict_checked_version':0}).status_code,400)
     self.assertEqual(client.post(f'/api/projects/{pid}/render',json={**body,'omit_fade':False}).status_code,400)
     broken=copy.deepcopy(p);broken['scenes'][0]['approved']=False;server.write(root/'project.json',broken)
     self.assertEqual(client.post(f'/api/projects/{pid}/render',json=body).status_code,400)
     broken=copy.deepcopy(p);broken['assets'][0]['reuse_review']['attested']=False;server.write(root/'project.json',broken)
     self.assertEqual(client.post(f'/api/projects/{pid}/render',json=body).status_code,400)
     server.write(root/'project.json',p)
     r=client.post(f'/api/projects/{pid}/render',json=body);self.assertEqual(r.status_code,202,r.get_data(as_text=True))
     job=server.JOBS[pid];job.worker_thread.join(timeout=60);state=job.snapshot();self.assertEqual(state['status'],'done',state)
     self.assertTrue((root/'renders'/state['output']).is_file())
     public=client.get(f'/api/projects/{pid}').get_json();self.assertTrue(public['render']['current']);self.assertTrue(public['render']['options']['srt_subtitles']);self.assertIn('adjusted_srt_url',public['render'])
     adjusted=(root/'renders'/Path(state['output']).with_suffix('.adjusted.srt')).read_text();self.assertIn('00:00:04,000 --> 00:00:06,000',adjusted)
     self.assertEqual((root/'inputs/captions.srt').read_text(),original);self.assertEqual((root/'project.json').read_bytes(),before)
     # A replaced source must mark the old result as not current, even if the project version did not change.
     stat=source.stat();__import__('os').utime(source,ns=(stat.st_atime_ns,stat.st_mtime_ns+1000000))
     self.assertFalse(client.get(f'/api/projects/{pid}').get_json()['render']['current'])
   finally:server.JOBS.pop(pid,None)
