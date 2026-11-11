import unittest

import reframe_geometry as g


class WindowWidthTest(unittest.TestCase):
    """The 9:16 window must be an even number of pixels, or the SAR goes fractional."""

    def test_nine_by_sixteen_from_1080_is_seven_twenty_even(self):
        # 1080 * 9/16 is exactly 607.5. Rounding that to a 607-wide crop emitted a
        # 1216:1215 SAR; an even width keeps it 1:1.
        width = g.window_width_for_aspect(1080, 1080, 1920)
        self.assertEqual(width % 2, 0)
        self.assertEqual(width, 608)

    def test_square_and_four_five_windows(self):
        self.assertEqual(g.window_width_for_aspect(1080, 1080, 1080), 1080)
        self.assertEqual(g.window_width_for_aspect(1080, 1080, 1350), 864)
        self.assertEqual(g.window_width_for_aspect(1080, 1080, 1350) % 2, 0)

    def test_a_4k_source_scales_proportionally(self):
        self.assertEqual(g.window_width_for_aspect(2160, 1080, 1920), 1216)
        self.assertEqual(g.window_width_for_aspect(2160, 1080, 1920) % 2, 0)

    def test_nonsense_input_is_refused(self):
        for args in ((0, 1080, 1920), (1080, 0, 1920), (1080, 1080, 0)):
            with self.assertRaises(ValueError):
                g.window_width_for_aspect(*args)


class PlacementTest(unittest.TestCase):
    """Where the window goes for each of the six cases that must be tested."""

    def test_person_at_the_far_left(self):
        boxes = [g.Box(60, 200, 220, 700)]           # hard against the left edge
        self.assertEqual(g.required_width(boxes) < 608, True)
        w = g.place_window(boxes, 608, 1920).clamped(1920, 1080)
        self.assertEqual(w.left, 0, 'a subject at the left edge must not push the '
                                    'window off the frame')
        self.assertEqual(w.left + w.width, 608, 'and the full window must stay in frame')

    def test_person_at_the_far_right(self):
        boxes = [g.Box(1640, 200, 220, 700)]
        w = g.place_window(boxes, 608, 1920).clamped(1920, 1080)
        self.assertEqual(w.left + w.width, 1920, 'the window must reach the right edge')
        self.assertGreater(w.left, 1000)

    def test_person_moving_across_is_followed(self):
        # Left, middle, right: the window must move monotonically with the subject.
        positions = [60, 800, 1640]
        lefts = []
        for x in positions:
            w = g.place_window([g.Box(x, 200, 220, 700)], 608, 1920).clamped(1920, 1080)
            lefts.append(w.left)
        self.assertEqual(lefts, sorted(lefts), 'the window did not follow the subject')
        self.assertGreater(lefts[-1] - lefts[0], 1000, 'the window barely moved')

    def test_two_people_standing_apart(self):
        boxes = [g.Box(120, 200, 220, 700), g.Box(1500, 200, 220, 700)]
        needed = g.required_width(boxes)
        self.assertGreater(needed, 608, 'the geometry must report that they do not fit')
        w = g.place_window(boxes, 608, 1920).clamped(1920, 1080)
        self.assertAlmostEqual(w.centre(1080)[0], 920, delta=2,
                               msg='the window should sit on their shared midpoint')

    def test_speaker_at_a_podium_is_centred_and_stable(self):
        # A speaker barely moves; the window must not drift.
        lefts = []
        for dx in (0, 2, -1, 3, 0, 1):
            w = g.place_window([g.Box(800 + dx, 150, 300, 800)], 608, 1920).clamped(1920, 1080)
            lefts.append(w.left)
        self.assertLess(max(lefts) - min(lefts), 10,
                        'a nearly still subject produced a wandering frame')

    def test_wide_establishing_shot_with_no_subject(self):
        w = g.place_window([], 608, 1920).clamped(1920, 1080)
        self.assertEqual(w.left, 656, 'with nothing detected the frame holds the middle')
        self.assertAlmostEqual(w.centre(1080)[0], 960, delta=1)

    def test_margin_grows_with_the_subject(self):
        small = g.required_width([g.Box(0, 0, 100, 300)])
        large = g.required_width([g.Box(0, 0, 600, 900)])
        self.assertGreater(large - 600, small - 100,
                           'a large subject needs proportionally more air')

    def test_window_never_leaves_the_source(self):
        for width in (608, 1080, 1920, 4000):
            for left in (-500, 0, 900, 5000):
                w = g.Window(width=width, left=left).clamped(1920, 1080)
                self.assertGreaterEqual(w.left, 0)
                self.assertLessEqual(w.left + w.width, 1920 + 0.001)
                self.assertGreaterEqual(w.width, 2)


class BoxTest(unittest.TestCase):
    def test_containment_is_proportional_to_the_inner_box(self):
        face = g.Box(100, 100, 50, 50)
        body = g.Box(80, 90, 200, 400)
        self.assertEqual(face.overlaps_fraction(body), 1.0)
        self.assertLess(body.overlaps_fraction(face), 0.1)

    def test_touching_boxes_do_not_overlap(self):
        self.assertEqual(g.Box(0, 0, 10, 10).overlaps_fraction(g.Box(10, 0, 10, 10)), 0.0)

    def test_empty_list_has_no_span(self):
        self.assertIsNone(g.subject_span([]))
        self.assertEqual(g.required_width([]), 0.0)


if __name__ == '__main__':
    unittest.main()
