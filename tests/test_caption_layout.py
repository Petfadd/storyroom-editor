import unittest
from caption_layout import make_single_line_ass

class CaptionLayoutTest(unittest.TestCase):
 def test_cues_stay_separate_and_fit_one_line(self):
  cues=[{'start_ms':0,'end_ms':2500,'text':'First sentence.\nStill the same cue.'},{'start_ms':2500,'end_ms':5000,'text':'Long words '+('and more words '*25)}]
  original=[dict(x) for x in cues];ass=make_single_line_ass(cues,1920,1080);events=[x for x in ass.splitlines() if x.startswith('Dialogue:')]
  self.assertEqual(cues,original);self.assertEqual(len(events),2);self.assertIn('First sentence. Still the same cue.',events[0]);self.assertIn('0:00:00.00,0:00:02.50',events[0]);self.assertIn('0:00:02.50,0:00:05.00',events[1]);self.assertNotIn('\\N',events[0]);self.assertIn('\\q2',events[0]);self.assertNotIn('\\fs56',events[1])
 def test_very_short_cues_fail_instead_of_disappearing_silently(self):
  with self.assertRaises(ValueError):make_single_line_ass([{'start_ms':0,'end_ms':1,'text':'test'}],1920,1080)
