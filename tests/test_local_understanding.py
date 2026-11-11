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

    @unittest.skipUnless(importlib.util.find_spec('onnxruntime'), 'install requirements-local-ai.txt')
    def test_dynamic_yamnet_waveform_and_output(self):
        import wave
        import numpy as np
        import onnxruntime as ort
        from types import SimpleNamespace
        wav=self.root/'dynamic.wav';model=self.root/'dynamic.onnx';model.write_bytes(b'x')
        with wave.open(str(wav),'wb') as f:
            f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(bytes(2 * 32000))
        seen=[]
        class FakeSession:
            def __init__(self,*args,**kwargs): pass
            def get_inputs(self): return [SimpleNamespace(name='waveform',shape=['dynamic'],type='tensor(float)')]
            def get_outputs(self): return [SimpleNamespace(name='output_0')]
            def run(self,outputs,feed):
                seen.append((outputs,feed['waveform'].shape)); result=np.zeros((3,521),dtype=np.float32);result[:,132]=.8;return [result]
        with patch.dict('os.environ',{'STORYROOM_YAMNET_MODEL':str(model)}),patch.object(ort,'InferenceSession',FakeSession):
            result=lu._sound_events(wav)
        self.assertEqual(seen,[(['output_0'],(32000,))]);self.assertEqual(result['windows'][0]['predictions'][0]['label'],'Music')

    def test_sound_only_reanalysis_preserves_other_stages(self):
        source=self.root/'source.wav';source.write_bytes(b'audio');model=self.root/'model.onnx';model.write_bytes(b'model')
        report={'status':'partial','source_signature':lu._signature(source),'stages':{'transcript':{'status':'complete','result':{'segments':[{'text':'Keep me'}]}},'visual':{'status':'unavailable'}}}
        lu._write(lu.result_path(self.root,'narration'),report)
        with patch.dict('os.environ',{'STORYROOM_YAMNET_MODEL':str(model)}),patch.object(lu,'_decode_audio'),patch.object(lu,'_sound_events',return_value={'windows':[{'start_s':0,'end_s':1}]}):
            lu.enqueue_sound_events(self.root,'narration',source)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                result=lu.read_result(self.root,'narration',source)
                if result['status'] not in ('queued','running'):break
                time.sleep(.01)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['stages']['transcript']['result']['segments'][0]['text'],'Keep me')
        self.assertEqual(result['stages']['sound_events']['status'],'complete')
        source.write_bytes(b'changed')
        with patch.dict('os.environ',{'STORYROOM_YAMNET_MODEL':str(model)}):
            with self.assertRaisesRegex(ValueError,'current'):lu.enqueue_sound_events(self.root,'narration',source)

    def test_visual_only_reanalysis_preserves_results_and_old_frames_on_failure(self):
        source=self.root/'source.mp4';source.write_bytes(b'video')
        report={'status':'partial','source_signature':lu._signature(source),'stages':{
            'transcript':{'status':'complete','result':{'segments':[{'text':'Keep me'}]}},
            'scene_changes':{'status':'complete','result':[{'start_s':0,'end_s':3}]},
            'visual':{'status':'complete','result':{'observations':[{'description':'Old observation'}]}}}}
        lu._write(lu.result_path(self.root,'video'),report)
        with patch.object(lu,'_vision',side_effect=RuntimeError('No model')):
            lu.enqueue_visual(self.root,'video',source)
            deadline=time.monotonic()+5
            while time.monotonic()<deadline:
                result=lu.read_result(self.root,'video',source)
                if result['status'] not in ('queued','running'):break
                time.sleep(.01)
        self.assertEqual(result['status'],'partial')
        self.assertEqual(result['stages']['transcript']['result']['segments'][0]['text'],'Keep me')
        self.assertEqual(result['stages']['visual']['result']['observations'][0]['description'],'Old observation')
        self.assertEqual(result['stages']['visual']['status'],'partial')
        self.assertIn('No model',result['stages']['visual']['detail'])
        source.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError,'current'):lu.enqueue_visual(self.root,'video',source)

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
                url=request if isinstance(request,str) else request.full_url
                payload=json.loads(request.data) if not isinstance(request,str) else None
                calls.append((url,payload))
                self_test.assertTrue(url.startswith('http://127.0.0.1:11434/'))
                if url.endswith('/api/status'):return io.BytesIO(b'{"cloud":{"disabled":true}}')
                if url.endswith('/api/show'):return io.BytesIO(b'{"capabilities":["vision"]}')
                if url.endswith('/api/generate'):return io.BytesIO(b'{}')
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

    def test_vision_refuses_cloud_unverified_or_nonvisual_models_before_sending_frames(self):
        path=self.make_video();scenes=[{'start_s':0,'end_s':2}]
        for status, details in [
            ({'cloud':{'disabled':False}},{}),
            ({'cloud':{'disabled':True}},{'remote_host':'https://example.invalid'}),
            ({'cloud':{'disabled':True}},{'remote_model':'hosted'}),
            ({'cloud':{'disabled':True}},{'capabilities':['completion']}),
            ({},{}),
        ]:
            with self.subTest(status=status,details=details):
                calls=[]
                class FakeOpener:
                    def open(self,request,timeout):
                        url=request if isinstance(request,str) else request.full_url
                        calls.append(url)
                        if url.endswith('/api/status'):return io.BytesIO(json.dumps(status).encode())
                        if url.endswith('/api/show'):return io.BytesIO(json.dumps(details).encode())
                        raise AssertionError('image or unload must not be sent')
                with patch.object(lu.urllib.request,'build_opener',return_value=FakeOpener()):
                    with self.assertRaises(RuntimeError):lu._vision(path,scenes)
                self.assertFalse(any(url.endswith('/api/chat') for url in calls))
        with patch.dict('os.environ',{'STORYROOM_OLLAMA_MODEL':'model:cloud'}),patch.object(lu.urllib.request,'build_opener') as opener:
            with self.assertRaisesRegex(RuntimeError,'Cloud'):lu._vision(path,scenes)
            opener.return_value.open.assert_not_called()

    def test_vision_unloads_after_failed_first_request(self):
        from urllib.error import URLError
        path=self.make_video();calls=[]
        class FakeOpener:
            def open(self,request,timeout):
                url=request if isinstance(request,str) else request.full_url;calls.append(url)
                if url.endswith('/api/status'):return io.BytesIO(b'{"cloud":{"disabled":true}}')
                if url.endswith('/api/show'):return io.BytesIO(b'{"capabilities":["vision"]}')
                if url.endswith('/api/chat'):raise URLError('timeout')
                return io.BytesIO(b'{}')
        with patch.object(lu.urllib.request,'build_opener',return_value=FakeOpener()):
            with self.assertRaises(RuntimeError):lu._vision(path,[{'start_s':0,'end_s':2}])
        self.assertTrue(calls[-1].endswith('/api/generate'))

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

    def test_delayed_video_audio_preserves_source_timeline(self):
        import wave
        import numpy as np
        video=self.root/'delayed.mp4';wav=self.root/'delayed.wav'
        subprocess.run([self.ff,'-v','error','-y','-f','lavfi','-i','color=c=black:s=160x90:r=10:d=5',
                        '-itsoffset','2','-f','lavfi','-i','sine=frequency=440:sample_rate=16000:duration=2',
                        '-map','0:v','-map','1:a','-c:v','mpeg4','-c:a','aac',str(video)],check=True)
        lu._decode_audio(video,wav)
        with wave.open(str(wav),'rb') as f:
            self.assertEqual(f.getframerate(),16000)
            audio=np.frombuffer(f.readframes(f.getnframes()),dtype='<i2').astype(np.float32)
        self.assertGreater(len(audio),int(3.9*16000))
        self.assertLess(np.sqrt(np.mean(audio[:int(1.8*16000)]**2)),10)
        self.assertGreater(np.sqrt(np.mean(audio[int(2.2*16000):int(3.8*16000)]**2)),1000)

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

    def test_timeline_context_maps_each_stage_for_short_and_long_media(self):
        for duration in (60,600,1800,3600):
            with self.subTest(duration=duration):
                start=duration-10
                report={'status':'partial','stages':{
                    'transcript':{'status':'complete','result':{'segments':[{'start_s':start-1,'end_s':start+4,'text':'partial overlap',
                        'words':[{'start_s':start-1,'end_s':start,'text':'outside'},{'start_s':start+1,'end_s':start+2,'text':'inside'},{'start_s':None,'end_s':None,'text':'unknown'}]}]}},
                    'scene_changes':{'status':'complete','result':[{'start_s':start-2,'end_s':start+1},{'start_s':start+1,'end_s':duration}]},
                    'audio_features':{'status':'complete','result':{'windows':[{'start_s':start,'end_s':duration,'rms':.01}],'low_energy_intervals':[{'start_s':start+2,'end_s':start+8}]}},
                    'speakers':{'status':'complete','result':{'turns':[{'start_s':start-2,'end_s':start+5,'speaker':'SPEAKER_00'}]}},
                    'sound_events':{'status':'complete','result':{'windows':[{'start_s':start,'end_s':start+3,'predictions':[]}]}},
                    'visual':{'status':'partial','result':{'observations':[{'frame_time_s':start+1,'description':'visible'},{'frame_time_s':start+5,'description':'outside'}]}}}}
                scene={'in_s':start,'frame_in':900,'frame_out':1050}
                result=lu.scene_context(report,scene)
                self.assertEqual(result['transcript'][0]['timeline_start_s'],30)
                self.assertEqual(result['transcript'][0]['timeline_end_s'],34)
                self.assertEqual(len(result['transcript'][0]['words']),3)
                self.assertEqual([w['text'] for w in result['transcript'][0]['timeline_words']],['inside'])
                self.assertEqual(result['transcript'][0]['timeline_words'][0]['timeline_start_s'],31)
                self.assertEqual((result['speakers'][0]['timeline_start_s'],result['speakers'][0]['timeline_end_s']),(30,35))
                self.assertEqual([(x['timeline_start_s'],x['timeline_end_s']) for x in result['scene_changes']],[(30,31),(31,35)])
                self.assertEqual(result['low_energy_intervals'][0]['timeline_end_s'],35)
                self.assertEqual(len(result['visual']),1)
                self.assertEqual(result['stage_status']['visual'],'partial')
                self.assertNotIn('timeline_start_s',report['stages']['transcript']['result']['segments'][0]['words'][1])

    def test_pre_alignment_reports_warn_without_losing_observations(self):
        source=self.root/'audio.wav';source.write_bytes(b'audio')
        report={'status':'complete','source_signature':lu._signature(source),'stages':{'transcript':{'status':'complete','result':{'segments':[{'start_s':0,'end_s':1,'text':'Hello'}]}}}}
        lu._write(lu.result_path(self.root,'narration'),report)
        result=lu.read_result(self.root,'narration',source)
        self.assertIn('delayed-audio',result['timing_warning'])
        self.assertEqual(result['stages']['transcript']['result']['segments'][0]['text'],'Hello')
        report['audio_timing_version']=2;lu._write(lu.result_path(self.root,'narration'),report)
        self.assertNotIn('timing_warning',lu.read_result(self.root,'narration',source))

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
            # Analysis is queued as heavy work, and the dispatcher deliberately refuses
            # to start heavy work on a machine that reads as strained -- a busy CI
            # runner is exactly that. This test is about the analysis report, not about
            # admission, so the machine is pinned quiet; what it would otherwise assert
            # is that a busy computer leaves the job queued, which is intended.
            import resources
            quiet=resources.Snapshot(4.0,20.0,8192.0,100.0,20.0,'idle','quiet')
            with patch.object(resources,'snapshot',return_value=quiet):
                self.assertIn(client.post(url).get_json()['status'],('queued','running','partial'))
                # Ten seconds was the original bound. A real analysis of one second of
                # audio fits easily on an idle machine, but a loaded runner can take far
                # longer, and a timeout there reports a product failure that did not
                # happen. The bound is generous and the assertion unchanged.
                for _ in range(300):
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

    def test_apply_narration_requires_complete_and_explicit_replacement(self):
        import app as server
        mp3=self.root/'narration.mp3'
        subprocess.run([self.ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=3',str(mp3)],check=True)
        projects=self.root/'projects';projects.mkdir()
        with patch.object(server,'PROJECTS',projects), patch.dict('os.environ',{'STORYROOM_UNDERSTANDING_AUTO':'0'}), patch('automatic.maybe_queue'):
            client=server.app.test_client();pid=client.post('/api/projects',json={'title':'Apply test'}).get_json()['id']
            client.post(f'/api/projects/{pid}/upload',data={'kind':'voiceover','file':(io.BytesIO(mp3.read_bytes()),'narration.mp3')},content_type='multipart/form-data')
            original=b'1\n00:00:00,000 --> 00:00:02,000\nOriginal caption.\n'
            client.post(f'/api/projects/{pid}/upload',data={'kind':'srt','file':(io.BytesIO(original),'original.srt')},content_type='multipart/form-data')
            root=projects/pid;source=root/'inputs/voiceover.mp3';out=root/'inputs/captions.srt'
            report={'status':'partial','source_signature':lu._signature(source),'stages':{'transcript':{'status':'running','result':{'segments':[{'start_s':.25,'end_s':2.9,'text':'Machine text.'}]}}}}
            lu._write(lu.result_path(root,'narration'),report)
            url=f'/api/projects/{pid}/understanding/narration/apply-srt'
            self.assertEqual(client.post(url,json={}).status_code,400)
            self.assertEqual(client.post(url,json={'confirm_generated':True}).status_code,400)
            self.assertEqual(client.post(url,json={'confirm_generated':True,'confirm_replace':True}).status_code,400)
            self.assertEqual(out.read_bytes(),original)
            report['stages']['transcript']['status']='complete';lu._write(lu.result_path(root,'narration'),report)
            response=client.post(url,json={'confirm_generated':True,'confirm_replace':True})
            self.assertEqual(response.status_code,200)
            self.assertIn('00:00:00,250 --> 00:00:02,900',out.read_text())
            self.assertEqual(response.get_json()['srt']['blocks'][0]['text'],'Machine text.')
            source.write_bytes(source.read_bytes()+b'changed')
            self.assertEqual(client.post(url,json={'confirm_generated':True,'confirm_replace':True}).status_code,400)

    def test_no_remote_whisper_model_download(self):
        with patch.dict('os.environ', {'STORYROOM_WHISPER_MODEL_DIR':''}):
            with self.assertRaisesRegex(RuntimeError,'model directory'):
                lu._transcribe(self.root/'audio.wav')

if __name__ == '__main__': unittest.main()
