import unittest

import reframe_render as R


class SmartFilterTest(unittest.TestCase):
    def build(self, **over):
        args = dict(path_expression='100.0000', window_width=608, window_height=1080,
                    source_height=1080, out_width=1080, out_height=1920, fps=30)
        args.update(over)
        return R.smart_reframe_filter(**args)

    def test_the_source_is_forced_square_pixel(self):
        # Without setsar=1 the overlay renders blank: measured, not theoretical.
        self.assertIn('setsar=1', self.build().split('[refsrc]')[0])

    def test_the_overlay_is_evaluated_per_frame(self):
        self.assertIn('eval=frame', self.build())

    def test_the_window_has_its_own_clock_rate(self):
        self.assertIn('s=608x1080:r=30', self.build())

    def test_the_expression_is_negated_into_the_overlay_x(self):
        self.assertIn("x='-(100.0000)'", self.build())

    def test_an_odd_window_is_rounded_to_even(self):
        # 607 wide from a 1080-high source produced a 1216:1215 SAR.
        graph = self.build(window_width=607)
        self.assertIn('s=608x1080', graph)

    def test_output_is_scaled_and_forced_square_pixel_again(self):
        graph = self.build(out_width=1080, out_height=1920)
        self.assertIn('scale=1080:1920', graph)
        self.assertTrue(graph.rstrip().endswith('setsar=1'))

    def test_other_shapes_are_supported(self):
        square = self.build(out_width=1080, out_height=1080, window_width=1080)
        self.assertIn('scale=1080:1080', square)
        landscape = self.build(out_width=1920, out_height=1080, window_width=1920)
        self.assertIn('scale=1920:1080', landscape)

    def test_a_zero_window_is_refused_rather_than_rendering_nothing(self):
        with self.assertRaises(ValueError):
            self.build(window_width=0)

    def test_no_stretch_is_requested(self):
        # Every scale either states both dimensions from a known-ratio window or uses
        # -2 to keep the aspect ratio.
        graph = self.build()
        self.assertNotIn('force_original_aspect_ratio=increase,setsar', graph)


class BlurredExtensionTest(unittest.TestCase):
    def graph(self, **over):
        args = dict(out_width=1080, out_height=1920, foreground_width=1080)
        args.update(over)
        return R.blurred_extension_filter(**args)

    def test_the_background_is_real_footage_blurred_not_generated(self):
        graph = self.graph()
        self.assertIn('gblur', graph)
        self.assertIn('split=2', graph)
        self.assertNotIn('fillborders', graph)

    def test_the_whole_frame_keeps_its_ratio(self):
        self.assertIn('force_original_aspect_ratio=increase', self.graph())

    def test_the_foreground_keeps_its_shape(self):
        self.assertIn('scale=1080:-2', self.graph())

    def test_the_foreground_is_centred(self):
        self.assertIn('overlay=(W-w)/2:(H-h)/2', self.graph())


class WindowTest(unittest.TestCase):
    def test_nine_by_sixteen_from_1080p(self):
        self.assertEqual(R.window_for(1920, 1080, 1080, 1920), (608, 1080))

    def test_square_from_1080p(self):
        self.assertEqual(R.window_for(1920, 1080, 1080, 1080), (1080, 1080))

    def test_a_narrow_source_is_never_widened_past_itself(self):
        width, height = R.window_for(720, 1080, 1080, 1920)
        self.assertLessEqual(width, 720)

    def test_landscape_output_uses_the_full_width(self):
        self.assertEqual(R.window_for(1920, 1080, 1920, 1080), (1920, 1080))


if __name__ == '__main__':
    unittest.main()
