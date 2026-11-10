import copy
from unittest.mock import patch
from tests.test_documentary_storyboard import StoryboardPreparationTest
from documentary_storyboard import prepare
from documentary_cue_groups import groups, report
from strict_cuts import report as strict_report


class DocumentaryCueGroupsTest(StoryboardPreparationTest):
    def grouped_project(self):
        self.p['production_script']['text'] = self.p['production_script']['text'].replace('00:00:00,000 --> 00:00:02,000', '00:00:00,100 --> 00:00:02,000').replace('Source OUT: 00:00:02,000', 'Source OUT: 00:00:01,900', 1)
        gap={'narration_in_ms':0,'narration_out_ms':100,'source_in_ms':15000,'source_out_ms':15100,'source_file':'S01.mp4'}
        p, plan = prepare(self.p, self.root, [gap], [], False, lambda _: True)
        p['edit_mode']='documentary'
        return p, plan

    def test_groups_slots_without_mutating_timing_or_approvals(self):
        p, _ = self.grouped_project();before=copy.deepcopy(p);result=groups(p)
        self.assertEqual(len(result),2)
        self.assertEqual([len(g['slots']) for g in result],[2,1])
        self.assertEqual(result[0]['slots'][0]['continuity'],'Documentary beat approved-gap')
        self.assertEqual(p,before)

    def test_reordered_or_unassigned_slots_fail_closed(self):
        p,_=self.grouped_project();p['scenes'].reverse()
        with self.assertRaisesRegex(ValueError,'order'):groups(p)
        p,_=self.grouped_project();p['scenes'][0]['plan_row']['srt_blocks']=[99]
        with self.assertRaisesRegex(ValueError,'matching'):groups(p)

    @patch('documentary_cue_groups.load_decisions',return_value=None)
    @patch('documentary_cue_groups.frame_plan')
    @patch('documentary_cue_groups.load_plan')
    @patch('documentary_cue_groups.annotate_plan')
    def test_strict_check_uses_documentary_groups_and_propagates_frame_errors(self, annotate, load, frame, decisions):
        p,_=self.grouped_project();frame.return_value={'errors':['Real cut mismatch'], 'adjustments':[], 'output_fps':'30'}
        r=strict_report(p,self.root)
        self.assertEqual(r['mode'],'documentary-cue-groups')
        self.assertEqual(len(r['shots']),2);self.assertEqual(r['physical_slots'],3)
        self.assertIn('Real cut mismatch',r['errors'])
