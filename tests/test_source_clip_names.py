import unittest
from pathlib import Path
from document_plan import script_beats
from production import instructions, matching_assets

class SourceClipNamesTest(unittest.TestCase):
    def test_template_uses_s_sources_and_maps_to_actual_names(self):
        p={'production_script':{'text':Path('docs/PRODUCTION_SCRIPT_TEMPLATE.md').read_text()},'srt':{'blocks':[{'number':1,'start_ms':0,'end_ms':5000,'text':'First'}, {'number':2,'start_ms':5000,'end_ms':10000,'text':'Second'}]}}
        rows=script_beats(p)
        self.assertEqual([r['source_file'] for r in rows],['S01.mp4','S02.mp4'])
        p['assets']=[{'id':'a','name':'S01.mp4','original_name':'S01.mp4'},{'id':'b','name':'v1.mp4','original_name':'v1.mp4'}]
        self.assertEqual([x['id'] for x in matching_assets(p,'S01.mp4')],['a'])
        self.assertEqual([x['id'] for x in matching_assets(p,'v1.mp4')],['b'])
        self.assertEqual(matching_assets(p,'S1.mp4'),[])
        self.assertEqual(matching_assets({'assets':[p['assets'][1]]},'S01.mp4'),[])
    def test_legacy_table_and_prose_recognise_s_and_preserve_v(self):
        header='| Beat | SRT blocks | Timeline IN → OUT | Media | Source IN/OUT (planned) |\n|---|---|---|---|---|\n'
        rows='| 1 | 1 | 00:00:00.000 → 00:00:05.000 | S01.mp4 | 00:00:10.000 → 00:00:15.000 |\n| 2 | 2 | 00:00:05.000 → 00:00:10.000 | v2.mp4 | 00:00:20.000 → 00:00:25.000 |\n'
        p={'production_script':{'text':header+rows},'srt':{'blocks':[{'number':1,'start_ms':0,'end_ms':5000},{'number':2,'start_ms':5000,'end_ms':10000}]}}
        self.assertEqual([r['source_file'] for r in script_beats(p)],['S01.mp4','v2.mp4'])
        p['production_script']['text']='SRT 1 S01.mp4 in: 2 out: 7\nSRT 2 v2.mp4 in: 10 out: 15'
        self.assertEqual([r['asset'] for r in instructions(p)[0]],['S01.mp4','v2.mp4'])

if __name__=='__main__':unittest.main()
