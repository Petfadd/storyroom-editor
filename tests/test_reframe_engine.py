"""The engine hook must be additive: no plan, no change to the existing render."""
import unittest

import engine


class FakeJob:
    """Just enough of RenderJob to call the hook without touching a real project."""

    def __init__(self, story):
        self.story = story

    _smart_reframe_graph = engine.RenderJob._smart_reframe_graph
    _reframe_scene_for = engine.RenderJob._reframe_scene_for

    def _dimensions(self, path):
        return (1920, 1080)


SHOT = {'shot': 1, 'framing': 'smart'}
VISUAL = {'path': '/tmp/fake.mp4', 'in_ms': 0, 'out_ms': 4000}


def plan_payload(mode='smart_crop', locked=False):
    return {'mode': mode, 'width': 1080, 'height': 1920,
            'source': {'width': 1920, 'height': 1080}, 'detector': 'opencv',
            'scenes': [{'scene': 0, 'start': 0.0, 'duration': 4.0, 'locked': locked,
                        'keyframes': [{'t': 0.0, 'x': 100.0, 'w': 608},
                                      {'t': 3.0, 'x': 900.0, 'w': 608}]}]}


class AdditiveTest(unittest.TestCase):
    def test_no_plan_means_no_change(self):
        job = FakeJob({'settings': {}})
        self.assertIsNone(job._smart_reframe_graph(SHOT, VISUAL, 'setpts=PTS-STARTPTS',
                                                   120, 30, 1080, 1920))

    def test_an_empty_scene_list_is_treated_as_no_plan(self):
        job = FakeJob({'settings': {}, 'reframe': {'scenes': [], 'mode': 'smart_crop'}})
        self.assertIsNone(job._smart_reframe_graph(SHOT, VISUAL, 'x', 120, 30, 1080, 1920))

    def test_the_old_framing_modes_are_not_hijacked(self):
        for framing in ('fit', 'crop', 'blur'):
            job = FakeJob({'settings': {}, 'reframe': plan_payload()})
            shot = dict(SHOT, framing=framing)
            self.assertIsNone(job._smart_reframe_graph(shot, VISUAL, 'x', 120, 30, 1080, 1920),
                              f'{framing} was overridden by a stored plan')

    def test_settings_framing_is_used_when_the_shot_has_none(self):
        job = FakeJob({'settings': {'framing': 'smart'}, 'reframe': plan_payload()})
        shot = {'shot': 1}
        self.assertIsNotNone(job._smart_reframe_graph(shot, VISUAL, 'x', 120, 30, 1080, 1920))


class GraphTest(unittest.TestCase):
    def graph_for(self, **over):
        payload = plan_payload(**over.pop('plan', {}))
        job = FakeJob({'settings': {}, 'reframe': payload})
        return job._smart_reframe_graph(SHOT, VISUAL, 'setpts=PTS-STARTPTS', 120, 30,
                                        1080, 1920)

    def test_a_smart_plan_uses_the_overlay_path(self):
        graph = self.graph_for()
        self.assertIn('overlay', graph)
        self.assertIn('eval=frame', graph)
        self.assertIn('[picture]', graph)

    def test_the_existing_prefix_is_kept(self):
        self.assertIn('setpts=PTS-STARTPTS', self.graph_for())

    def test_the_frame_count_is_still_enforced(self):
        self.assertIn('trim=end_frame=120', self.graph_for())

    def test_a_blurred_plan_uses_the_blended_background_not_a_crop(self):
        graph = self.graph_for(plan={'mode': 'blurred'})
        self.assertIn('gblur', graph)
        self.assertNotIn('eval=frame', graph)

    def test_a_locked_scene_holds_one_position(self):
        graph = self.graph_for(plan={'locked': True})
        self.assertIn("x='-(100.0000)'", graph)
        self.assertNotIn('clip(', graph)

    def test_a_moving_scene_produces_a_ramp(self):
        self.assertIn('clip(', self.graph_for())


class SceneMappingTest(unittest.TestCase):
    """A shot must be matched to the plan scene covering its interval of the source."""

    def job(self):
        payload = {'mode': 'smart_crop', 'width': 1080, 'height': 1920,
                   'source': {'width': 1920, 'height': 1080},
                   'scenes': [{'scene': 0, 'start': 0.0, 'duration': 2.0,
                               'keyframes': [{'t': 0.0, 'x': 10.0, 'w': 608}]},
                              {'scene': 1, 'start': 2.0, 'duration': 2.0,
                               'keyframes': [{'t': 0.0, 'x': 900.0, 'w': 608}]}]}
        return FakeJob({'settings': {}, 'reframe': payload})

    def scene_for(self, in_ms, out_ms):
        import reframe_plan
        job = self.job()
        plan = reframe_plan.ReframePlan.from_dict(job.story['reframe'])
        return job._reframe_scene_for(plan, {}, {'in_ms': in_ms, 'out_ms': out_ms})

    def test_the_first_half_maps_to_the_first_scene(self):
        self.assertEqual(self.scene_for(0, 1500).index, 0)

    def test_the_second_half_maps_to_the_second_scene(self):
        self.assertEqual(self.scene_for(2500, 3900).index, 1)

    def test_a_shot_starting_before_the_analysis_uses_the_first_scene(self):
        self.assertEqual(self.scene_for(0, 500).index, 0)

    def test_a_shot_beyond_the_analysis_is_not_force_fitted(self):
        self.assertIsNone(self.scene_for(9000, 9500))


if __name__ == '__main__':
    unittest.main()
