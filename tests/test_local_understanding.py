import base64
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
        self.assertEqual(result['stages']['speakers']['status'],'not_requested')

    @unittest.skipUnless(importlib.util.find_spec('librosa'), 'install requirements-local-ai.txt')
    def test_low_energy_intervals_cross_chunks(self):
        import wave
        import numpy as np
        wav=self.root/'silence.wav'
        y=np.full(12*16000, 8192, dtype='<i2')
        y[9*16000:11*16000]=0
        with wave.open(str(wav),'wb') as f:
            f.setnchannels(1); f.setsampwidth(2); f.setframerate(16000); f.writeframes(y.tobytes())
        result=lu._audio_features(wav)
        self.assertEqual(result['low_energy_intervals'],[{'start_s':9.0,'end_s':11.0}])

    def test_vision_samples_long_scene_and_spreads_budget(self):
        scenes=[{'start_s':0,'end_s':1000}]
        samples=lu._sample_frames(scenes,4)
        self.assertEqual(len(samples),4)
        self.assertGreater(samples[0][0],0)
        self.assertGreater(samples[-1][0],850)
        self.assertLess(samples[-1][0],1000)

    @unittest.skipUnless(importlib.util.find_spec('onnxruntime'), 'install requirements-local-ai.txt')
    def test_sound_event_windows_and_unclassified_short_tail(self):
        import wave
        import numpy as np
        import onnxruntime as ort
        from types import SimpleNamespace
        wav=self.root/'audio.wav'; model=self.root/'model.onnx';model.write_bytes(b'test')
        with wave.open(str(wav),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(bytes(2 * (3*16000+8000)))
        class FakeSession:
            def __init__(self,*args,**kwargs):
                self.kwargs=kwargs
            def get_inputs(self):return [SimpleNamespace(name='input',shape=[1,48000],type='tensor(float)')]
            def get_outputs(self):return [SimpleNamespace(name='scores')]
            def run(self,outputs,feed):
                result=np.zeros((6,521),dtype=np.float32);result[:,0]=0.9;return [result]
        with patch.dict('os.environ',{'STORYROOM_YAMNET_MODEL':str(model)}), patch.object(ort,'InferenceSession',FakeSession):
            windows=lu._sound_events(wav)['windows']
        self.assertEqual((windows[0]['start_s'],windows[0]['end_s']),(0,3))
        self.assertEqual(windows[0]['predictions'][0]['label'],'Speech')
        self.assertTrue(windows[1]['too_short'])
        self.assertEqual(windows[1]['predictions'],[])

    def test_speakers_aligned_by_overlap_without_claiming_identity(self):
        transcript={'segments':[{'start_s':1,'end_s':3,'words':[{'start_s':1,'end_s':2}, {'start_s':2,'end_s':3}, {'start_s':4,'end_s':5}]}]}
        speakers={'turns':[{'start_s':0,'end_s':2,'speaker':'SPEAKER_00'},{'start_s':2,'end_s':3,'speaker':'SPEAKER_01'}]}
        lu._align_speakers(transcript,speakers)
        self.assertEqual([w['speaker'] for w in transcript['segments'][0]['words']],['SPEAKER_00','SPEAKER_01',None])

    @unittest.skipUnless(importlib.util.find_spec('faster_whisper'), 'install requirements-local-ai.txt')
    def test_whisper_uses_only_local_model_and_preserves_word_times(self):
        import faster_whisper
        from types import SimpleNamespace
        model=self.root/'whisper';model.mkdir()
        for name in ('model.bin','config.json','tokenizer.json'):(model/name).write_bytes(b'test')
        seen={}
        class FakeModel:
            def __init__(self, path, **kwargs):seen.update(path=path,**kwargs)
            def transcribe(self, path, **kwargs):
                seen.update(kwargs)
                return iter([SimpleNamespace(start=1.25,end=2.1,text=' Hello',words=[SimpleNamespace(start=1.3,end=2.0,word=' Hello')])]),SimpleNamespace(language='en',language_probability=.9)
        with patch.dict('os.environ',{'STORYROOM_WHISPER_MODEL_DIR':str(model)}), patch.object(faster_whisper,'WhisperModel',FakeModel):
            result=lu._transcribe(self.root/'sound.wav')
        self.assertEqual(seen['device'],'cpu');self.assertTrue(seen['local_files_only'])
        self.assertTrue(seen['word_timestamps'])
        self.assertEqual(result['segments'][0]['words'][0]['start_s'],1.3)

    @unittest.skipUnless(importlib.util.find_spec('faster_whisper'), 'install requirements-local-ai.txt')
    def test_transcription_checkpoints_before_failure(self):
        import faster_whisper
        from types import SimpleNamespace
        model=self.root/'whisper';model.mkdir()
        for name in ('model.bin','config.json','tokenizer.json'):(model/name).write_bytes(b'test')
        def segments():
            yield SimpleNamespace(start=0,end=1,text=' A line.',words=[])
            raise RuntimeError('decode failed')
        class FakeModel:
            def __init__(self,*args,**kwargs):pass
            def transcribe(self,*args,**kwargs):return segments(),SimpleNamespace(language='en',language_probability=.9)
        data={'stages':{}};report=self.root/'partial.json'
        def checkpoint(result):data['stages']['transcript']={'status':'running','result':result};lu._write(report,data)
        with patch.dict('os.environ',{'STORYROOM_WHISPER_MODEL_DIR':str(model)}),patch.object(faster_whisper,'WhisperModel',FakeModel):
            lu._stage(data,report,'transcript',lambda:lu._transcribe(self.root/'audio.wav',checkpoint))
        stage=json.loads(report.read_text())['stages']['transcript']
        self.assertEqual(stage['status'],'partial')
        self.assertEqual(stage['result']['segments'][0]['text'],'A line.')

    def test_vision_checkpoints_frames_and_never_targets_remote_host(self):
        from urllib.error import URLError
        path=self.make_video();calls=[]
        class FakeOpener:
            def open(self, request, timeout):
                payload=json.loads(request.data);calls.append((request.full_url,payload))
                self_test.assertTrue(request.full_url.startswith('http://127.0.0.1:11434/'))
                if request.full_url.endswith('/api/generate'):return io.BytesIO(b'{}')
                self_test.assertEqual(payload['model'],'qwen3-vl:2b')
                self_test.assertTrue(base64.b64decode(payload['messages'][0]['images'][0]).startswith(b'\xff\xd8'))
                if sum(url.endswith('/api/chat') for url,_ in calls)==2:raise URLError('offline')
                return io.BytesIO(b'{"message":{"content":"A red frame"}}')
        self_test=self
        data={'stages':{}};report=self.root/'report.json';scenes=[{'start_s':0,'end_s':2},{'start_s':2,'end_s':4}]
        def checkpoint(result):
            data['stages']['visual']={'status':'running','result':result};lu._write(report,data)
        with patch.object(lu.urllib.request,'build_opener',return_value=FakeOpener()):
            lu._stage(data,report,'visual',lambda:lu._vision(path,scenes,checkpoint))
        saved=json.loads(report.read_text())['stages']['visual']
        self.assertEqual(saved['status'],'partial')
        self.assertEqual(saved['result']['sampled_frames'],1)
        self.assertEqual(saved['result']['planned_frames'],2)
        self.assertEqual(saved['result']['observations'][0]['frame_time_s'],1.0)
        self.assertTrue(calls[-1][0].endswith('/api/generate'))

    def test_pyannote_runs_in_offline_subprocess(self):
        from types import SimpleNamespace
        model=self.root/'pipeline';model.mkdir()
        wav=self.root/'audio.wav';wav.write_bytes(b'dummy')
        def runner(args,**kwargs):
            Path(args[-1]).write_text(json.dumps({'turns':[]}))
            self.assertEqual(kwargs['env']['HF_HUB_OFFLINE'],'1')
            self.assertEqual(kwargs['env']['PYANNOTE_METRICS_ENABLED'],'0')
            return SimpleNamespace(returncode=0,stderr=b'')
        with patch.dict('os.environ', {'STORYROOM_PYANNOTE_MODEL_DIR':str(model)}), patch.object(lu.subprocess,'run',side_effect=runner):
            self.assertEqual(lu._diarize(wav),{'turns':[]})

    def test_ffmpeg_decodes_mp3(self):
        mp3=self.root/'narration.mp3'; wav=self.root/'decoded.wav'
        subprocess.run([self.ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=1',str(mp3)],check=True)
        lu._decode_audio(mp3,wav)
        self.assertGreater(wav.stat().st_size,16000)

    def test_source_observations_are_mapped_to_the_saved_scene(self):
        report={'status':'partial','stages':{
            'transcript':{'status':'complete','result':{'segments':[{'start_s':9.0,'end_s':11.0,'text':'Before and within'}, {'start_s':14.0,'end_s':15.0,'text':'outside'}]}},
            'visual':{'status':'complete','result':{'observations':[{'frame_time_s':10.5,'description':'visible'}, {'frame_time_s':14.0,'description':'outside'}]}},
            'sound_events':{'status':'complete','result':{'windows':[{'start_s':12.0,'end_s':15.0,'predictions':[{'label':'Music','score':.7}]}]}}}}
        scene={'in_s':10,'frame_in':300,'frame_out':420}
        context=lu.scene_context(report,scene)
        self.assertEqual((context['transcript'][0]['timeline_start_s'],context['transcript'][0]['timeline_end_s']),(10,11))
        self.assertEqual(context['visual'][0]['timeline_time_s'],10.5)
        self.assertEqual((context['sound_events'][0]['timeline_start_s'],context['sound_events'][0]['timeline_end_s']),(12,14))
        self.assertEqual(len(context['visual']),1)
        self.assertEqual(lu.scene_context({'status':'stale'},scene)['transcript'],[])

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

    @unittest.skipUnless(importlib.util.find_spec('faster_whisper'), 'install requirements-local-ai.txt')
    def test_incomplete_model_never_calls_tokenizer_download(self):
        import faster_whisper
        model=self.root/'incomplete';model.mkdir();(model/'model.bin').write_bytes(b'test')
        with patch.dict('os.environ', {'STORYROOM_WHISPER_MODEL_DIR':str(model)}), patch.object(faster_whisper,'WhisperModel') as constructor:
            self.assertFalse(lu.capabilities()['whisper_model_ready'])
            with self.assertRaisesRegex(RuntimeError,'tokenizer.json'):lu._transcribe(self.root/'audio.wav')
            constructor.assert_not_called()

    def test_srt_download_keeps_source_timings_and_does_not_edit_project(self):
        import app as server
        mp3=self.root/'narration.mp3'
        subprocess.run([self.ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=3',str(mp3)],check=True)
        projects=self.root/'projects'; projects.mkdir()
        with patch.object(server, 'PROJECTS', projects), patch.dict('os.environ', {'STORYROOM_UNDERSTANDING_AUTO':'0'}):
            client=server.app.test_client();pid=client.post('/api/projects',json={'title':'SRT test'}).get_json()['id']
            client.post(f'/api/projects/{pid}/upload',data={'kind':'voiceover','file':(io.BytesIO(mp3.read_bytes()),'narration.mp3')},content_type='multipart/form-data')
            root=projects/pid;source=root/'inputs'/'voiceover.mp3'
            report={'status':'partial','source_signature':lu._signature(source),'stages':{'transcript':{'status':'complete','result':{'segments':[{'start_s':.25,'end_s':1.5,'text':'Hello there.'},{'start_s':2,'end_s':2.9,'text':'Second line.'}]}}}}
            lu._write(lu.result_path(root,'narration'),report)
            r=client.get(f'/api/projects/{pid}/understanding/narration/transcript.srt')
            self.assertEqual(r.status_code,200)
            self.assertIn('00:00:00,250 --> 00:00:01,500',r.get_data(as_text=True))
            self.assertIn('transcript.srt',r.headers['Content-Disposition'])
            self.assertIsNone(client.get(f'/api/projects/{pid}').get_json()['srt'])
            report['stages']['transcript']['result']['segments'][1]['start_s']=1
            lu._write(lu.result_path(root,'narration'),report)
            self.assertEqual(client.get(f'/api/projects/{pid}/understanding/narration/transcript.srt').status_code,400)

    def test_no_remote_whisper_model_download(self):
        with patch.dict('os.environ', {'STORYROOM_WHISPER_MODEL_DIR':''}):
            with self.assertRaisesRegex(RuntimeError,'model directory'):
                lu._transcribe(self.root/'audio.wav')

if __name__ == '__main__': unittest.main()
