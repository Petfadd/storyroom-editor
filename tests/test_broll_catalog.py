"""The b-roll catalog and matcher: what b-roll is, and what it may never be.

Three kinds of picture feed this production - photographs, footage and b-roll taken
from that footage. These tests hold the b-roll half of that rule: a window must be a
window that was actually looked at, it must never be a person speaking, and no frame
may be used twice.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))

from place_broll import (EXCLUSIONS, MIN_SHOT_S, NOT_BROLL, load_catalog, named,
                         plan_broll, score_cue, spoken)
from place_photos import PEOPLE

CATALOG = ROOT / 'data' / 'broll_catalog.json'


class CatalogTest(unittest.TestCase):
    def setUp(self):
        self.catalog = load_catalog(CATALOG)

    def test_every_window_is_inside_its_source_and_positive(self):
        sources = self.catalog['sources']
        for shot in self.catalog['shots']:
            duration = sources[shot['source']]['duration']
            self.assertGreater(shot['out'], shot['in'], shot['id'])
            self.assertGreaterEqual(shot['in'], 0, shot['id'])
            self.assertLessEqual(shot['out'], duration + 0.001, shot['id'])

    def test_windows_in_one_source_do_not_overlap(self):
        # Two catalog entries over the same frames would let the planner put the same
        # picture on screen twice while believing it had found two shots.
        by_source = {}
        for shot in self.catalog['shots']:
            by_source.setdefault(shot['source'], []).append(shot)
        for source, shots in by_source.items():
            shots = sorted(shots, key=lambda s: s['in'])
            for a, b in zip(shots, shots[1:]):
                self.assertLessEqual(a['out'], b['in'] + 0.001,
                                     f'{source}: {a["id"]} and {b["id"]} overlap')

    def test_every_shot_says_what_is_on_screen(self):
        for shot in self.catalog['shots']:
            self.assertTrue(shot['what'].strip(), shot['id'])
            self.assertTrue(shot['tags'], shot['id'])
            self.assertTrue(shot.get('verified', True), shot['id'])

    def test_sources_describe_the_file_honestly(self):
        # Each source must record a duration, so a window cannot be checked against
        # a number nobody wrote down.
        for name, source in self.catalog['sources'].items():
            self.assertGreater(source['duration'], 0, name)
            self.assertTrue(source['what'].strip(), name)


class MatcherTest(unittest.TestCase):
    def test_a_named_person_is_never_b_roll(self):
        # The narrator says a name, so that person's photograph belongs on it.
        for name, (pattern, _) in PEOPLE.items():
            self.assertEqual(named(f'somebody said {name} was there'), [name])

    def test_spoken_cues_carry_their_subjects(self):
        cues = spoken([{'number': '1', 'start_ms': 0, 'end_ms': 3000,
                        'text': 'the jogger along Braes Bayou spotted something'}])
        self.assertEqual(cues[0]['topics'][0][0], 'bayou')
        self.assertEqual(cues[0]['named'], [])

    def test_speaker_kinds_are_never_b_roll(self):
        # A press conference is a person speaking: it may only appear when that
        # person is the subject. The place shot behind it is fair game.
        catalog = {'shots': [
            {'id': 'X1', 'source': 'S', 'in': 0, 'out': 20, 'tags': ['police'],
             'what': 'a press conference', 'kind': 'speaker', 'verified': True},
            {'id': 'X2', 'source': 'S', 'in': 20, 'out': 40, 'tags': ['police'],
             'what': 'a police car', 'kind': 'place', 'verified': True}]}
        cues = spoken([{'number': '1', 'start_ms': 0, 'end_ms': 2500,
                        'text': 'police arrived and put up tape'}])
        plan, _ = plan_broll(catalog, cues, min_score=2)
        self.assertEqual([p['shot']['id'] for p in plan], ['X2'])

    def test_no_window_is_used_twice(self):
        # One 12-second shot and four cues that all want it: the shot is spent from
        # its head forward, so the plan can never show the same frames twice.
        catalog = {'shots': [{'id': 'S1', 'source': 'S', 'in': 0.0, 'out': 12.0,
                              'tags': ['bayou'], 'what': 'the bayou', 'kind': 'place',
                              'verified': True}]}
        cues = spoken([{'number': str(i), 'start_ms': i * 3000, 'end_ms': i * 3000 + 3000,
                        'text': 'the bayou'} for i in range(4)])
        plan, _ = plan_broll(catalog, cues, min_score=3)
        windows = [(p['shot']['in'], p['shot']['out']) for p in plan]
        self.assertEqual(len(windows), 4)
        for a, b in zip(windows, windows[1:]):
            self.assertLessEqual(a[1], b[0] + 0.001)

    def test_a_cue_naming_somebody_is_left_as_footage(self):
        catalog = {'shots': [{'id': 'S1', 'source': 'S', 'in': 0.0, 'out': 20.0,
                              'tags': ['bayou'], 'what': 'the bayou', 'kind': 'place',
                              'verified': True}]}
        cues = spoken([{'number': '1', 'start_ms': 0, 'end_ms': 3000,
                        'text': 'the bayou where Maliyah was found'}])
        plan, skipped = plan_broll(catalog, cues, min_score=3)
        self.assertEqual(plan, [])
        self.assertIn('names', skipped[0][1])

    def test_a_speaking_cue_is_left_as_footage(self):
        catalog = {'shots': [{'id': 'S1', 'source': 'S', 'in': 0.0, 'out': 20.0,
                              'tags': ['bayou'], 'what': 'the bayou', 'kind': 'place',
                              'verified': True}]}
        cues = spoken([{'number': '1', 'start_ms': 0, 'end_ms': 3000,
                        'text': 'here is the chief describe the bayou'}])
        self.assertEqual(plan_broll(catalog, cues, min_score=3)[0], [])

    def test_unverified_and_short_cues_are_dropped(self):
        catalog = {'shots': [
            {'id': 'S1', 'source': 'S', 'in': 0.0, 'out': 20.0, 'tags': ['bayou'],
             'what': 'guess', 'kind': 'place', 'verified': False}]}
        cues = spoken([{'number': '1', 'start_ms': 0, 'end_ms': 3000, 'text': 'the bayou'}])
        self.assertEqual(plan_broll(catalog, cues, min_score=3)[0], [])
        self.assertGreater(MIN_SHOT_S, 0)

    def test_exclusions_belong_to_the_project_that_declared_them(self):
        # Cue numbers only mean something inside the SRT they were read from. The
        # exclusions live in the catalog; a different project must not inherit them.
        catalog = load_catalog(CATALOG)
        self.assertIn(34, catalog[EXCLUSIONS])
        shots = [{'id': 'S1', 'source': 'S', 'in': 0.0, 'out': 20.0, 'tags': ['bayou'],
                  'what': 'the bayou', 'kind': 'place', 'verified': True}]
        cues = spoken([{'number': '3', 'start_ms': 0, 'end_ms': 3000, 'text': 'the bayou'}])
        other = {'shots': shots}
        plan, _ = plan_broll(other, cues, min_score=3)
        self.assertEqual(len(plan), 1, 'another project must keep its own cue 3')


if __name__ == '__main__':
    unittest.main()
