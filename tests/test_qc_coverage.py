import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
import frame_qc
import app as server

class QCAndCoverageTest(unittest.TestCase):
 def test_qc_has_five_boundary_samples_and_unavailable_before_start(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'source.mp4').touch()
   p={'assets':[{'id':'a','kind':'video','path':'source.mp4','original_name':'source.mp4','duration':12}],'scenes':[{'id':'s','asset_id':'a','in_s':1,'frame_in':0,'frame_out':150,'srt_blocks':[1],'plan_row':{'strict_production':True,'source_in_ms':1000,'source_out_ms':6000}}]}
   with patch('frame_qc._grab',return_value=Image.new('RGB',(320,180))):
    manifest=frame_qc.build(root,p)
   samples=manifest['beats'][0]['samples'];self.assertEqual([x['position'] for x in samples],['Before IN','IN','Midpoint','Last before OUT','After OUT']);self.assertFalse(samples[0]['decoded']);self.assertEqual(samples[1]['source_s'],1);self.assertEqual(samples[2]['source_s'],3.5);self.assertLess(samples[3]['source_s'],6);self.assertEqual(samples[4]['source_s'],8)
 def test_coverage_keeps_unassigned_separate(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Coverage'}).get_json()['id'];p=server.load(pid)
   # Empty productions are a supported report and retain validation issues.
   data=c.get(f'/api/projects/{pid}/coverage-summary').get_json();self.assertEqual(data['cue_count'],0);self.assertEqual(data['video_seconds'],0);self.assertTrue(data['unresolved_issues'])
 def test_qc_image_route_supports_over_99_shots(self):
  with tempfile.TemporaryDirectory() as tmp,patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Long QC'}).get_json()['id'];root=Path(tmp)/pid;cache=root/'.cache/frame_qc';cache.mkdir(parents=True,exist_ok=True);name='frame_qc_sheet_120_'+'a'*24+'.jpg';Image.new('RGB',(10,10)).save(cache/name)
   self.assertEqual(c.get(f'/api/projects/{pid}/frame-qc/{name}').status_code,200)
