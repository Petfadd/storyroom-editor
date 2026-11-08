import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import imageio_ffmpeg
import app as server
from test_section_script import SCRIPT, BLOCKS

class ScriptDraftTest(unittest.TestCase):
    def test_draft_shows_sources_but_cannot_export_or_change_inputs(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)):
            client=server.app.test_client();pid=client.post('/api/projects',json={'title':'Synthetic'}).get_json()['id'];root=Path(tmp)/pid
            p=server.load(pid);p['srt']={'blocks':copy.deepcopy(BLOCKS),'name':'unchanged.srt'};p['production_script']={'name':'untouched.md','text':SCRIPT};p['picture_policy']='clips_only'
            (root/'inputs/captions.srt').write_text('Original names exactly')
            ff=imageio_ffmpeg.get_ffmpeg_exe()
            for name in ['S01.mp4','S02.mp4']:
                path=root/'sources'/name
                subprocess.run([ff,'-y','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30:duration=6','-c:v','libx264','-pix_fmt','yuv420p',str(path)],check=True)
                p['assets'].append({'id':name,'name':name,'original_name':name,'kind':'video','duration':6,'path':'sources/'+name,'verified':False,'source':'','people':[],'category':'general','rights':'unreviewed','notes':''})
            server.save(p)
            response=client.post(f'/api/projects/{pid}/script-draft',json={});self.assertEqual(response.status_code,200,response.get_data(as_text=True))
            saved=server.load(pid);self.assertTrue(saved['review_only_draft']);self.assertEqual(len(saved['scenes']),2)
            self.assertTrue(all(s['asset_id'] for s in saved['scenes']));self.assertEqual(saved['scenes'][0]['plan_row']['extracted_frame_ms'],1000)
            self.assertEqual(saved['scenes'][0]['plan_row']['required_caption'],'“Context label”')
            self.assertEqual(saved['srt']['blocks'],BLOCKS);self.assertEqual(saved['production_script']['text'],SCRIPT);self.assertEqual(saved['picture_policy'],'clips_only')
            self.assertEqual((root/'inputs/captions.srt').read_text(),'Original names exactly')
            self.assertEqual(client.post(f'/api/projects/{pid}/render',json={}).status_code,400)
            self.assertEqual(client.post(f'/api/projects/{pid}/continuous-preview',json={}).status_code,400)
            self.assertEqual(client.post(f'/api/projects/{pid}/scene-exports',json={'source':'preview'}).status_code,400)
            self.assertEqual(client.post(f'/api/projects/{pid}/script-draft',json={}).status_code,400)

if __name__=='__main__':unittest.main()
