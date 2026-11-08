import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import app as server

class ReuseRightsTest(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.patch=patch.object(server,'PROJECTS',Path(self.temp.name));self.patch.start();self.c=server.app.test_client();self.pid=self.c.post('/api/projects',json={'title':'Rights test'}).get_json()['id'];self.root=Path(self.temp.name)/self.pid
  p=server.load(self.pid);(self.root/'sources/a.mp4').write_bytes(b'original');p['assets']=[{'id':'a','name':'a.mp4','original_name':'a.mp4','path':'sources/a.mp4','kind':'video','duration':20,'source':'https://example.com/source','people':[],'category':'general','verified':True,'rights':'reviewed_other','notes':'Automatic media approval'}];p['scenes']=[{'id':'s','asset_id':'a','frame_in':0,'frame_out':120,'in_s':0,'transition':'cut','focus':'scene','person':'','notes':'','approved':True,'srt_blocks':[1,1],'emotion':'neutral','purpose':'test','category':'general','profile':'documentary','motion':'auto'}];server.save(p)
 def tearDown(self):self.patch.stop();self.temp.cleanup()
 def put(self,data):return self.c.put(f'/api/projects/{self.pid}/assets/a/reuse-rights',json=data)
 def test_approval_requires_attestation_basis_and_specific_details(self):
  for data in [{'decision':'approved'}, {'decision':'approved','basis':'license','details':'contract','attested':False}, {'decision':'approved','basis':'license','details':'','attested':True}]:
   self.assertEqual(self.put(data).status_code,400)
  result=self.put({'decision':'approved','basis':'license','details':'License from publisher for this video; expires in 2028.','attested':True});self.assertEqual(result.status_code,200,result.get_data(as_text=True));p=server.load(self.pid);record=p['assets'][0]['reuse_review'];self.assertEqual(record['decision'],'approved');self.assertEqual(record['basis'],'license');self.assertFalse(record['independent_verification']);self.assertFalse(p['scenes'][0]['approved']);self.assertEqual((self.root/'sources/a.mp4').read_bytes(),b'original')
 def test_unreviewed_auto_approval_is_not_rights_clearance(self):
  p=server.load(self.pid);a=p['assets'][0];errors,_=server.scene_review(p,0,p['scenes'][0],a,{},0,final=True);self.assertTrue(any('reuse-rights approval' in e for e in errors));self.assertEqual(self.put({'decision':'not_approved','details':'Pending rights'}).status_code,200);p=server.load(self.pid);errors,_=server.scene_review(p,0,p['scenes'][0],p['assets'][0],{},0,final=True);self.assertTrue(any('reuse-rights approval' in e for e in errors))
 def test_invalid_identifier_and_revocation(self):
  self.assertEqual(self.c.put(f'/api/projects/{self.pid}/assets/no-such/reuse-rights',json={'decision':'unreviewed'}).status_code,404)
  self.put({'decision':'approved','basis':'permission','details':'Written permission from rights holder','attested':True});self.assertEqual(self.put({'decision':'unreviewed'}).status_code,200);self.assertEqual(server.load(self.pid)['assets'][0]['reuse_review']['decision'],'unreviewed')
