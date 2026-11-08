import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import app as server
from footage_requirements import footage_requirements

class FootageRequirementsTest(unittest.TestCase):
 def test_quick_scan_without_srt_deduplicates_names_not_clip_ids(self):
  p={'production_script':{'text':'C01 C02 S01.mp4 S02.mp4 s01.MP4 S10.mov `link 7.mp4`'},'assets':[
   {'name':'S01.mp4','original_name':'S01.mp4','kind':'video'},
   {'name':'S02.mp4','original_name':'S02.mp4','kind':'video'},
   {'name':'S02.mp4','original_name':'S02.mp4','kind':'video'},
   {'name':'S10.mov','original_name':'S10.mov','kind':'image'},
   {'name':'link 7.mp4','original_name':'link 7.mp4','kind':'video','derived_from':{'kind':'video'}}]}
  r=footage_requirements(p);self.assertEqual((r['total'],r['matched'],r['missing'],r['ambiguous']),(4,1,2,1));self.assertEqual([x['name'] for x in r['files']],['link 7.mp4','S01.mp4','S02.mp4','S10.mov'])
 def test_exact_name_and_unsupported_or_no_script(self):
  self.assertEqual(footage_requirements({})['status'],'no_script')
  self.assertEqual(footage_requirements({'production_script':{'text':'C01, C02; no filenames'}})['status'],'not_found')
  r=footage_requirements({'production_script':{'text':'S01.mp4 S1.mp4'},'assets':[{'kind':'video','original_name':'S1.mp4'}]})
  self.assertEqual((r['total'],r['matched'],r['missing']),(2,1,1))
 def test_upload_and_replace_script_without_srt_updates_public_requirements(self):
  with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Count test'}).get_json()['id']
   def upload(text):
    return c.post(f'/api/projects/{pid}/upload',data={'kind':'production_script','file':(io.BytesIO(text.encode()),'plan.md')},content_type='multipart/form-data')
   result=upload('S01.mp4 S02.mp4 S01.mp4 C01');self.assertEqual(result.status_code,200)
   r=result.get_json()['footage_requirements'];self.assertEqual((r['total'],r['missing']),(2,2));self.assertIsNone(result.get_json()['srt'])
   result=upload('S03.mp4');self.assertEqual(result.get_json()['footage_requirements']['files'][0]['name'],'S03.mp4')
   result=c.delete(f'/api/projects/{pid}/inputs/production_script',json={'confirm':True})
   self.assertEqual(result.status_code,200);self.assertEqual(result.get_json()['footage_requirements']['status'],'no_script')

if __name__=='__main__':unittest.main()
