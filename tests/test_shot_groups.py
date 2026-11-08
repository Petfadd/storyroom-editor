import json
import unittest
from automatic import repetition_errors
from edl import assemble, parse

class ShotGroupTest(unittest.TestCase):
    def make(self, grouped=False):
        rows=[]
        for i, (file, start) in enumerate([('a.mp4',0),('b.mp4',0),('a.mp4',20)], 1):
            row={'block':i,'start':f'00:00:{(i-1)*4:02}.000','end':f'00:00:{i*4:02}.000',
                 'beat':i,'media_file':file,'source_in':f'00:00:{start:02}.000',
                 'source_out':f'00:00:{start+4:02}.000','visible_content':'Same description'}
            if grouped and i in (1,3):row['footage_group']='shot A'
            rows.append(row)
        p={'srt':{'blocks':[{'number':i,'start_ms':(i-1)*4000,'end_ms':i*4000,'text':'Line'} for i in range(1,4)]},
           'picture_plan':{'beats':parse(json.dumps(rows).encode(),'.json')},
           'assets':[{'id':name,'name':name,'original_name':name,'kind':'video','duration':100} for name in ('a.mp4','b.mp4')],
           'repetition_policy':{'cooldown_slots':6},'allow_archive_reuse':True}
        assemble(p,[{'srt_blocks':[i,i]} for i in range(1,4)])
        return p

    def test_repeated_description_is_not_evidence_of_a_shot(self):
        p=self.make()
        self.assertFalse(any(s.get('footage_group') for s in p['scenes']))
        self.assertEqual(repetition_errors(p),[])

    def test_explicit_group_catches_non_overlapping_return(self):
        p=self.make(True)
        self.assertIn('same shot', repetition_errors(p)[0])

if __name__=='__main__':unittest.main()

class UnassignedRepetitionTest(unittest.TestCase):
    def test_empty_slots_do_not_count_as_the_same_source(self):
        p={'assets':[],'repetition_policy':{'cooldown_slots':6},'scenes':[{'asset_id':None,'in_s':0,'frame_in':0,'frame_out':120},{'asset_id':None,'in_s':0,'frame_in':120,'frame_out':240}]}
        self.assertEqual(repetition_errors(p),[])
