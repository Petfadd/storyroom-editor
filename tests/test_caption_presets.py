import unittest

import captions


class PresetShapeTest(unittest.TestCase):
    def test_every_preset_has_a_label_detail_and_style(self):
        for key, (label, detail, css, ass) in captions.PRESETS.items():
            self.assertTrue(label and detail and css and ass, key)
            self.assertIn('FontName=', ass, key)

    def test_an_unknown_preset_falls_back_to_the_default(self):
        self.assertEqual(captions.get('nonsense'), captions.PRESETS[captions.DEFAULT])
        self.assertEqual(captions.ass_style('nonsense'), captions.ass_style(captions.DEFAULT))

    def test_boxed_presets_keep_a_nonzero_outline(self):
        """libass draws the BackColour plate only when the outline width is nonzero.

        With Outline=0 the plate silently disappears and the caption burns in as bare
        text, which made two different presets render identically. That was measured,
        not assumed, so it is pinned here.
        """
        for key in ('boxed', 'news', 'lower_band'):
            style = captions.ass_style(key)
            fields = dict(part.split('=', 1) for part in style.split(',') if '=' in part)
            self.assertEqual(fields['BorderStyle'], '3', key)
            self.assertNotEqual(float(fields['Outline']), 0,
                                f'{key} would lose its plate')

    def test_caption_size_follows_the_master_height(self):
        for key in captions.PRESETS:
            hd = captions.ass_style_for_size(key, 1080)
            uhd = captions.ass_style_for_size(key, 2160)
            hd_fields = dict(p.split('=', 1) for p in hd.split(',') if '=' in p)
            uhd_fields = dict(p.split('=', 1) for p in uhd.split(',') if '=' in p)
            self.assertEqual(float(uhd_fields['FontSize']),
                             2 * float(hd_fields['FontSize']),
                             f'{key}: 4K captions must not stay at 1080p size')
            self.assertEqual(float(uhd_fields['MarginV']),
                             2 * float(hd_fields['MarginV']))

    def test_catalogue_is_complete(self):
        data = captions.catalogue()
        self.assertEqual([p['key'] for p in data['presets']], list(captions.PRESETS))
        for preset in data['presets']:
            self.assertTrue(preset['css'], preset['key'])
        self.assertEqual(data['selected'], captions.DEFAULT)
