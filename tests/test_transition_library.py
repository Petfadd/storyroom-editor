import unittest
from pathlib import Path

import engine
import transitions


class LibraryTest(unittest.TestCase):
    """The catalogue and the renderer must not be able to drift apart."""

    def test_every_library_entry_has_a_renderer_filter(self):
        self.assertEqual(set(transitions.LIBRARY), set(engine.XFADE_NAMES),
                         'a card is offered that the renderer cannot produce')

    def test_only_cut_has_no_ffmpeg_filter(self):
        for key, (name, _, _) in transitions.LIBRARY.items():
            if key == 'cut':
                self.assertEqual(name, '')
            else:
                self.assertTrue(name, f'{key} is offered as a blend but names no filter')

    def test_catalogue_is_self_contained(self):
        data = transitions.catalogue()
        self.assertTrue(data['transitions'])
        for t in data['transitions']:
            self.assertTrue(t['label'] and t['detail'])
            self.assertEqual(t['overlaps'], bool(t['ffmpeg']))
        self.assertEqual(data['defaults']['intensity'],
                         transitions.DEFAULT_INTENSITY)
        self.assertEqual(data['defaults']['duration'], transitions.DEFAULT_DURATION)

    def test_intensity_names_only_real_transitions(self):
        for key, (_, allowed) in transitions.INTENSITY.items():
            for name in allowed:
                self.assertIn(name, transitions.LIBRARY, f'{key} names {name}')


class SuggestTest(unittest.TestCase):
    """Auto-suggest must fit each boundary, not stamp one look on the film."""

    def scenes(self):
        return [
            {'emotion': 'scrutiny', 'purpose': 'investigation',
             'asset_id': 'a', 'frame_in': 0, 'frame_out': 90},
            {'emotion': 'clarity', 'purpose': 'accountability',
             'asset_id': 'b', 'frame_in': 90, 'frame_out': 180},
            {'emotion': 'orientation', 'purpose': 'place',
             'asset_id': 'c', 'frame_in': 180, 'frame_out': 270},
            {'emotion': 'grief', 'purpose': 'remembrance',
             'asset_id': 'd', 'frame_in': 270, 'frame_out': 360},
        ]

    def test_the_last_scene_never_blends(self):
        keys = transitions.suggest_all(self.scenes())
        self.assertEqual(keys[-1], 'cut')

    def test_no_two_neighbouring_boundaries_repeat_an_effect(self):
        for intensity in transitions.INTENSITY:
            keys = transitions.suggest_all(self.scenes(), intensity)
            for a, b in zip(keys, keys[1:]):
                if a != 'cut':
                    self.assertNotEqual(a, b, f'{intensity}: {keys}')

    def test_a_run_inside_one_take_stays_a_cut(self):
        # Blending across a jump cut inside a single continuous shot reads as a fault.
        self.assertEqual(
            transitions.suggest({'emotion': 'grief', 'purpose': 'loss'}, same_source=True),
            'cut')

    def test_a_very_short_beat_gets_the_briefest_blend(self):
        key = transitions.suggest({'emotion': 'grief', 'purpose': 'loss'},
                                  duration_frames=9)
        self.assertIn(key, ('fadefast', 'cut'))

    def test_emotion_changes_the_choice(self):
        grief = transitions.suggest(
            {'emotion': 'grief', 'purpose': 'remembrance',
             'frame_in': 0, 'frame_out': 90}, intensity='expressive')
        scrutiny = transitions.suggest(
            {'emotion': 'scrutiny', 'purpose': 'investigation',
             'frame_in': 0, 'frame_out': 90}, intensity='expressive')
        self.assertNotEqual(grief, scrutiny,
                            'two opposite beats were given the same transition')

    def test_subtle_never_escalates_past_a_fade(self):
        for scene in self.scenes():
            for key in transitions.suggest_all([scene, scene], 'subtle'):
                self.assertIn(key, transitions.INTENSITY['subtle'][1])

    def test_the_result_is_not_all_cuts(self):
        keys = transitions.suggest_all(self.scenes())
        self.assertTrue(any(k != 'cut' for k in keys[:-1]),
                        'auto-suggest produced a film without a single transition')

    def test_a_run_inside_one_take_gets_no_blend(self):
        # Every beat starts on the same source frame: one continuous shot. A blend at
        # the entry into the take is fine, but every boundary inside it must stay a cut,
        # because blending across a jump cut inside one shot reads as a fault.
        take = [{'emotion': e, 'purpose': p, 'asset_id': 'a1', 'in_s': 0.0,
                 'frame_in': i * 90, 'frame_out': (i + 1) * 90}
                for i, (e, p) in enumerate([('grief', 'remembrance'),
                                            ('scrutiny', 'investigation'),
                                            ('clarity', 'accountability'),
                                            ('orientation', 'place')])]
        keys = transitions.suggest_all(take, 'expressive')
        self.assertEqual(keys[1:], ['cut'] * (len(take) - 1),
                         f'the inside of one take was blended: {keys}')

    def test_separate_beats_from_one_file_are_not_a_take(self):
        # A documentary draws many beats from one interview file. Those are separate
        # shots and take transitions like any other -- only touching source intervals
        # count as one continuous take.
        beats = [{'emotion': 'grief', 'purpose': 'remembrance', 'asset_id': 'a1',
                  'in_s': i * 3.0, 'frame_in': i * 90, 'frame_out': (i + 1) * 90}
                 for i in range(4)]
        self.assertFalse(transitions._inside_one_take(beats, 1))
        keys = transitions.suggest_all(beats, 'expressive')
        self.assertTrue(any(k != 'cut' for k in keys[:-1]),
                        'separate shots from one file were all left as cuts')

    def test_empty_timeline_is_harmless(self):
        self.assertEqual(transitions.suggest_all([]), [])


