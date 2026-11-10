import tempfile,time,unittest,subprocess
import cv2, imageio_ffmpeg
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import app as server
import preview_render

class ContinuousPreviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patch=patch.object(server,'PROJECTS',Path(self.tmp.name));self.patch.start()
        self.c=server.app.test_client();self.pid=self.c.post('/api/projects',json={'title':'Preview test'}).get_json()['id'];self.root=Path(self.tmp.name)/self.pid
        Image.new('RGB',(200,120),'red').save(self.root/'assets'/'still.jpg')
        p=server.load(self.pid);p['assets']=[{'id':'a','name':'still.jpg','path':'assets/still.jpg','kind':'image','duration':0,'source':'','people':[],'category':'general','verified':False,'rights':'unreviewed','notes':''}]
        p['scenes']=[{'id':'b'*16,'frame_in':0,'frame_out':45,'asset_id':'a','approved':False,'emotion':'calm','purpose':'context','focus':'object','in_s':0,'transition':'cut','person':'','notes':'','context_only':False}];server.save(p)
    def tearDown(self):
        preview_render.JOBS.pop(self.pid,None);self.patch.stop();self.tmp.cleanup()
    def test_preview_is_temporary_and_does_not_bypass_export_validation(self):
        p=server.load(self.pid);self.assertFalse(server.check(p)['can_export'])
        self.assertEqual(self.c.post(f'/api/projects/{self.pid}/continuous-preview',json={}).status_code,202)
        for _ in range(200):
            state=preview_render.status(self.root,self.pid,p['updated'])
            if state['status'] not in ('queued','running'):break
            time.sleep(.05)
        self.assertEqual(state['status'],'done',state)
        r=self.c.get(f'/api/projects/{self.pid}').get_json();self.assertEqual(r['preview']['status'],'done');self.assertFalse(r['validation']['can_export'])
        response=self.c.get(r['preview']['url']);self.assertEqual(response.status_code,200);response.close()
        p=server.load(self.pid);p['title']='Changed';server.save(p)
        self.assertEqual(self.c.get(r['preview']['url']).status_code,404)
        self.assertEqual(self.c.get(f'/api/projects/{self.pid}').get_json()['preview']['status'],'stale')
        self.assertEqual(len(server.load(self.pid)['scenes']),1)

    def test_video_and_narration_preview_keeps_frame_count(self):
        ff=imageio_ffmpeg.get_ffmpeg_exe();source=self.root/'sources'/'motion.mp4';voice=self.root/'inputs'/'voiceover.wav'
        subprocess.run([ff,'-y','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30:duration=4','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
        subprocess.run([ff,'-y','-v','error','-f','lavfi','-i','sine=frequency=440:duration=3',str(voice)],check=True)
        p=server.load(self.pid);p['assets']=[{'id':'v','name':'motion.mp4','path':'sources/motion.mp4','kind':'video','duration':4,'source':'','people':[],'category':'general','verified':False,'rights':'unreviewed','notes':''}];p['voiceover']={'name':'voiceover.wav','path':'inputs/voiceover.wav','duration':3}
        first=p['scenes'][0];first.update(frame_out=45,asset_id='v',in_s=0.5);second=dict(first,id='c'*16,frame_in=45,frame_out=90,in_s=2.0);p['scenes']=[first,second];server.save(p)
        self.assertEqual(self.c.post(f'/api/projects/{self.pid}/continuous-preview',json={}).status_code,202)
        for _ in range(200):
            state=preview_render.status(self.root,self.pid,p['updated'])
            if state['status'] not in ('queued','running'):break
            time.sleep(.05)
        self.assertEqual(state['status'],'done',state)
        output=self.root/'.cache/continuous_preview/preview.mp4';cap=cv2.VideoCapture(str(output));self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),90);cap.release()
        info=subprocess.run([ff,'-hide_banner','-i',str(output)],capture_output=True,text=True).stderr;self.assertIn('Audio:',info)

if __name__=='__main__':unittest.main()
