import tempfile,unittest
from pathlib import Path
from documentary_script_diagnostics import diagnose
from automatic import automatic_readiness

class DocumentaryDiagnosticsTest(unittest.TestCase):
 def test_beat_script_is_reported_not_guessed(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'S01.mp4').write_bytes(b'video')
   p={'edit_mode':'documentary','production_script':{'text':'''Beat: 001
Video File: SO1.mp4
Narration Clock: 00:00:00,100 --> 00:00:01,000
Beat: 002
Type: DOCUMENTARY SOUND BITE
Video File: SO1.mp4
Beat: 003
Video File: SO1.mp4
Narration Clock: 00:00:01,200 --> 00:00:02,000
'''},'voiceover':{'duration':2.5},'assets':[{'name':'S01.mp4','original_name':'S01.mp4','path':'S01.mp4'}]}
   result=diagnose(p,root);text=' '.join(result['build_issues']);self.assertFalse(result['ready_to_build']);self.assertIn('reviewed explicitly',text);self.assertIn('SO1.mp4',text)
   # The old per-beat caption warning was false: captions are burned in now.
   self.assertNotIn('requires a supported documentary renderer',text);self.assertIn('0.000–0.100s',text);self.assertIn('1.000s to 1.200s',text);self.assertIn('2.500s',text)
   # A few milliseconds between an SRT clock and the encoded audio is rounding,
   # not narration the script failed to account for.
   p['voiceover']['duration']=2.05;self.assertNotIn('Last scripted narration ends', ' '.join(diagnose(p,root)['build_issues']))
 def _beat(self,label,start,end,kind,narration=None):
  tc=lambda ms:f'{ms//3600000:02d}:{(ms//60000)%60:02d}:{(ms//1000)%60:02d},{ms%1000:03d}'
  rows=[f'Beat: {label}',f'Type: {kind}']
  if kind=='VOICE OVER':
   rows+=[f'VO Clock: PLAYING {tc(narration)} --> {tc(narration+(end-start))}',
          'VO State: PLAYING','Footage Audio: MUTED (0%)']
  else:
   rows+=[f'Master Timeline: {tc(start)} --> {tc(end)} (Duration: {(end-start)//1000} sec)',
          f'VO Clock: PAUSED at {tc(narration)}','Footage Audio: UNMUTED (100% Dialogue Level, Clean)',
          'Speaker & Role: Someone']
  rows+=[f'Full Source: S01.mp4',f'Source IN: {tc(start)}',f'Source OUT: {tc(end)}','']
  return '\n'.join(rows)

 def test_a_short_source_names_the_clamp_the_plan_will_apply(self):
  # A real case: a Beat script written against a 235 s cut, uploaded against a
  # 173.8 s file. The panel used to demand a re-upload while the Beat plan
  # clamped and extended the range by itself, so the button under the warning
  # already resolved it. Say what the plan will do, with the real numbers.
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'sources').mkdir();(root/'sources'/'S01.mp4').write_bytes(b'video')
   text=(self._beat('001',0,15000,'VOICE OVER',0)+self._beat('002',200000,215000,'VOICE OVER',15000)
         +self._beat('003',220000,235000,'VOICE OVER',30000))
   p={'edit_mode':'documentary','production_script':{'text':text},'voiceover':{'duration':45},
      'assets':[{'id':'a1','name':'S01.mp4','original_name':'S01.mp4','path':'sources/S01.mp4','kind':'video','duration':173.8}]}
   result=diagnose(p,root)
   notes=' '.join(x['text'] for x in result['build_notes'])
   self.assertIn('173.800s',notes)
   self.assertIn('clamped to 173.800s',notes)
   # The old wording asked for a file that is not needed and no longer appears.
   self.assertNotIn('run past the end of the uploaded file',' '.join(result['build_issues']))
   self.assertNotIn('Beat 002: source OUT exceeds',' '.join(result['build_issues']))
   self.assertNotIn('Beat 003: source OUT exceeds',' '.join(result['build_issues']))
   # A range that runs past the file is a change, not a fault: reuse of the same
   # frames is the only thing the plan refuses.
   self.assertFalse(any('run past the end' in x for x in result['build_issues']))

 def test_a_repeated_range_blocks_only_when_the_frames_are_identical(self):
  # Reuse under new narration is allowed; replaying exactly the same frames is
  # not. The panel must not call the first a fault.
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'sources').mkdir();(root/'sources'/'S01.mp4').write_bytes(b'video')
   text=(self._beat('001',0,15000,'VOICE OVER',0)+self._beat('002',5000,20000,'VOICE OVER',15000))
   p={'edit_mode':'documentary','production_script':{'text':text},'voiceover':{'duration':30},
      'assets':[{'id':'a1','name':'S01.mp4','original_name':'S01.mp4','path':'sources/S01.mp4','kind':'video','duration':173.8}]}
   result=diagnose(p,root)
   self.assertTrue(result['build_notes'])
   self.assertFalse(any('would show exactly the same frames' in x for x in result['build_issues']))
   self.assertFalse(result['ready_to_build'], 'a Beat plan is reviewed, never built automatically')

 def test_narration_mismatch_states_the_actual_difference(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'sources').mkdir();(root/'sources'/'S01.mp4').write_bytes(b'video')
   text=self._beat('001',0,93000,'VOICE OVER',0)
   p={'edit_mode':'documentary','production_script':{'text':text},'voiceover':{'duration':100.05},
      'assets':[{'id':'a1','name':'S01.mp4','original_name':'S01.mp4','path':'sources/S01.mp4','kind':'video','duration':173.8}]}
   joined=' '.join(diagnose(p,root)['build_issues'])
   self.assertIn('100.050s',joined)
   self.assertIn('+7.050s',joined)

 def test_a_pause_between_narration_beats_is_reported_as_a_pause(self):
  """Those few hundred milliseconds are where the sound bites go.

  Measured on a real script: 14 pauses of 100-700ms between narration beats, one at
  every one of its 15 bites. They were reported as "Narration gap ... No narration was
  trimmed", which reads as a fault in the script and sent the reader looking for one.
  """
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);(root/'sources').mkdir();(root/'sources'/'S01.mp4').write_bytes(b'video')
   first=self._beat('001',0,4000,'VOICE OVER',0)
   # narration 0-4000ms, then 400ms of silence where a bite sits, then 4400-8400ms
   second=self._beat('002',4000,8400,'VOICE OVER',4400)
   p={'edit_mode':'documentary','production_script':{'text':first+second},
      'voiceover':{'duration':8.8},
      'assets':[{'id':'a1','name':'S01.mp4','original_name':'S01.mp4','path':'sources/S01.mp4','kind':'video','duration':173.8}]}
   joined=' '.join(diagnose(p,root)['build_issues'])
   self.assertIn('Narration pause between Beat 001 and Beat 002',joined)
   self.assertIn('400 ms',joined)
   self.assertIn('covers this pause',joined)
   self.assertNotIn('Narration gap',joined)

 def test_documentary_readiness_does_not_claim_automatic_assembly(self):
  p={'edit_mode':'documentary','srt':{'blocks':[]},'voiceover':{'name':'n'},'assets':[{}],'production_script':{'text':'example'},'scenes':[],'auto_assemble_uploads':True}
  r=automatic_readiness(p);self.assertEqual(r['status'],'paused');self.assertIn('not implemented',r['detail'])


