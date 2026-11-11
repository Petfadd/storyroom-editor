"""Splicing smart reframing into the existing renderer.

This is the only place the analysis modules touch the export. It takes the plan the
editor stored and returns a filter-graph fragment, or None when reframing does not apply
-- in which case the renderer keeps whatever it was already doing. Nothing here changes
audio, duration, frame rate or sync: the fragment edits the picture path only.

The technique is the one that measured clean. `crop` cannot animate in this ffmpeg
build (its x is not evaluated per frame, and sendcmd has no frame-step option), so the
frame is a fixed-size window and the full-size source slides inside it:

    [src] ... -> the full frame
    [win] color=black -> the window, at the window's own size
    overlay x=-(path) eval=frame -> the visible part

`overlay` is the only filter here that is genuinely temporal.

Two things are non-negotiable and both were learned from a broken render:

* `setsar=1` on the source branch, or overlay goes blank.
* A window width that is an even number, or a 1080-high source yields 607.5px and the
  output silently carries a 1216:1215 sample aspect ratio.
"""
from __future__ import annotations

from reframe_geometry import window_width_for_aspect


def smart_reframe_filter(path_expression: str, window_width: int, window_height: int,
                         source_height: int, out_width: int, out_height: int,
                         fps: int, aspect: str = 'crop') -> str:
    """The filter chain replacing the plain scale, for one scene.

    `path_expression` is x(t) in source pixels, already generated from the plan.
    """
    if window_width % 2:
        window_width += 1
    if window_height % 2:
        window_height += 1
    if window_width <= 0 or window_height <= 0:
        raise ValueError('A reframe window needs a positive size.')
    return (
        f'setsar=1,scale=-2:{source_height}[refsrc];'
        f'color=c=black:s={window_width}x{window_height}:r={fps}[refwin];'
        f'[refwin][refsrc]overlay=x=\'-({path_expression})\':y=0:eval=frame:shortest=1,'
        f'scale={out_width}:{out_height}:flags=lanczos,setsar=1'
    )


def blurred_extension_filter(out_width: int, out_height: int,
                            foreground_width: int) -> str:
    """Mode B/C background: the whole frame over a soft, larger copy of itself.

    Deliberately not generative. The background is the real footage, enlarged and
    blurred; nothing is invented, which is the rule for evidence footage.

    The foreground keeps its true aspect ratio and is centred, so no person is cropped
    out -- the opposite trade from mode A.
    """
    if foreground_width % 2:
        foreground_width += 1
    return (
        f'setsar=1,split=2[refback][reffront];'
        f'[refback]scale={out_width}:{out_height}:force_original_aspect_ratio=increase,'
        f'crop={out_width}:{out_height},gblur=sigma=28,eq=brightness=-0.12:saturation=0.8[refbg];'
        f'[reffront]scale={foreground_width}:-2[reffg];'
        f'[refbg][reffg]overlay=(W-w)/2:(H-h)/2:shortest=1'
    )


def window_for(source_width: int, source_height: int,
               out_width: int, out_height: int) -> tuple[int, int]:
    """The fixed window a plan's coordinates are expressed in."""
    width = window_width_for_aspect(source_height, out_width, out_height)
    return min(width, source_width), source_height
