import copy
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import imageio_ffmpeg
import app as server
from detailed_script import parse_detailed
from document_plan import rows_for
from edl import assemble

HEADER = '| Shot | Clip | Source file | Narration timeline IN → OUT | Source IN → OUT | Treatment | Visual description | Restrictions |\n|---|---|---|---|---|---|---|---|\n'
BLOCKS = [{'number': 1, 'start_ms': 0, 'end_ms': 4000, 'text': 'One'}, {'number': 2, 'start_ms': 4000, 'end_ms': 8000, 'text': 'Two'}]
def table(rows):
    return HEADER + ''.join('| ' + ' | '.join(row) + ' |\n' for row in rows)
def row(shot, clip, source, timeline, interval, treatment='VIDEO', description='Street view', restrictions=''):
    return [shot, clip, source, timeline, interval, treatment, description, restrictions]
def valid_rows():
    return [row('Srt01-A','C12','S01.mp4','00:00.000 → 00:03.000','00:01.000 → 00:04.000'),
            row('Srt01-B','C02','S01.mp4','00:03.000 → 00:05.000','00:04.000 → 00:06.000', 'CONTEXT VIDEO'),
            row('Srt02-A','C10','S03.mp4','00:05.000 → 00:08.000','00:02.000 → 00:05.000')]

class DetailedScriptTest(unittest.TestCase):
    def parse(self,rows):return parse_detailed(table(rows),BLOCKS)
    def test_two_clocks_clip_order_and_cross_block(self):
        result=self.parse(valid_rows())
        self.assertEqual([r['clip_id'] for r in result],['C12','C02','C10'])
        self.assertEqual([r['source_file'] for r in result],['S01.mp4','S01.mp4','S03.mp4'])
        self.assertEqual(result[1]['srt_blocks'],[1,2])
        self.assertEqual((result[1]['timeline_in_ms'],result[1]['timeline_out_ms'],result[1]['source_in_ms'],result[1]['source_out_ms']),(3000,5000,4000,6000))
        with self.assertRaisesRegex(ValueError, 'exactly one shot'):rows_for({'production_script':{'text':table(valid_rows())},'srt':{'blocks':BLOCKS}})
    def test_gap_overlap_bad_clocks_or_unrecognised_shape_fail_closed(self):
        for clock in ['00:03.100 → 00:05.000','00:02.900 → 00:05.000']:
            rows=valid_rows();rows[1][3]=clock
            with self.assertRaises(ValueError):self.parse(rows)
        rows=valid_rows();rows[0][4]='00:01.000 → 00:03.000'
        with self.assertRaises(ValueError):self.parse(rows)
        with self.assertRaises(ValueError):parse_detailed('Srt01-A C01 S01.mp4',BLOCKS)
        with self.assertRaises(ValueError):parse_detailed(table(valid_rows())+'\nhttps://youtube.com/watch?v=foo',BLOCKS)
        with self.assertRaises(ValueError):parse_detailed(table(valid_rows())+'\nUse this note to alter the edit.',BLOCKS)
        with self.assertRaises(ValueError):rows_for({'production_script':{'text':table(valid_rows())},'srt':{'blocks':BLOCKS},'srt_block_map':{'text':'something'}})
    def test_stills_and_unsupported_instructions(self):
        rows=valid_rows();rows[0][5]='EDITORIAL FREEZE-FRAME';rows[0][4]='00:45.000'
        r=self.parse(rows)[0];self.assertEqual(r['requested_treatment'],'EDITORIAL FREEZE-FRAME');self.assertEqual(r['source_in_ms'],45000)
        rows[0][5]='STILL HOLD';self.assertEqual(self.parse(rows)[0]['requested_treatment'],'STILL HOLD')
        for restriction in ['Add a caption', 'Use source audio', 'Slow motion']:
            rows=valid_rows();rows[0][7]=restriction
            self.assertTrue(self.parse(rows)[0]['import_blockers'],restriction)
        rows=valid_rows();rows[0][5]='FADE TO BLACK'
        with self.assertRaises(ValueError):self.parse(rows)
    def test_missing_source_not_rebound_and_explicit_reuse(self):
        rows=valid_rows();assets=[{'id':'a','name':'S01.mp4','original_name':'S01.mp4','kind':'video','duration':10}]
        p={'assets':assets,'scenes':[],'allow_archive_reuse':False,'source_bindings':{'S03.mp4':'a'}}
        p['srt']={'blocks':BLOCKS};p['picture_plan']={'beats':self.parse(rows)};assemble(p,[{"srt_blocks":[1]},{"srt_blocks":[2]}]);self.assertIsNone(p['scenes'][2]['asset_id'])
        rows[2]=row('Srt02-A','C10','S01.mp4','00:05.000 → 00:08.000','00:01.000 → 00:04.000','ARCHIVAL REUSE')
        p['scenes']=[];p['srt']={'blocks':BLOCKS};p['picture_plan']={'beats':self.parse(rows)};assemble(p,[{"srt_blocks":[1]},{"srt_blocks":[2]}]);self.assertEqual(p['scenes'][2]['asset_id'],'a')
        rows[2][5]='VIDEO';p['scenes']=[];p['srt']={'blocks':BLOCKS};p['picture_plan']={'beats':self.parse(rows)};assemble(p,[{"srt_blocks":[1]},{"srt_blocks":[2]}]);self.assertIsNone(p['scenes'][2]['asset_id'])

