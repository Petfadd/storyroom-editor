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

class PhotoRequirementsTest(unittest.TestCase):
 def test_photo_scan_without_srt_with_spaced_names_and_format_matching(self):
  from footage_requirements import photo_requirements
  p={'production_script':{'text':'Photograph: Victim Photo.jpg\nFull source: PROPOSED: Exhibit A.png\nFull source: S03.mp4\nOther: `Suspect Photo.jpeg`\nDuplicate: `Suspect Photo.jpeg`'},'assets':[{'kind':'image','original_name':'Victim Photo.jpeg'},{'kind':'image','original_name':'Exhibit A.jpg'},{'kind':'image','original_name':'Exhibit A.png'}]}
  r=photo_requirements(p);self.assertEqual([x['name'] for x in r['files']],['Victim Photo.jpg','Exhibit A.png','Suspect Photo.jpeg']);self.assertEqual((r['total'],r['matched'],r['missing'],r['ambiguous']),(3,1,1,1));self.assertEqual(r['files'][0]['matched_names'],['Victim Photo.jpeg'])
 def test_no_script_and_no_names(self):
  from footage_requirements import photo_requirements
  self.assertEqual(photo_requirements({})['status'],'no_script');self.assertEqual(photo_requirements({'production_script':{'text':'S01.mp4'}})['status'],'not_found')
 def test_photo_list_updates_on_script_upload_without_srt(self):
  with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
   c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Photo count'}).get_json()['id']
   res=c.post(f'/api/projects/{pid}/upload',data={'kind':'production_script','file':(io.BytesIO(b'Photograph: Victim Portrait.jpg\nFull source: S01.mp4'),'script.txt')},content_type='multipart/form-data');self.assertEqual(res.status_code,200);r=res.get_json();self.assertEqual(r['photo_requirements']['files'][0]['name'],'Victim Portrait.jpg');self.assertEqual(r['photo_requirements']['missing'],1);self.assertIsNone(r['srt'])
