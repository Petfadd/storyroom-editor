import re,unittest
from caption_layout import make_single_line_ass,caption_phrases

class CaptionLayoutTest(unittest.TestCase):
 def test_short_phrases_preserve_words_and_stay_within_cues(self):
  cues=[{'start_ms':0,'end_ms':2500,'text':'First sentence.\nStill the same cue.'},{'start_ms':2500,'end_ms':5000,'text':'Long words '+('and more words '*25)}]
  original=[dict(x) for x in cues];phrases=caption_phrases(cues);ass=make_single_line_ass(cues,1920,1080);events=[x for x in ass.splitlines() if x.startswith('Dialogue:')]
  self.assertEqual(cues,original);self.assertEqual(len(events),len(phrases));self.assertEqual(' '.join(p['text'] for p in phrases if p['cue_index']==0),'First sentence. Still the same cue.');self.assertEqual(' '.join(p['text'] for p in phrases if p['cue_index']==1),' '.join(cues[1]['text'].split()));self.assertTrue(all(len(p['text'])<=36 for p in phrases));self.assertEqual(phrases[0]['start_ms'],0);self.assertEqual([p for p in phrases if p['cue_index']==0][-1]['end_ms'],2500);self.assertEqual(phrases[-1]['end_ms'],5000);self.assertNotIn('\\N',events[0]);self.assertTrue(all('\\q2' in e for e in events));self.assertTrue(all(a['end_ms']==b['start_ms'] for a,b in zip(phrases,phrases[1:])))
 def test_very_short_cues_fail_instead_of_disappearing_silently(self):
  with self.assertRaises(ValueError):make_single_line_ass([{'start_ms':0,'end_ms':1,'text':'test'}],1920,1080)
