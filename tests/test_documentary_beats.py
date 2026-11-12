import tempfile, unittest
from pathlib import Path
from documentary_beats import read_beats, audit_beats

SCRIPT='''Beat: 001
Type: NARRATION (VOICE OVER)
Narration State: PLAYING
Narration Clock: 00:00:00,000 --> 00:00:02,000
Footage Audio: MUTED (0%)
Video File: S01.mp4
Source IN: 00:00:00,000
Source OUT: 00:00:02,000
On-screen Caption: None
Beat: 002
Type: DOCUMENTARY SOUND BITE
Narration State: PAUSED (Suspended at 00:00:02,000)
Footage Audio: UNMUTED (100% Live Speech)
Video File: S01.mp4
Source IN: 00:00:10,000
Source OUT: 00:00:12,000
Duration: 00:00:02,000
Speaker: Not verified
On-screen Caption: None
Beat: 003
Type: NARRATION (VOICE OVER)
Narration State: RESUMING
Narration Clock: 00:00:02,000 --> 00:00:04,000
Footage Audio: MUTED (0%)
Video File: S01.mp4
Source IN: 00:00:02,000
Source OUT: 00:00:04,000
On-screen Caption: None
'''
class BeatReaderTest(unittest.TestCase):
 def test_reads_and_audits_without_mutating(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'sources').mkdir();(root/'sources'/'video.mp4').write_bytes(b'fixture')
   p={'voiceover':{'duration':4},'assets':[{'id':'a','name':'S01.mp4','kind':'video','path':'sources/video.mp4','duration':20}]}
   b=read_beats(SCRIPT);self.assertEqual([x['kind'] for x in b],['narration','sound_bite','narration']);self.assertEqual(b[1]['pause_ms'],2000);self.assertEqual(audit_beats(b,p,root),[])
   b[1]['pause_ms']=2100;self.assertTrue(any('pause' in e for e in audit_beats(b,p,root)))
   b[1]['source_in_ms']=1000;self.assertTrue(any('overlap' in e for e in audit_beats(b,p,root)))
   # One note per file, not one line per overlapping pair.
   self.assertEqual(sum('overlap' in e for e in audit_beats(b,p,root)),1)
   p['assets'][0]['name']='SO1.mp4';self.assertTrue(any('missing' in e for e in audit_beats(b,p,root)))
 def test_malformed_fails_closed(self):
  for script in [SCRIPT.replace('00:00:12,000\nDuration','00:00:13,000\nDuration'),SCRIPT.replace('Video File: S01.mp4','Video File: ../S01.mp4',1),SCRIPT.replace('MUTED (0%)','UNMUTED (100%)',1),SCRIPT.replace('Beat: 003','Beat: 002')]:
   with self.assertRaises(ValueError):read_beats(script)

# The supplied production scripts label the same facts differently. Accepting
# those labels must not relax any check: every mismatch still fails closed.
ALT='''Beat: 001
Type: VOICE OVER
Master Timeline: 00:00:00,000 --> 00:00:02,000
VO Clock: 00:00:00,000 --> 00:00:02,000
VO State: PLAYING
Full Source: S01.mp4
Source IN: 00:00:00,000
Source OUT: 00:00:02,000
Footage Audio: MUTED (0%)
Cut Action: CUT TO SCENE
On-screen Caption: None
Beat: 002
Type: DOCUMENTARY SOUND BITE
Master Timeline: 00:00:02,000 --> 00:00:04,000 (Duration: 2 sec)
VO Clock: PAUSED at 00:00:02,000
VO State: PAUSED (Suspended for duration of bite)
Full Source: S01.mp4
Source IN: 00:00:10,000
Source OUT: 00:00:12,000
Footage Audio: UNMUTED (100% Dialogue Level, Clean)
On-screen Caption: None
Beat: 003
Type: VOICE OVER
Master Timeline: 00:00:04,000 --> 00:00:06,000
VO Clock: RESUMES at 00:00:02,000 --> 00:00:04,000
VO State: RESUMING
Full Source: S01.mp4
Source IN: 00:00:02,000
Source OUT: 00:00:04,000
Footage Audio: MUTED (0%)
On-screen Caption: None
'''
class AlternateLabelsTest(unittest.TestCase):
 def test_reads_alternate_labels(self):
  b=read_beats(ALT)
  self.assertEqual([x['kind'] for x in b],['narration','sound_bite','narration'])
  self.assertEqual((b[1]['pause_ms'],b[1]['duration_ms']),(2000,2000))
  self.assertEqual((b[2]['narration_in_ms'],b[2]['narration_out_ms']),(2000,4000))
  self.assertEqual([x['source_file'] for x in b],['S01.mp4']*3)
 def test_alternate_labels_fail_closed(self):
  # A wrong Duration, a muted bite or an unreadable label is still rejected. A
  # clock that disagrees with its source range is reported on the beat instead of
  # aborting the whole script; documentary_beat_plan fits it and records the change.
  for script in [ALT.replace('(Duration: 2 sec)','(Duration: 3 sec)'),
                 ALT.replace('UNMUTED (100% Dialogue Level, Clean)','MUTED (0%)')]:
   with self.assertRaises(ValueError):read_beats(script)
  reported=read_beats(ALT.replace('RESUMES at 00:00:02,000 --> 00:00:04,000','RESUMES at 00:00:02,000 --> 00:00:09,000'))
  self.assertEqual(reported[2]['duration_mismatch_ms'],-5000)
  # A pause that disagrees with the preceding narration endpoint is still caught.
  moved=[dict(x) for x in read_beats(ALT)];moved[1]['pause_ms']=2100
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);(root/'sources').mkdir();(root/'sources'/'v.mp4').write_bytes(b'x')
   p={'voiceover':{'duration':4},'assets':[{'id':'a','name':'S01.mp4','kind':'video','path':'sources/v.mp4','duration':20}]}
   self.assertTrue(any('pause' in e for e in audit_beats(moved,p,root)))
 def test_alternate_speaker_label_is_kept_as_a_claim(self):
  script=ALT.replace('Footage Audio: UNMUTED (100% Dialogue Level, Clean)','Footage Audio: UNMUTED (100% Dialogue Level, Clean)\nSpeaker & Role: Witness (unverified)')
  self.assertEqual(read_beats(script)[1]['speaker'],'Witness (unverified)')
 def test_path_style_source_still_rejected(self):
  with self.assertRaises(ValueError):read_beats(ALT.replace('Full Source: S01.mp4','Full Source: ../S01.mp4',1))
 def test_duration_may_be_written_on_the_master_timeline(self):
  # A sound bite that carries its Duration on the Master Timeline line instead of
  # in its own Duration field used to crash with IndexError: the timecode pattern
  # had no capture group, so group(1) did not exist. A script written this way is
  # valid and must read back with the duration it declares.
  script=ALT.replace('Master Timeline: 00:00:02,000 --> 00:00:04,000 (Duration: 2 sec)',
                     'Master Timeline: 00:00:02,000 --> 00:00:04,000 (Duration: 00:00:02,000)')
  beat=read_beats(script)[1]
  self.assertEqual(beat['duration_ms'],2000)
  self.assertEqual(beat['kind'],'sound_bite')
  # The declared duration still has to agree with the source range.
  with self.assertRaises(ValueError):
   read_beats(script.replace('(Duration: 00:00:02,000)','(Duration: 00:00:03,000)'))
