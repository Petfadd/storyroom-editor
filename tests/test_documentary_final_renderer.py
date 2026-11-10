import copy
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

import av
import imageio_ffmpeg
import numpy as np
from documentary_final_renderer import render
from documentary_storyboard import prepare
from tests.test_documentary_beats import SCRIPT


class DocumentaryAssemblyTest(unittest.TestCase):
    def test_real_frame_indexed_assembly_and_audio_handoffs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('sources', 'inputs', 'renders'):
                (root / folder).mkdir()
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            voice = root / 'inputs/voice.wav'; source = root / 'sources/video.mp4'
            subprocess.run([ff, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=4:sample_rate=48000', '-c:a', 'pcm_s16le', str(voice)], check=True)
            subprocess.run([ff, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=s=160x90:r=30:d=15', '-f', 'lavfi', '-i', 'sine=frequency=880:duration=15:sample_rate=48000', '-c:v', 'libx264', '-threads', '2', '-c:a', 'aac', '-shortest', str(source)], check=True)
            script = SCRIPT.replace('On-screen Caption: None', 'On-screen Caption: Test caption', 1)
            p = {'profile': 'documentary', 'scenes': [], 'production_script': {'text': script},
                 'voiceover': {'path': 'inputs/voice.wav', 'name': 'voice.wav', 'duration': 4},
                 'srt': {'blocks': [{'number': 1, 'start_ms': 0, 'end_ms': 2000, 'text': 'One'}, {'number': 2, 'start_ms': 2000, 'end_ms': 4000, 'text': 'Two'}]},
                 'assets': [{'id': 'a', 'name': 'S01.mp4', 'original_name': 'S01.mp4', 'kind': 'video', 'path': 'sources/video.mp4', 'duration': 15, 'revision': 'v'}]}
            p, plan = prepare(p, root, [], [], False, lambda _: True)
            before = copy.deepcopy((p, plan)); hashes = [hashlib.sha256(x.read_bytes()).digest() for x in (voice, source)]
            out = root / 'renders/out.mp4'; result = render(root, p, plan, out, 160, 90)
            self.assertEqual(result['frames'], 180); self.assertEqual(result['fps'], '30'); self.assertEqual(result['captions'], 1)
            with av.open(str(out)) as media:
                self.assertEqual(media.streams.video[0].frames, 180)
                pcm = np.concatenate([f.to_ndarray() for f in media.decode(audio=0)], axis=1)[0]
            with av.open(str(source)) as media:
                source_frames = {i: frame.to_ndarray(format='rgb24').astype('float32') for i, frame in enumerate(media.decode(video=0)) if i in (0, 60, 300)}
            with av.open(str(out)) as media:
                rendered_frames = {i: frame.to_ndarray(format='rgb24').astype('float32') for i, frame in enumerate(media.decode(video=0)) if i in (0, 60, 120)}
            # Lower half avoids the scripted caption overlay in the first segment.
            for output_index, source_index in ((0, 0), (60, 300), (120, 60)):
                self.assertLess(np.abs(rendered_frames[output_index][45:] - source_frames[source_index][45:]).mean(), 8)
            def peak(t):
                y = pcm[int(t*48000):int((t+.25)*48000)]
                return np.fft.rfftfreq(len(y), 1/48000)[np.abs(np.fft.rfft(y)).argmax()]
            for time, hz in ((.5, 440), (2.5, 880), (4.5, 440)):
                self.assertAlmostEqual(peak(time), hz, delta=6)
            self.assertEqual((p, plan), before)
            self.assertEqual(hashes, [hashlib.sha256(x.read_bytes()).digest() for x in (voice, source)])
            with self.assertRaisesRegex(ValueError, 'already exists'):
                render(root, p, plan, out, 160, 90)

    def test_ntsc_source_keeps_its_native_rate_without_duplicate_frames(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('sources', 'inputs', 'renders'): (root / folder).mkdir()
            ff = imageio_ffmpeg.get_ffmpeg_exe()
            voice = root/'inputs/voice.wav'; source = root/'sources/video.mp4'
            subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=4:sample_rate=48000','-c:a','pcm_s16le',str(voice)],check=True)
            subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30000/1001:d=15','-f','lavfi','-i','sine=frequency=880:duration=15:sample_rate=48000','-c:v','libx264','-threads','2','-c:a','aac','-shortest',str(source)],check=True)
            p={'profile':'documentary','scenes':[],'production_script':{'text':SCRIPT},'voiceover':{'path':'inputs/voice.wav','name':'voice.wav','duration':4},'srt':{'blocks':[{'number':1,'start_ms':0,'end_ms':2000,'text':'One'},{'number':2,'start_ms':2000,'end_ms':4000,'text':'Two'}]},'assets':[{'id':'a','name':'S01.mp4','kind':'video','path':'sources/video.mp4','duration':15,'revision':'v'}]}
            p, sound_plan = prepare(p, root, [], [], False, lambda _:True)
            out = root/'renders/ntsc.mp4'
            result = render(root, p, sound_plan, out, 160, 90)
            self.assertEqual((result['frames'], result['fps']), (180, '30000/1001'))
            with av.open(str(out)) as media:
                frames = list(media.decode(video=0))
                self.assertEqual(len(frames),180)
                timestamps=[f.pts*f.time_base for f in frames]
                self.assertTrue(all(b>a for a,b in zip(timestamps,timestamps[1:])))
