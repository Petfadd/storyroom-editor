"""The geometry of reframing a wide picture into a tall one.

Everything in this module is arithmetic. It takes boxes and returns window positions,
so it can be tested exactly, without a detector, a video or a GPU. The detector's job is
to produce boxes; deciding where the window goes is done here, once, where the rules can
be read and checked.

The central object is a `Window`: the region of the source that will fill the output.
It is defined by its width in source pixels (the height is always the full source height
for a full-bleed crop) and its left edge. `resolve()` clamps it to the source.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Box:
    """A detected subject, in source pixels, at a moment in time."""
    x: float
    y: float
    w: float
    h: float
    kind: str = 'person'          # person | face | body | object
    score: float = 1.0            # detector confidence, 0..1
    subject: int | None = None    # which tracked person this belongs to

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def overlaps_fraction(self, other: 'Box') -> float:
        """How much of THIS box lies inside the other, 0..1."""
        if self.area <= 0:
            return 0.0
        left = max(self.x, other.x)
        right = min(self.x + self.w, other.x + other.w)
        top = max(self.y, other.y)
        bottom = min(self.y + self.h, other.y + other.h)
        if right <= left or bottom <= top:
            return 0.0
        return ((right - left) * (bottom - top)) / self.area


@dataclass
class Window:
    """The source region that fills the output frame.

    `left` is the x of the window's left edge in source pixels. `width` is how many
    source pixels the window spans. The window is full height unless `top`/`height`
    are set, which only the inset modes use.
    """
    width: float
    left: float
    height: float | None = None
    top: float = 0.0

    def clamped(self, source_width: float, source_height: float) -> 'Window':
        width = min(max(2.0, self.width), source_width)
        height = min(max(2.0, self.height if self.height else source_height), source_height)
        left = min(max(0.0, self.left), source_width - width)
        top = min(max(0.0, self.top), source_height - height)
        return Window(width=width, left=left, height=height, top=top)

    def centre(self, source_height: float | None = None) -> tuple[float, float]:
        h = self.height if self.height else (source_height or 0)
        return (self.left + self.width / 2, self.top + h / 2)


def window_width_for_aspect(source_height: float, out_w: int, out_h: int) -> int:
    """Source pixels across that fill an out_w:out_h frame of this source height.

    Always even, because an odd crop width makes ffmpeg emit a fractional sample
    aspect ratio: a 1080-high source needs 607.5px for 9:16, and rounding that to 608
    without an even width produced SAR 1216:1215 -- subtly non-square pixels in a
    file that looked fine in a player. Even numbers keep SAR 1:1 exact.
    """
    if source_height <= 0 or out_w <= 0 or out_h <= 0:
        raise ValueError('Frame and output dimensions must be positive.')
    exact = source_height * (out_w / out_h)
    even = int(round(exact / 2.0) * 2)
    return max(2, min(even, int(source_height * 8)))     # sanity bound


def subject_span(boxes: list[Box]) -> tuple[float, float] | None:
    """Left and right edge covering every box."""
    boxes = [b for b in boxes if b.w > 0 and b.h > 0]
    if not boxes:
        return None
    return (min(b.x for b in boxes), max(b.x + b.w for b in boxes))


def required_width(boxes: list[Box], margin: float = 0.12) -> float:
    """How wide a window has to be to hold these subjects with breathing room.

    `margin` is the share of the subject's own width added on each side, so a face is
    not flush against the frame edge. It is proportional rather than fixed in pixels
    because a 200px face and a 900px face need different amounts of air.
    """
    span = subject_span(boxes)
    if not span:
        return 0.0
    left, right = span
    widest = max((b.w for b in boxes), default=0)
    pad = widest * margin
    return (right - left) + 2 * pad


def place_window(boxes: list[Box], desired_width: float,
                 source_width: float) -> Window:
    """A window of the given width that holds these subjects as well as it can.

    The rule, in order:
    1. If the subjects fit inside the width, centre the window on them.
    2. If they do not fit, centre it on their midpoint anyway. The caller is told the
       subjects do not fit by comparing `required_width` to `desired_width`; deciding
       what to do about it -- follow one person, or widen -- belongs to the caller,
       because it is an editorial choice, not arithmetic.
    """
    if desired_width <= 0:
        raise ValueError('Window width must be positive.')
    span = subject_span(boxes)
    if not span:
        # Nothing detected: hold the middle rather than inventing a position.
        return Window(width=desired_width, left=(source_width - desired_width) / 2)
    left, right = span
    centre = (left + right) / 2
    return Window(width=desired_width, left=centre - desired_width / 2)


@dataclass
class Subject:
    """One tracked person across a scene."""
    index: int
    boxes: list[Box] = field(default_factory=list)
    score: float = 0.0
    speaking: float = 0.0
