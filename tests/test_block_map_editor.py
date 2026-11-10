import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import app as server

SRT = b'1\n00:00:00,000 --> 00:00:04,000\nFirst line\n\n2\n00:00:04,000 --> 00:00:08,000\nSecond line\n'

class BlockMapEditorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.original_projects = server.PROJECTS
        server.PROJECTS = Path(self.tmp.name)
        self.client = server.app.test_client()
        self.pid = self.client.post('/api/projects', json={'title': 'Map test'}).get_json()['id']
        self.upload('srt', SRT, 'narration.srt')

    def tearDown(self):
        server.PROJECTS = self.original_projects
        self.tmp.cleanup()

    def upload(self, kind, payload, filename, **extra):
        return self.client.post(f'/api/projects/{self.pid}/upload', data={
            'kind': kind, 'file': (io.BytesIO(payload), filename), **extra,
        }, content_type='multipart/form-data')

    def editor(self):
        return self.client.get(f'/api/projects/{self.pid}/block-map-editor')

    def test_prefill_save_reopen_and_metadata(self):
        response = self.editor()
        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(len(data['rows']), 2)
        rows = data['rows']
        rows[0].update(media_file='source.mp4', source_in='00:00:01.000',
                       source_out='00:00:05.000', visible_content='A street',
                       footage_group='street', custom_instruction='preserved')
        result = self.upload('srt_block_map', json.dumps(rows).encode(), 'authored.json',
                             expected_srt_signature=data['srt_signature'])
        self.assertEqual(result.status_code, 200, result.get_json())
        saved = self.editor().get_json()['rows']
        self.assertEqual(saved[0]['source_in_ms'], 1000)
        self.assertEqual(saved[0]['footage_group'], 'street')
        self.assertEqual(saved[0]['custom_instruction'], 'preserved')
        self.assertEqual(saved[1]['source_file'], '')
        self.assertTrue(saved[1]['gap_note'])

    @unittest.skipUnless(shutil.which('node'), 'Node is needed to exercise browser serialization')
    def test_edit_imported_row_round_trip(self):
        data = self.editor().get_json()
        rows = data['rows']
        rows[0].update(media_file='old.mp4', source_in='00:00:01.000', source_out='00:00:05.000', required_visual='Old picture', custom_instruction='keep me')
        self.assertEqual(self.upload('srt_block_map', json.dumps(rows).encode(), 'map.json').status_code, 200)
        saved = self.editor().get_json()['rows']
        saved[0].update(_edit_media_file='new.mp4', _edit_source_in='00:00:09.000', _edit_source_out='00:00:13.000', _edit_beat='2', _edit_visible_content='New picture')
        js = (Path(__file__).resolve().parents[1] / 'static/editor.js').read_text()
        helpers = js[js.index('function mapValue('):js.index('function renderMapEditor(')]
        helpers += js[js.index('function mapSaveRows('):js.index('async function saveBlockMap(')]
        code = helpers + "\nprocess.stdout.write(JSON.stringify(mapSaveRows(JSON.parse(process.argv[1]))));"
        payload = subprocess.check_output(['node', '-e', code, json.dumps(saved)])
        response = self.upload('srt_block_map', payload, 'authored.json', expected_srt_signature=data['srt_signature'])
        self.assertEqual(response.status_code, 200, response.get_json())
        row = self.editor().get_json()['rows'][0]
        self.assertEqual((row['source_file'], row['source_in_ms'], row['source_out_ms'], row['parent_beat_id']), ('new.mp4', 9000, 13000, '2'))
        self.assertEqual(row['custom_instruction'], 'keep me')
        self.assertEqual(row['visible_content'], 'New picture')

    def test_invalid_row_does_not_replace_map(self):
        data = self.editor().get_json()
        rows = data['rows']
        rows[0].update(media_file='source.mp4', source_in='00:00:05.000', source_out='00:00:01.000')
        result = self.upload('srt_block_map', json.dumps(rows).encode(), 'authored.json',
                             expected_srt_signature=data['srt_signature'])
        self.assertEqual(result.status_code, 400)
        self.assertIn('OUT', result.get_json()['error'])
        self.assertFalse(self.editor().get_json()['existing'])

    def test_changed_srt_rejects_draft_and_stale_map(self):
        data = self.editor().get_json()
        payload = json.dumps(data['rows']).encode()
        self.assertEqual(self.upload('srt_block_map', payload, 'authored.json', expected_srt_signature=data['srt_signature']).status_code, 200)
        self.assertEqual(self.upload('srt', SRT.replace(b'First line', b'New wording'), 'new.srt').status_code, 200)
        response = self.upload('srt_block_map', payload, 'authored.json', expected_srt_signature=data['srt_signature'])
        self.assertEqual(response.status_code, 400)
        self.assertIn('SRT changed', response.get_json()['error'])
        self.assertEqual(self.editor().status_code, 400)

if __name__ == '__main__':
    unittest.main()
