import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as server
import local_understanding as lu


class RemoveFilesTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.patcher=patch.object(server,'PROJECTS',Path(self.tmp.name));self.patcher.start()
        self.client=server.app.test_client();self.pid=self.client.post('/api/projects',json={'title':'Removal test'}).get_json()['id']
        self.root=Path(self.tmp.name)/self.pid
    def tearDown(self):
        self.patcher.stop();self.tmp.cleanup()
    def asset(self):
        aid='a'*16;path=self.root/'sources'/f'{aid}.mp4';path.write_bytes(b'video')
        p=server.load(self.pid);p['assets'].append({'id':aid,'name':'test.mp4','original_name':'test.mp4','path':f'sources/{aid}.mp4','kind':'video','duration':10,'source':'','people':[],'category':'general','verified':False,'rights':'unreviewed','notes':''});server.save(p)
        return aid,path
    def test_stale_automatic_status_is_not_reported_as_working(self):
        server.write(self.root/'automatic_status.json',{'status':'studying','detail':'Working'})
        r=self.client.get(f'/api/projects/{self.pid}');self.assertEqual(r.get_json()['automatic_status']['status'],'interrupted')
        from automatic import _QUEUE_LOCK,_PENDING
        with _QUEUE_LOCK:_PENDING.add(self.pid)
        try:self.assertEqual(self.client.get(f'/api/projects/{self.pid}').get_json()['automatic_status']['status'],'studying')
        finally:
            with _QUEUE_LOCK:_PENDING.discard(self.pid)
    def test_remove_only_confirmed_asset_and_its_report(self):
        aid,path=self.asset();report=lu.result_path(self.root,aid);lu._write(report,{'test':True})
        other=self.root/'sources'/'unrelated.mp4';other.write_bytes(b'keep')
        url=f'/api/projects/{self.pid}/assets/{aid}'
        self.assertEqual(self.client.delete(url,json={}).status_code,400);self.assertTrue(path.exists())
        r=self.client.delete(url,json={'confirm':True});self.assertEqual(r.status_code,200)
        self.assertFalse(path.exists());self.assertFalse(report.exists());self.assertTrue(other.exists());self.assertEqual(r.get_json()['assets'],[])
    def test_used_asset_requires_confirmation_and_preserves_scene(self):
        aid,path=self.asset();p=server.load(self.pid);p['source_bindings']={'v7.mp4':aid};p['scenes']=[{'id':'b'*16,'frame_in':0,'frame_out':60,'asset_id':aid,'approved':True,'emotion':'calm','purpose':'context','focus':'object','in_s':0,'transition':'cut','person':'','notes':'','context_only':False}];server.save(p)
        url=f'/api/projects/{self.pid}/assets/{aid}'
        self.assertEqual(self.client.delete(url,json={'confirm':True}).status_code,400);self.assertTrue(path.exists())
        r=self.client.delete(url,json={'confirm':True,'confirm_used':True});self.assertEqual(r.status_code,200,r.get_json())
        p=server.load(self.pid);self.assertEqual(len(p['scenes']),1);self.assertIsNone(p['scenes'][0]['asset_id']);self.assertFalse(p['scenes'][0]['approved']);self.assertEqual(p['source_bindings'],{})
        self.assertFalse(path.exists());self.assertFalse(r.get_json()['validation']['can_export'])
    def test_removing_video_also_removes_derived_frames(self):
        aid,path=self.asset();p=server.load(self.pid);frame=self.root/'assets'/'frame.png';frame.write_bytes(b'frame')
        p['assets'].append({'id':'frame','name':'frame.png','path':'assets/frame.png','kind':'image','duration':0,'source':'','people':[],'category':'general','verified':False,'rights':'unreviewed','notes':'','derived_from':{'asset_id':aid}});server.save(p)
        url=f'/api/projects/{self.pid}/assets/{aid}'
        self.assertEqual(self.client.delete(url,json={'confirm':True}).status_code,400)
        self.assertEqual(self.client.delete(url,json={'confirm':True,'confirm_used':True}).status_code,200)
        self.assertFalse(path.exists());self.assertFalse(frame.exists());self.assertEqual(server.load(self.pid)['assets'],[])
    def test_remove_script_and_narration(self):
        p=server.load(self.pid);p['production_script']={'name':'script.txt','text':'A script'};p['voiceover']={'name':'voice.mp3','path':'inputs/voiceover.mp3','duration':2};server.save(p)
        script=self.root/'inputs/production_script.txt';script.write_text('A script');voice=self.root/'inputs/voiceover.mp3';voice.write_bytes(b'audio')
        report=lu.result_path(self.root,'narration');lu._write(report,{'test':True})
        for kind in ('production_script','voiceover'):
            r=self.client.delete(f'/api/projects/{self.pid}/inputs/{kind}',json={'confirm':True});self.assertEqual(r.status_code,200,r.get_json())
        self.assertFalse(script.exists());self.assertFalse(voice.exists());self.assertFalse(report.exists())

    def test_busy_analysis_and_auto_build_cannot_be_interrupted(self):
        from automatic import _PENDING,_QUEUE_LOCK
        aid,path=self.asset();key=(str(self.root),aid);url=f'/api/projects/{self.pid}/assets/{aid}'
        with lu._GUARD:lu._RUNNING.add(key)
        try:self.assertEqual(self.client.delete(url,json={'confirm':True}).status_code,400)
        finally:
            with lu._GUARD:lu._RUNNING.discard(key)
        with _QUEUE_LOCK:_PENDING.add(self.pid)
        try:self.assertEqual(self.client.delete(url,json={'confirm':True}).status_code,400)
        finally:
            with _QUEUE_LOCK:_PENDING.discard(self.pid)
        self.assertTrue(path.exists())
    def test_delete_srt_without_losing_existing_scenes(self):
        srt=b'1\n00:00:00,000 --> 00:00:02,000\nHello\n'
        self.client.post(f'/api/projects/{self.pid}/upload',data={'kind':'srt','file':(io.BytesIO(srt),'captions.srt')},content_type='multipart/form-data')
        p=server.load(self.pid);p['scenes']=[{'id':'b'*16,'frame_in':0,'frame_out':60,'asset_id':None,'approved':True,'emotion':'calm','purpose':'context','focus':'object','in_s':0,'transition':'cut','person':'','notes':'','context_only':False}];server.save(p)
        url=f'/api/projects/{self.pid}/inputs/srt'
        self.assertEqual(self.client.delete(url,json={'confirm':True}).status_code,400)
        r=self.client.delete(url,json={'confirm':True,'confirm_used':True});self.assertEqual(r.status_code,200,r.get_json())
        p=r.get_json();self.assertIsNone(p['srt']);self.assertEqual(len(p['scenes']),1);self.assertFalse(p['scenes'][0]['approved']);self.assertFalse((self.root/'inputs/captions.srt').exists())
        self.assertTrue(any('SRT was removed' in x for x in p['validation']['errors']))
        self.client.post(f'/api/projects/{self.pid}/upload',data={'kind':'srt','file':(io.BytesIO(srt),'captions.srt'),'replace':'yes'},content_type='multipart/form-data')
        self.assertNotIn('srt_removed',server.load(self.pid))

if __name__=='__main__':unittest.main()
