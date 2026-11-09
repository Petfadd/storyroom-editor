import copy, subprocess, tempfile, unittest
from pathlib import Path
import imageio_ffmpeg
from strict_cuts import report

class StrictCutTest(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);(self.root/'sources').mkdir()
  self.path=self.root/'sources'/'exact.mp4'
  subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=3','-c:v','libx264','-pix_fmt','yuv420p',str(self.path)],check=True)
  self.row={'strict_production':True,'shot_id':'Srt01','source_file':'exact.mp4','source_in_ms':33,'source_out_ms':1033,'timeline_in_ms':0,'timeline_out_ms':1000,'srt_blocks':[1]}
  self.scene={'id':'s','asset_id':'a','frame_in':0,'frame_out':30,'in_s':.033,'transition':'cut','plan_row':self.row}
  self.p={'production_script':{'name':'script'},'srt':{'blocks':[{'number':1}]},'assets':[{'id':'a','original_name':'exact.mp4','name':'exact.mp4','kind':'video','path':'sources/exact.mp4','duration':3}],'scenes':[self.scene]}
 def tearDown(self):self.tmp.cleanup()
 def test_nearest_source_boundaries_and_no_mutation(self):
  before=copy.deepcopy(self.p);r=report(self.p,self.root);self.assertEqual(r['errors'],[]);x=r['shots'][0];self.assertEqual((x['source_start_frame'],x['source_end_frame']),(1,31));self.assertAlmostEqual(x['in_deviation_ms'],1/3);self.assertAlmostEqual(x['out_deviation_ms'],1/3);self.assertEqual(self.p,before)
 def test_fail_closed_for_bad_inputs(self):
  for mutate,expected in [
   (lambda p:p['scenes'][0].update(asset_id=None),'unassigned'),
   (lambda p:p['assets'][0].update(original_name='similar.mp4'),'filename'),
   (lambda p:p['assets'][0].update(duration=.5),'runtime'),
   (lambda p:p['scenes'][0]['plan_row'].update(source_out_ms=1034),'does not equal'),
   (lambda p:p['scenes'][0].update(frame_in=1),'gap'),
   (lambda p:p['scenes'][0].update(in_s=.2),'differs'),
   (lambda p:p['scenes'][0]['plan_row'].update(source_in_ms=50,source_out_ms=1050),'halfway'),
   (lambda p:p['scenes'][0].update(transition='dissolve'),'clean cut'),
  ]:
   p=copy.deepcopy(self.p);mutate(p);self.assertIn(expected,' '.join(report(p,self.root)['errors']))
 def test_cfr_24fps_source(self):
  path=self.root/'sources'/'24fps.mp4';subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=3','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
  p=copy.deepcopy(self.p);p['assets'][0].update(path='sources/24fps.mp4',original_name='24fps.mp4');p['scenes'][0]['plan_row']['source_file']='24fps.mp4';r=report(p,self.root);self.assertEqual(r['errors'],[]);self.assertEqual(r['shots'][0]['source_fps'],'24');self.assertAlmostEqual(r['shots'][0]['placed_in_ms'],1000/24)

 def test_vfr_timestamps_block_export(self):
  path=self.root/'sources'/'vfr.mp4';subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=30:d=3','-vf',"select='not(eq(n,10))'",'-fps_mode','vfr','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
  p=copy.deepcopy(self.p);p['assets'][0].update(path='sources/vfr.mp4',original_name='vfr.mp4');p['scenes'][0]['plan_row']['source_file']='vfr.mp4';self.assertIn('variable or discontinuous',' '.join(report(p,self.root)['errors']))

class StrictEngineTest(unittest.TestCase):
 def test_frame_indexed_export_does_not_include_out_frame(self):
  from engine import RenderJob
  import cv2
  import numpy as np
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'sources').mkdir();source=root/'sources'/'video.mp4';out=root/'segment.mp4';ff=imageio_ffmpeg.get_ffmpeg_exe()
   subprocess.run([ff,'-v','error','-y','-f','lavfi','-i','testsrc2=s=160x90:r=24:d=3','-c:v','libx264','-pix_fmt','yuv420p',str(source)],check=True)
   job=RenderJob.__new__(RenderJob);job.story={'settings':{'fps':30,'width':160,'height':90}};job.root=root;job.ffmpeg=ff;job.threads=1;job.catalog={};job._dims_cache={};job._duration_cache={}
   job._semantic_upload=lambda slot:None
   shot={'shot':1,'slot':None,'media_type':'video','source_file':'video.mp4','video_in_ms':1000,'video_out_ms':2000,'duration_frames':30,'strict_source_frames':[24,48]}
   cmd=job._picture_segment_command(shot,0,out);subprocess.run(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,check=True)
   cap=cv2.VideoCapture(str(out));self.assertEqual(int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),30);ok,first=cap.read();self.assertTrue(ok);cap.set(cv2.CAP_PROP_POS_FRAMES,29);ok,last=cap.read();self.assertTrue(ok);cap.release()
   cap=cv2.VideoCapture(str(source));cap.set(cv2.CAP_PROP_POS_FRAMES,24);ok,source_in=cap.read();self.assertTrue(ok);cap.set(cv2.CAP_PROP_POS_FRAMES,47);ok,source_last=cap.read();self.assertTrue(ok);cap.set(cv2.CAP_PROP_POS_FRAMES,48);ok,source_out=cap.read();self.assertTrue(ok);cap.release()
   diff=lambda a,b:np.abs(a.astype('float32')-b.astype('float32')).mean()
   self.assertLess(diff(first,source_in),15);self.assertLess(diff(last,source_last),15);self.assertLess(diff(last,source_last),diff(last,source_out))
