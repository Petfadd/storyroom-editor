import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as server
import automatic
import local_understanding
import preview_render
import scene_exports


class DeleteProjectTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.project_dir = self.base / 'projects'; self.project_dir.mkdir()
        self.patcher = patch.object(server, 'PROJECTS', self.project_dir); self.patcher.start()
        self.client = server.app.test_client()
        self.pid = self.client.post('/api/projects', json={'title': 'Delete me'}).get_json()['id']
        self.other = self.client.post('/api/projects', json={'title': 'Keep me'}).get_json()['id']
        self.root = self.project_dir / self.pid
        for name in ('sources/upload.mp4', 'assets/image.png', 'inputs/voiceover.wav', 'inputs/production_script.txt', 'renders/final.mp4', 'reviews/report.json', '.cache/preview/work.tmp'):
            path = self.root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(b'project content')
        (self.project_dir / self.other / 'sources' / 'other.mp4').write_bytes(b'keep')
        self.external = self.base / 'outside'; self.external.mkdir(); (self.external / 'original.mp4').write_bytes(b'external original')
        (self.root / 'external-link').symlink_to(self.external, target_is_directory=True)
    def tearDown(self):
        server.JOBS.pop(self.pid, None);preview_render.JOBS.pop(self.pid, None);scene_exports.JOBS.pop(self.pid, None)
        with automatic._QUEUE_LOCK:automatic._PENDING.discard(self.pid)
        with local_understanding._GUARD:local_understanding._RUNNING.discard((str(self.root),'asset'))
        self.patcher.stop();self.temp.cleanup()
    def delete(self, data=None):return self.client.delete(f'/api/projects/{self.pid}', json=data)
    def test_confirm_then_delete_only_this_project_and_files(self):
        self.assertEqual(self.delete({}).status_code,400);self.assertTrue(self.root.exists())
        self.assertEqual(self.delete({'confirm':'true'}).status_code,400);self.assertTrue(self.root.exists())
        response=self.delete({'confirm':True});self.assertEqual(response.status_code,200);self.assertTrue(response.get_json()['deleted'])
        self.assertFalse(self.root.exists());self.assertTrue((self.external/'original.mp4').exists())
        self.assertTrue((self.project_dir/self.other/'sources/other.mp4').exists())
        self.assertEqual(self.client.get(f'/api/projects/{self.pid}').status_code,404)
        self.assertEqual([p['id'] for p in self.client.get('/api/projects').get_json()['projects']],[self.other])
    def test_background_work_blocks_deletion(self):
        class Job:
            def snapshot(self):return {'status':'running'}
        server.JOBS[self.pid]=Job();self.assertEqual(self.delete({'confirm':True}).status_code,400);server.JOBS.pop(self.pid)
        with automatic._QUEUE_LOCK:automatic._PENDING.add(self.pid)
        self.assertEqual(self.delete({'confirm':True}).status_code,400)
        with automatic._QUEUE_LOCK:automatic._PENDING.discard(self.pid)
        for module in (preview_render,scene_exports):
            with module.GUARD:module.JOBS[self.pid]={'status':'running'}
            self.assertEqual(self.delete({'confirm':True}).status_code,400)
            with module.GUARD:module.JOBS.pop(self.pid)
        with local_understanding._GUARD:local_understanding._RUNNING.add((str(self.root),'asset'))
        self.assertEqual(self.delete({'confirm':True}).status_code,400)
        self.assertTrue(self.root.exists())
    def test_invalid_id_and_failure_are_not_reported_as_success(self):
        self.assertEqual(self.client.delete('/api/projects/not-an-id',json={'confirm':True}).status_code,404)
        with patch('app.shutil.rmtree',side_effect=OSError('locked')):
            result=self.delete({'confirm':True})
        self.assertEqual(result.status_code,400);self.assertIn('some project files may remain',result.get_json()['error']);self.assertTrue(self.root.exists())

if __name__ == '__main__':unittest.main()