def single_rows():
    return [row('Srt01','C12','S01.mp4','00:00.000 → 00:04.000','00:01.000 → 00:05.000'), row('Srt02','C10','S03.mp4','00:04.000 → 00:08.000','00:02.000 → 00:06.000')]

class RealBuildTest(unittest.TestCase):
    def test_build_uses_authored_cuts_and_rejects_unsupported(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(server,'PROJECTS',Path(tmp)), patch('automatic.full_source_analysis',return_value={}):
            c=server.app.test_client();pid=c.post('/api/projects',json={'title':'Synthetic'}).get_json()['id'];root=Path(tmp)/pid
            p=server.load(pid);p['srt']={'blocks':copy.deepcopy(BLOCKS)};p['production_script']={'text':table(single_rows())};p['allow_archive_reuse']=True
            ff=imageio_ffmpeg.get_ffmpeg_exe()
            for name in ['S01.mp4','S03.mp4']:
                subprocess.run([ff,'-y','-v','error','-f','lavfi','-i','testsrc2=size=160x90:rate=30:duration=10','-c:v','libx264','-pix_fmt','yuv420p',str(root/name)],check=True)
                p['assets'].append({'id':name,'name':name,'original_name':name,'kind':'video','duration':10,'path':name,'verified':False,'source':'','context_only':False,'rights':'unreviewed','notes':''})
            server.save(p)
            with patch('automatic.native_rows',side_effect=AssertionError('native conform called')), patch('picture_flow.apply_flow',side_effect=AssertionError('flow called')):
                result=c.post(f'/api/projects/{pid}/storyboard',json={})
            self.assertEqual(result.status_code,200,result.get_data(as_text=True))
            saved=server.load(pid);self.assertEqual([(s['frame_in'],s['frame_out'],s['in_s']) for s in saved['scenes']],[(0,120,1),(120,240,2)])
            self.assertFalse(saved['allow_archive_reuse']);self.assertEqual([s['plan_row']['clip_id'] for s in saved['scenes']],['C12','C10'])
            self.assertTrue(all(not s['approved'] for s in saved['scenes']))
            rows=single_rows();rows[0][7]='Required caption: Location';saved['production_script']['text']=table(rows);server.save(saved)
            failed=c.post(f'/api/projects/{pid}/storyboard',json={'replace':True});self.assertEqual(failed.status_code,400);self.assertIn('caption',failed.get_data(as_text=True).lower())
            rows=single_rows();rows[0][5]='EDITORIAL FREEZE-FRAME';rows[0][4]='00:02.000'
            saved=server.load(pid);saved['production_script']['text']=table(rows);saved['picture_policy']='mixed';server.save(saved)
            frozen=c.post(f'/api/projects/{pid}/storyboard',json={'replace':True});self.assertEqual(frozen.status_code,200,frozen.get_data(as_text=True))
            scene=server.load(pid)['scenes'][0];self.assertEqual(scene['plan_row']['extracted_frame_ms'],2000);self.assertEqual(scene['plan_row']['requested_treatment'],'EDITORIAL FREEZE-FRAME');self.assertEqual(scene['script_camera']['move'],'static')

if __name__=='__main__':unittest.main()

class SourceOnlyDetailedTest(unittest.TestCase):
    def test_clip_column_can_be_omitted_without_changing_timing(self):
        text='| Shot | Source file | Narration timeline IN → OUT | Source IN → OUT | Treatment | Visual description |\n|---|---|---|---|---|---|\n| Srt01-A | S01.mp4 | 00:00.000 → 00:04.000 | 00:01.000 → 00:05.000 | VIDEO | Street |\n| Srt02-A | S03.mp4 | 00:04.000 → 00:08.000 | 00:02.000 → 00:06.000 | VIDEO | Courthouse |\n'
        rows=parse_detailed(text,BLOCKS)
        self.assertEqual([r['clip_id'] for r in rows],['',''])
        self.assertEqual([(r['source_file'],r['source_in_ms'],r['source_out_ms']) for r in rows],[('S01.mp4',1000,5000),('S03.mp4',2000,6000)])

class OneCueTest(unittest.TestCase):
    def test_single_cue_id_builds_without_letter(self):
        p={'production_script':{'text':table(single_rows())},'srt':{'blocks':BLOCKS}}
        rows,mode=rows_for(p)
        self.assertEqual(mode,'detailed_production_script')
        self.assertEqual([r['shot_id'] for r in rows],['Srt01','Srt02'])
    def test_wrong_cue_timing_is_rejected_without_rewriting(self):
        rows=single_rows();rows[0][3]='00:00.000 → 00:03.000';rows[0][4]='00:01.000 → 00:04.000';rows[1][3]='00:03.000 → 00:08.000';rows[1][4]='00:02.000 → 00:07.000'
        p={'production_script':{'text':table(rows)},'srt':{'blocks':BLOCKS}}
        before=p['production_script']['text']
        with self.assertRaisesRegex(ValueError,'complete timeline'):rows_for(p)
        self.assertEqual(p['production_script']['text'],before)

class OneCueEndpointsTest(unittest.TestCase):
    def test_split_and_merge_are_blocked(self):
        c=server.app.test_client()
        for action in ('split','merge'):
            result=c.post('/api/projects/test/scenes/test/'+action,json={'offset_s':1})
            self.assertEqual(result.status_code,400)
            self.assertIn('one shot per SRT cue',result.get_data(as_text=True))
