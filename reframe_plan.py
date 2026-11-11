"""The reframe plan: what the analysis decided, and how it reaches ffmpeg.

A plan is data. It is stored on the project, shown in the editor, edited by hand, and
read by the renderer. Storing the decision rather than recomputing it at export time is
what makes manual correction possible at all: if export re-ran detection, a hand-moved
frame would be overwritten by the machine's opinion.

The plan is keyframes, not per-frame positions. Any keyframe can be moved, added, or
deleted by the editor, and the renderer turns whatever is left into one continuous
expression.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from reframe_track import Keyframe, SceneReframe

MODES = {
    'smart_crop': 'Smart subject crop. Fills the frame and follows the subject.',
    'blurred': 'Full scene over a blurred copy of itself. Nothing is cropped away.',
    'cinematic': 'Composed for vertical: subject-aware framing over a soft extension.',
}


def ramp_expression(times: list[float], values: list[float],
                    variable: str = 't') -> str:
    """Piecewise-linear x(t) as a sum of clamped ramps.

    ffmpeg evaluates this per frame, which is what makes the movement smooth. The
    alternative -- sending commands at intervals -- produced visible steps: a measured
    test gave 6.6px mean steps with a 14.7px maximum using this expression, and the
    stepped version could not even change the crop at all in this build.

    A zero-length segment is skipped rather than written as a division by zero.
    """
    if len(times) != len(values):
        raise ValueError('Each keyframe needs one time and one value.')
    if not times:
        raise ValueError('A path needs at least one keyframe.')
    terms = [f'{values[0]:.4f}']
    for i in range(len(times) - 1):
        span = times[i + 1] - times[i]
        change = values[i + 1] - values[i]
        if span <= 0:
            continue
        if abs(change) < 1e-9:
            continue
        terms.append(f'({change:.4f})*clip(({variable}-{times[i]:.4f})/{span:.4f},0,1)')
    return '+'.join(terms)


@dataclass
class ScenePlan:
    """One scene's contribution to the plan."""
    index: int
    start: float                      # scene start on the source clock, seconds
    duration: float
    keyframes: list[Keyframe] = field(default_factory=list)
    manual: bool = False
    locked: bool = False
    follow: int | None = None
    notes: list[str] = field(default_factory=list)

    def expression(self) -> str:
        """The x(t) expression for this scene, in scene-local time."""
        anchors = self._anchored()
        times = [k.t for k in anchors]
        values = [k.left for k in anchors]
        return ramp_expression(times, values)

    def _anchored(self) -> list[Keyframe]:
        """A path that starts at t=0 and ends at the scene's last frame.

        Scenes are rendered with their own clock, so the first keyframe must land on
        the very first frame and something must hold the position after the last
        keyframe -- otherwise the crop snaps back to its default mid-scene.
        """
        frames = [k for k in self.keyframes if k.width > 0]
        if not frames:
            return [Keyframe(0.0, 0.0, 0.0)]
        frames = sorted(frames, key=lambda k: k.t)
        if frames[0].t > 0:
            frames.insert(0, Keyframe(0.0, frames[0].left, frames[0].width))
        if frames[-1].t < self.duration:
            frames.append(Keyframe(self.duration, frames[-1].left, frames[-1].width))
        return frames

    def width(self) -> int:
        frames = self._anchored()
        return int(round(frames[0].width)) if frames else 0

    def as_dict(self) -> dict:
        return {'scene': self.index, 'start': round(self.start, 4),
                'duration': round(self.duration, 4),
                'keyframes': [k.as_dict() for k in self.keyframes],
                'manual': self.manual, 'locked': self.locked, 'follow': self.follow,
                'notes': self.notes}


@dataclass
class ReframePlan:
    """The whole decision, for one project and one output shape."""
    mode: str = 'smart_crop'
    width: int = 0                    # output width in pixels
    height: int = 0
    source_width: int = 0
    source_height: int = 0
    scenes: list[ScenePlan] = field(default_factory=list)
    detector: str = ''
    analysed_at: float = 0.0
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {'mode': self.mode, 'width': self.width, 'height': self.height,
                'source': {'width': self.source_width, 'height': self.source_height},
                'detector': self.detector, 'analysed_at': self.analysed_at,
                'scenes': [s.as_dict() for s in self.scenes], 'notes': self.notes,
                'usable': bool(self.scenes)}

    @classmethod
    def from_dict(cls, data: dict) -> 'ReframePlan':
        """Rebuild from stored JSON, tolerating an older or partial plan."""
        data = data or {}
        source = data.get('source') or {}
        plan = cls(mode=data.get('mode') or 'smart_crop',
                   width=int(data.get('width') or 0),
                   height=int(data.get('height') or 0),
                   source_width=int(source.get('width') or 0),
                   source_height=int(source.get('height') or 0),
                   detector=data.get('detector') or '',
                   analysed_at=float(data.get('analysed_at') or 0),
                   notes=list(data.get('notes') or []))
        for raw in data.get('scenes') or []:
            scene = ScenePlan(index=int(raw.get('scene') or 0),
                              start=float(raw.get('start') or 0),
                              duration=float(raw.get('duration') or 0),
                              manual=bool(raw.get('manual')),
                              locked=bool(raw.get('locked')),
                              follow=raw.get('follow'),
                              notes=list(raw.get('notes') or []))
            for k in raw.get('keyframes') or []:
                try:
                    scene.keyframes.append(Keyframe(float(k['t']), float(k['x']),
                                                    float(k['w'])))
                except (KeyError, TypeError, ValueError):
                    continue
            plan.scenes.append(scene)
        return plan


def plan_from_scenes(results: list[SceneReframe], mode: str, out_w: int, out_h: int,
                     source_w: int, source_h: int, detector: str = '') -> ReframePlan:
    """Assemble analysis output into a plan, with each scene's own clock."""
    plan = ReframePlan(mode=mode, width=out_w, height=out_h,
                       source_width=source_w, source_height=source_h,
                       detector=detector)
    clock = 0.0
    for result in results:
        keys = sorted(result.keyframes, key=lambda k: k.t)
        duration = (keys[-1].t - keys[0].t) if keys else 0.0
        scene = ScenePlan(index=result.scene_index, start=clock, duration=duration,
                          keyframes=list(keys), notes=list(result.notes))
        plan.scenes.append(scene)
        clock += duration
    return plan
