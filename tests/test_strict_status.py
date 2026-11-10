import threading, time, unittest
from unittest.mock import patch
import app as server

class StrictStatusTest(unittest.TestCase):
 def setUp(self):
  server.STRICT_JOBS.clear();self.client=server.app.test_client();self.project={'updated':123.5};self.calls=[];self.release=threading.Event()
 def tearDown(self):
  self.release.set()
 def test_background_dedup_version_and_result(self):
  def check(project,root,progress=None):
   self.calls.append(project['updated']);self.release.wait(2)
   return {'required':True,'checked_version':project['updated'],'shots':[],'errors':['one blocker']}
  with patch.object(server,'load',side_effect=lambda pid:dict(self.project)),patch.object(server,'folder',return_value='/tmp'),patch('strict_cuts.report',side_effect=check):
   first=self.client.get('/api/projects/example/strict-cut-status').get_json();self.assertEqual(first['state'],'checking')
   self.assertEqual(self.client.get('/api/projects/example/strict-cut-status').get_json()['state'],'checking')
   self.release.set()
   for _ in range(100):
    result=self.client.get('/api/projects/example/strict-cut-status').get_json()
    if result['state']=='done':break
    time.sleep(.01)
   self.assertEqual(result['report']['errors'],['one blocker']);self.assertEqual(self.calls,[123.5])
   self.project['updated']=124.5
   result=self.client.get('/api/projects/example/strict-cut-status').get_json();self.assertEqual(result['version'],124.5)
   for _ in range(100):
    result=self.client.get('/api/projects/example/strict-cut-status').get_json()
    if result['state']=='done':break
    time.sleep(.01)
   self.assertEqual(result['report']['checked_version'],124.5);self.assertEqual(self.calls,[123.5,124.5])
 def test_failed_job_can_be_retried(self):
  with patch.object(server,'load',return_value=dict(self.project)),patch.object(server,'folder',return_value='/tmp'),patch('strict_cuts.report',side_effect=RuntimeError('decode failed')):
   self.client.get('/api/projects/example/strict-cut-status')
   for _ in range(100):
    result=self.client.get('/api/projects/example/strict-cut-status').get_json()
    if result['state']=='error':break
    time.sleep(.01)
   self.assertIn('decode failed',result['error']);self.client.post('/api/projects/example/strict-cut-status/retry');self.assertNotIn(('example',123.5),server.STRICT_JOBS)
