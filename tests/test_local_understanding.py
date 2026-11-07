import importlib.util
import io
import time
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
import local_understanding as lu

class LocalUnderstandingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.ff = imageio_ffmpeg.get_ffmpeg_exe()

    def tearDown(self):
        self.tmp.cleanup()

    def make_video(self):
        path = self.root / 'video.mp4'
        subprocess.run([self.ff, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=c=red:s=160x90:r=10:d=2',
                        '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10:d=2',
                        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=16000:duration=4',
                        '-filter_complex', '[0:v][1:v]concat=n=2:v=1:a=0[v]', '-map', '[v]', '-map', '2:a',
                        '-c:v', 'mpeg4', '-c:a', 'aac', '-shortest', str(path)], check=True)
        return path

    @unittest.skipUnless(importlib.util.find_spec('scenedetect') and importlib.util.find_spec('librosa'), 'install requirements-local-ai.txt')
    def test_real_video_audio_and_partial_results(self):
        path = self.make_video()
        data={'status':'queued','source_signature':lu._signature(path),'source':path.name,'kind':'video',
              'duration_s':4,'stages':{},'started_at':0}
        with patch.dict('os.environ', {'STORYROOM_WHISPER_MODEL_DIR':''}):
            with patch.object(lu, '_vision', side_effect=RuntimeError('No local model')):
                lu._run(self.root,'test',path,data)
        result=json.loads(lu.result_path(self.root,'test').read_text())
        self.assertEqual(result['status'],'partial')
        scenes=result['stages']['scene_changes']['result']
        self.assertEqual(len(scenes),2)
        self.assertAlmostEqual(scenes[1]['start_s'],2,places=1)
        self.assertGreater(result['stages']['audio_features']['result']['windows'][0]['rms'],0.05)
        self.assertEqual(result['stages']['transcript']['status'],'unavailable')
        self.assertEqual(result['stages']['speakers']['status'],'unavailable')

    def test_ffmpeg_decodes_mp3(self):
        mp3=self.root/'narration.mp3'; wav=self.root/'decoded.wav'
        subprocess.run([self.ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=1',str(mp3)],check=True)
        lu._decode_audio(mp3,wav)
        self.assertGreater(wav.stat().st_size,16000)

    def test_stale_results_not_returned_for_changed_media(self):
        source=self.root/'audio.wav';source.write_bytes(b'first')
        lu._write(lu.result_path(self.root,'narration'), {'status':'complete','source_signature':lu._signature(source)})
        source.write_bytes(b'replaced longer media')
        self.assertEqual(lu.read_result(self.root,'narration',source)['status'],'stale')

    @unittest.skipUnless(importlib.util.find_spec('librosa'), 'install requirements-local-ai.txt')
    def test_uploaded_mp3_analysis_report_api(self):
        import app as server
        mp3=self.root/'narration.mp3'
        subprocess.run([self.ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=1',str(mp3)],check=True)
        projects=self.root/'projects'; projects.mkdir()
        with patch.object(server, 'PROJECTS', projects), patch.dict('os.environ', {'STORYROOM_UNDERSTANDING_AUTO':'0', 'STORYROOM_WHISPER_MODEL_DIR':''}):
            client=server.app.test_client()
            pid=client.post('/api/projects',json={'title':'Local report'}).get_json()['id']
            upload=client.post(f'/api/projects/{pid}/upload',data={'kind':'voiceover','file':(io.BytesIO(mp3.read_bytes()),'narration.mp3')},content_type='multipart/form-data')
            self.assertEqual(upload.status_code,200,upload.get_json())
            url=f'/api/projects/{pid}/understanding/narration'
            self.assertEqual(client.get(url).get_json()['status'],'not_started')
            self.assertIn(client.post(url).get_json()['status'],('queued','running','partial'))
            for _ in range(100):
                result=client.get(url).get_json()
                if result['status'] not in ('queued','running'):break
                time.sleep(.1)
            self.assertEqual(result['status'],'partial',result)
            self.assertEqual(result['stages']['audio_features']['status'],'complete')
            project=client.get(f'/api/projects/{pid}').get_json()
            self.assertIsNone(project['srt'])
            self.assertEqual(project['scenes'],[])

    def test_no_remote_whisper_model_download(self):
        with patch.dict('os.environ', {'STORYROOM_WHISPER_MODEL_DIR':''}):
            with self.assertRaisesRegex(RuntimeError,'model directory'):
                lu._transcribe(self.root/'audio.wav')

if __name__ == '__main__': unittest.main()
