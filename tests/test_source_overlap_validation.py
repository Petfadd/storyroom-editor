import unittest
import app

class SourceOverlapValidationTest(unittest.TestCase):
 def make(self, source_in, source_out, fi, fo):
  return {'in_s':source_in/1000,'frame_in':fi,'frame_out':fo,'transition':'cut','plan_row':{'strict_production':True,'source_file':'A.mp4','source_in_ms':source_in,'source_out_ms':source_out,'timeline_in_ms':round(fi*1000/30),'timeline_out_ms':round(fo*1000/30)}}
 def test_exact_ranges_touch_even_if_output_frames_round(self):
  asset={'original_name':'A.mp4'}
  first=self.make(41000,45266,0,128);second=self.make(45266,51366,128,311)
  a=app.source_overlap_interval(first,asset);b=app.source_overlap_interval(second,asset)
  self.assertEqual(a[1],b[0])
 def test_real_overlap_is_not_hidden(self):
  asset={'original_name':'A.mp4'}
  first=self.make(41000,45266,0,128);second=self.make(45000,51100,128,311)
  self.assertGreater(app.source_overlap_interval(first,asset)[1],app.source_overlap_interval(second,asset)[0])
 def test_edited_in_point_uses_actual_duration(self):
  s=self.make(41000,45266,0,128);s['in_s']=41.5
  self.assertAlmostEqual(app.source_overlap_interval(s,{'original_name':'A.mp4'})[1],41.5+128/30)