class ChainRenderTest(unittest.TestCase):
    """The blend chain must produce mixed transitions without changing length.

    A shortcut here would desynchronise narration for every scene after the first
    blend, so this renders real frames rather than inspecting the filter string.
    """

    def test_mixed_transitions_keep_the_programme_length(self):
        import subprocess, tempfile, re
        from pathlib import Path
        import imageio_ffmpeg
        import engine

        ff = imageio_ffmpeg.get_ffmpeg_exe()
        fps = 30
        spec = [('red', 30, 15, 'circleopen'), ('green', 30, 0, 'cut'),
                ('blue', 30, 10, 'fadeslow'), ('yellow', 24, 0, 'cut')]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shots, segments = [], []
            for i, (colour, dur, handle, tr) in enumerate(spec):
                total = dur + handle
                path = root / f's{i}.mp4'
                subprocess.run([ff, '-y', '-hide_banner', '-loglevel', 'error',
                                '-f', 'lavfi', '-i',
                                f'color={colour}:s=160x90:r={fps}:d={total/fps}',
                                '-c:v', 'libx264', '-pix_fmt', 'yuv420p',
                                '-frames:v', str(total), str(path)], check=True)
                segments.append(path)
                shots.append({'shot': i + 1, 'duration_frames': dur,
                              'transition': tr, 'transition_handle_frames': handle,
                              'subject': colour})
            job = engine.RenderJob.__new__(engine.RenderJob)
            job.story = {'shots': shots,
                         'settings': {'fps': fps, 'width': 160, 'height': 90}}
            job.work_dir = root
            job.threads = 2
            job.ffmpeg = ff
            job.crf = 23
            job.log = lambda *a, **k: None
            job.update = lambda **k: None
            job._stage = lambda name: (lambda: None)
            cmd, total, out = job._transition_chain(segments)
            self.assertEqual(total, sum(s['duration_frames'] for s in shots))
            subprocess.run(cmd, check=True)
            probe = subprocess.run([ff, '-hide_banner', '-i', str(out), '-map', '0:v',
                                    '-f', 'null', '-'], capture_output=True, text=True).stderr
            rendered = int(re.findall(r'frame=\s*(\d+)', probe)[-1])
            self.assertEqual(rendered, total,
                             'the transition chain changed the programme length')
            names = re.findall(r'xfade=transition=(\w+)',
                               subprocess.run(['true'], capture_output=True, text=True) and ' '.join(cmd))
            self.assertEqual(names, ['circleopen', 'fadeslow'],
                             'each boundary must use the transition its scene named')

    def test_no_transitions_returns_none_for_the_copy_path(self):
        import engine
        job = engine.RenderJob.__new__(engine.RenderJob)
        job.story = {'shots': [{'shot': 1, 'duration_frames': 30,
                                'transition': 'cut', 'transition_handle_frames': 0},
                               {'shot': 2, 'duration_frames': 30,
                                'transition': 'cut', 'transition_handle_frames': 0}],
                     'settings': {'fps': 30, 'width': 160, 'height': 90}}
        self.assertIsNone(job._transition_chain([Path('/x'), Path('/y')]))