class UploadPreflightTest(unittest.TestCase):
    """The mismatch must reach the browser on upload, not only in a later review."""

    SCRIPT = """TOTAL DURATION: ~04:45,000
SOURCE FILE: S01.mp4

Beat: 001
Type: NARRATION (VOICE OVER)
Video File: S01.mp4
Source IN: 00:00:00,000
Source OUT: 00:00:10,000
VO Clock: PLAYING 00:00:00,000 --> 00:00:10,000
VO State: PLAYING
Footage Audio: MUTED
Visual Direction: opening

Beat: 002
Type: NARRATION (VOICE OVER)
Video File: S01.mp4
Source IN: 00:00:10,000
Source OUT: 00:00:20,000
VO Clock: RESUMES 00:00:10,000 --> 00:00:20,000
VO State: PLAYING
Footage Audio: MUTED
Visual Direction: closing
"""

    def test_the_upload_response_carries_the_source_mismatch(self):
        import io, json, shutil, tempfile
        from unittest.mock import patch
        import app as server
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'PROJECTS', Path(tmp)):
            client = server.app.test_client()
            pid = client.post('/api/projects', json={'title': 'Preflight', 'edit_mode': 'documentary'}).get_json()['id']
            root = Path(tmp)/pid
            (root/'sources'/'a.mp4').write_bytes(b'video')
            project = json.loads((root/'project.json').read_text())
            project['assets'] = [{'id': 'a1', 'kind': 'video', 'original_name': 'S01.mp4', 'name': 'S01.mp4',
                                  'path': 'sources/a.mp4', 'duration': 12, 'has_audio': True}]
            (root/'project.json').write_text(json.dumps(project))
            response = client.post(f'/api/projects/{pid}/upload',
                                   data={'kind': 'production_script', 'file': (io.BytesIO(self.SCRIPT.encode()), 'plan.md')},
                                   content_type='multipart/form-data')
            self.assertEqual(response.status_code, 200)
            body = response.get_json()
            self.assertIn('script_preflight', body, 'the upload must report the mismatch itself')
            text = ' '.join(body['script_preflight'])
            # The plan clamps and extends this range instead of demanding a
            # re-upload, so the warning says that, with the real numbers.
            self.assertIn('is past the end of S01.mp4 (12.000s)', text)
            self.assertIn('clamped to 12.000s', text)

    def test_a_matching_project_gets_no_preflight_warning(self):
        import io, json, tempfile
        from unittest.mock import patch
        import app as server
        with tempfile.TemporaryDirectory() as tmp, patch.object(server, 'PROJECTS', Path(tmp)):
            client = server.app.test_client()
            pid = client.post('/api/projects', json={'title': 'Clean', 'edit_mode': 'documentary'}).get_json()['id']
            root = Path(tmp)/pid
            (root/'sources'/'a.mp4').write_bytes(b'video')
            project = json.loads((root/'project.json').read_text())
            project['assets'] = [{'id': 'a1', 'kind': 'video', 'original_name': 'S01.mp4', 'name': 'S01.mp4',
                                  'path': 'sources/a.mp4', 'duration': 20, 'has_audio': True}]
            (root/'project.json').write_text(json.dumps(project))
            response = client.post(f'/api/projects/{pid}/upload',
                                   data={'kind': 'production_script', 'file': (io.BytesIO(self.SCRIPT.encode()), 'plan.md')},
                                   content_type='multipart/form-data')
            self.assertEqual(response.status_code, 200)
            self.assertNotIn('script_preflight', response.get_json())
