import json, subprocess, tempfile, time, unittest, zipfile
from pathlib import Path
from unittest.mock import patch
import cv2, imageio_ffmpeg
import numpy as np
import app as server
import scene_exports

class SceneExportsTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(server,'PROJECTS',Path(self.temp.name));self.patch.start()
        self.c=server.app.test_client();self.pid=self.c.post('/api/projects',json={'title':'Scene test'}).get_json()['id'];self.root=Path(self.temp.name)/self.pid
        p=server.load(self.pid);p['scenes']=[]
        for i in range(3):
            p['scenes'].append({'id':f'{i+1:016x}','frame_in':[0,23,61][i],'frame_out':[23,61,90][i],'asset_id':None,'approved':False,'emotion':'calm','purpose':'context','focus':'picture','in_s':0,'transition':'cut','person':'','notes':'','context_only':False})
        server.save(p);self.updated=p['updated'];preview=self.root/'.cache/continuous_preview';preview.mkdir(parents=True)
        ff=imageio_ffmpeg.get_ffmpeg_exe();subprocess.run([ff,'-y','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30:duration=3','-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-shortest',str(preview/'preview.mp4')],check=True)
        (preview/'status.json').write_text(json.dumps({'status':'done','progress':100,'updated':self.updated}))
    def tearDown(self):
        scene_exports.JOBS.pop(self.pid,None);self.patch.stop();self.temp.cleanup()
    def test_preview_source_zip_has_named_frame_accurate_clips(self):
        self.assertEqual(self.c.post(f'/api/projects/{self.pid}/scene-exports',json={'source':'render'}).status_code,400)
        self.assertEqual(self.c.post(f'/api/projects/{self.pid}/scene-exports',json={'source':'preview'}).status_code,202)
        for _ in range(200):
            state=scene_exports.status(self.root,self.pid,self.updated)
            if state['status'] not in ('running','queued'):break
            time.sleep(.05)
        self.assertEqual(state['status'],'done',state)
        out=self.c.get(f'/api/projects/{self.pid}').get_json();self.assertEqual(out['scene_exports']['source_kind'],'preview')
        archive=self.root/'renders/scene_exports'/state['output'];response=self.c.get(out['scene_exports']['url']);self.assertEqual(response.status_code,200);response.close()
        with zipfile.ZipFile(archive) as z:
            self.assertEqual(z.namelist(),['C01.mp4','C02.mp4','C03.mp4'])
            z.extractall(self.root/'clips')
        source=cv2.VideoCapture(str(self.root/'.cache/continuous_preview/preview.mp4'))
        for i in range(3):
            clip=self.root/'clips'/scene_exports.scene_name(i+1);cap=cv2.VideoCapture(str(clip));self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),[23,38,29][i])
            ok,frame=cap.read();cap.release();self.assertTrue(ok)
            source.set(cv2.CAP_PROP_POS_FRAMES,[0,23,61][i]);ok,expected=source.read();self.assertTrue(ok)
            self.assertLess(float(np.mean(np.abs(frame.astype(float)-expected.astype(float)))),6)
            info=subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-hide_banner','-i',str(clip)],capture_output=True,text=True).stderr;self.assertIn('Audio:',info)
        source.release()
        p=server.load(self.pid);p['title']='Change';server.save(p)
        self.assertEqual(self.c.get(f'/api/projects/{self.pid}').get_json()['scene_exports']['status'],'stale')
        self.assertTrue(archive.exists())
    def test_scene_number_scales_beyond_99(self):
        self.assertEqual([scene_exports.scene_name(i) for i in [1,9,10,99,100,1000]],['C01.mp4','C09.mp4','C10.mp4','C99.mp4','C100.mp4','C1000.mp4'])

if __name__=='__main__':unittest.main()
