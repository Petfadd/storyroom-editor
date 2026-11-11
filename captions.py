"""Caption presets: the looks a caption can wear, in one place.

A preset is a small, fixed record rather than a free-form style editor. Captions sit on
top of documentary footage that already carries verification and rights constraints, so
the useful choice is which of a handful of legible treatments to use -- not an unbounded
font-and-colour picker that can produce something unreadable and then be burned into the
master.

Each preset maps to two renderings of the same choice: the CSS the editor previews with,
and the libass style string ffmpeg burns in with. Keeping them side by side is what stops
the preview from promising a look the export does not deliver.
"""
from __future__ import annotations

# key -> (label, description, css preview, ass style overrides)
#
# ASS colours are &HAABBGGRR with AA as the alpha byte. `MarginV` is in pixels at the
# 1080p subtitle canvas the renderer already uses.
#
# Every boxed preset carries `Outline=1`. That is not decoration: libass draws the
# BackColour box only when the outline width is nonzero, so `Outline=0` silently drops
# the plate and the caption renders as bare text. Two presets that differed only in
# BackColour were therefore burned in identically until this was measured.
PRESETS = {
    'clean': (
        'Clean', 'White, soft shadow, lower third.',
        'font-weight:650;color:#fff;text-shadow:0 2px 6px rgba(0,0,0,.85);'
        'background:none;padding:0;border-radius:0;',
        'FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H00101010,'
        'BorderStyle=1,Outline=0,Shadow=2.5,Alignment=2,MarginV=34,MarginL=85,MarginR=85',
    ),
    'boxed': (
        'Boxed', 'White on a solid plate.',
        'font-weight:700;color:#fff;background:rgba(0,0,0,.72);padding:2px 10px;border-radius:4px;'
        'text-shadow:none;',
        'FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00FFFFFF,BackColour=&H99000000,'
        'BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=34,MarginL=85,MarginR=85',
    ),
    'news': (
        'News', 'Yellow band, upper third.',
        'font-weight:800;color:#0d0d0d;background:#f2c94c;padding:2px 12px;border-radius:2px;'
        'text-shadow:none;text-transform:uppercase;letter-spacing:.02em;',
        'FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00101010,BackColour=&H004CC9F2,'
        'BorderStyle=3,Outline=1,Shadow=0,Alignment=8,MarginV=34,MarginL=0,MarginR=0',
    ),
    'lower_band': (
        'Lower plate', 'Solid plate, well clear of the frame edge.',
        'font-weight:600;color:#fff;background:rgba(0,0,0,.86);padding:6px 16px;'
        'border-radius:3px;text-shadow:none;',
        'FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00FFFFFF,BackColour=&H1E000000,'
        'BorderStyle=3,Outline=1,Shadow=0,Alignment=2,MarginV=64,MarginL=85,MarginR=85',
    ),
    'kinetic': (
        'Emphasis', 'Heavy white with a red edge.',
        'font-weight:800;color:#fff;text-shadow:0 0 0 #ee455f,0 3px 10px rgba(0,0,0,.9);'
        'background:none;padding:0;border-radius:0;',
        'FontName=DejaVu Sans,FontSize=16,PrimaryColour=&H00FFFFFF,OutlineColour=&H005F45EE,'
        'BorderStyle=1,Outline=3.4,Shadow=0,Alignment=2,MarginV=34,MarginL=85,MarginR=85',
    ),
}

DEFAULT = 'clean'


def get(key):
    return PRESETS.get(key, PRESETS[DEFAULT])


def ass_style(key):
    """The libass style string for a preset, at the renderer's 1080p default."""
    return get(key)[3]


def ass_style_for_size(key, height):
    """Scale the style to the actual master height.

    FontSize and margins are authored for 1080p. A 4K master that kept 16px captions
    would render them a quarter of their intended size, so the numbers move with the
    picture.
    """
    style = ass_style(key)
    scale = (height or 1080) / 1080
    out = []
    for part in style.split(','):
        name, _, value = part.partition('=')
        if name in ('FontSize', 'MarginV', 'MarginL', 'MarginR'):
            try:
                value = str(max(0, round(float(value) * scale, 2)))
                if value.endswith('.0'):
                    value = value[:-2]
            except ValueError:
                pass
        out.append(f'{name}={value}')
    return ','.join(out)


def css(key):
    return get(key)[2]


def catalogue():
    return {'presets': [{'key': k, 'label': v[0], 'detail': v[1], 'css': v[2]}
                        for k, v in PRESETS.items()],
            'selected': DEFAULT}
