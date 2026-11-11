"""Analysing a source clip and producing a reframe plan.

Cost is controlled by not looking at most frames. A clip is sampled about once a second,
plus once at each scene boundary, and each sample costs one detector pass. A three
minute clip is therefore roughly 180 detector calls rather than 5400, and because the
tracker interpolates between samples the result is still smooth.

Scene boundaries come from PySceneDetect when it is installed, and from a cheap frame
difference otherwise. Both are optional: with neither, the whole clip is one scene, which
is still correct -- just less precise about where the subject changes.
"""
from __future__ import annotations

import math
import subprocess
from dataclasses import dataclass
from pathlib import Path

import cv2

import reframe_track
from reframe_detect import Detector, choose_detector
from reframe_geometry import window_width_for_aspect

# Seconds between detector passes. Below about half a second the cost climbs steeply and
# the extra samples tell the tracker almost nothing new.
SAMPLE_INTERVAL = 1.0

# A cut is only trusted if the scene is at least this long, so a flash or a whip pan
# cannot split a scene in two and force a re-decision.
MIN_SCENE_SECONDS = 0.6

# Relative cut detection, for when PySceneDetect is not installed. A frame difference
# counts as a cut when it is both above a small floor and a multiple of what this clip
# normally does between frames. Measured cut: 9.5 against a 0.4 typical difference.
CUT_FLOOR = 3.0
CUT_RATIO = 3.0


@dataclass
class AnalysisResult:
    plan: 'object'
    scenes: list
    samples: int
    detector: str
    duration: float
    notes: list


def probe_duration(path: Path) -> float:
    try:
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
        cap.release()
        if fps > 0 and frames > 0:
            return float(frames) / float(fps)
    except Exception:                                    # pragma: no cover
        pass
    return 0.0


def detect_scenes(path: Path, duration: float) -> list[float]:
    """Boundary times in seconds, always starting at 0.0.

    Adaptive detection comes first. The default ContentDetector threshold of 27 is too
    high for a real cut between two similar-looking shots: on a measured four-scene clip
    it found no boundaries at all, while the adaptive detector found all four. Missing a
    cut is the expensive failure, because the frame is then tracked smoothly across a
    change of scene, which is exactly the mistake reframing must not make.
    """
    try:
        from scenedetect import detect, AdaptiveDetector
        scenes = detect(str(path), AdaptiveDetector())
        cuts = _keep_meaningful([0.0] + [start.get_seconds() for start, _ in scenes[1:]])
        if len(cuts) > 1:
            return cuts
    except Exception:
        pass
    try:
        from scenedetect import detect, ContentDetector
        scenes = detect(str(path), ContentDetector(threshold=12))
        cuts = _keep_meaningful([0.0] + [start.get_seconds() for start, _ in scenes[1:]])
        if len(cuts) > 1:
            return cuts
    except Exception:
        pass
    return _fallback_scenes(path, duration)


def _keep_meaningful(cuts: list[float]) -> list[float]:
    """Drop boundaries that would make a scene shorter than MIN_SCENE_SECONDS."""
    kept = [cuts[0]] if cuts else [0.0]
    for moment in sorted(cuts[1:]):
        if moment - kept[-1] >= MIN_SCENE_SECONDS:
            kept.append(moment)
    return kept


def _fallback_scenes(path: Path, duration: float) -> list[float]:
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    cuts = [0.0]
    history: list[float] = []
    previous = None
    index = 0
    step = max(1, int(fps / 4))          # four looks per second is plenty for a cut
    while True:
        ret = cap.grab()
        if not ret:
            break
        if index % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            small = cv2.resize(frame, (64, 36), interpolation=cv2.INTER_AREA)
            grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY).astype('float32')
            if previous is not None:
                difference = float(abs(grey - previous).mean())
                seconds = index / fps
                history.append(difference)
                # A cut is judged RELATIVE to how much this footage normally changes.
                # A fixed threshold cannot work: a measured cut between two similar
                # shots moved only 9.5 on this scale, well under any sane absolute
                # number, while a real camera pan moves continuously and would trip a
                # low one. Comparing against the recent median handles both.
                recent = sorted(history[-8:])
                typical = recent[len(recent) // 2]
                strong = difference > max(CUT_FLOOR, typical * CUT_RATIO)
                if strong and seconds - cuts[-1] >= MIN_SCENE_SECONDS:
                    cuts.append(seconds)
            previous = grey
        index += 1
    cap.release()
    return cuts


def sample_times(cuts: list[float], duration: float,
                 interval: float = SAMPLE_INTERVAL) -> list[float]:
    """When to look. Every boundary gets a sample, plus a regular grid inside a scene."""
    if duration <= 0:
        return [0.0]
    bounds = sorted(set([0.0] + [c for c in cuts if 0 < c < duration]))
    if duration - bounds[-1] < MIN_SCENE_SECONDS and len(bounds) > 1:
        bounds.pop()                     # a sliver at the end is not its own scene
    times: list[float] = []
    for i, start in enumerate(bounds):
        end = bounds[i + 1] if i + 1 < len(bounds) else duration
        # Always sample the first frame of a scene, and the last usable one before the
        # next cut, so the tracker has something to anchor both ends to.
        times.append(round(start, 4))
        moment = start + interval
        while moment < min(end, duration) - 1e-6:
            times.append(round(moment, 4))
            moment += interval
        tail = min(end, duration) - 1.0 / 30.0
        if tail > start + 1e-6:
            times.append(round(tail, 4))
    ordered = sorted(set(times))
    return [t for t in ordered if 0 <= t < duration]


def read_frame(cap, seconds: float):
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
    ok, frame = cap.read()
    return frame if ok else None


def analyse(path: Path, out_width: int, out_height: int, mode: str = 'smart_crop',
            detector: Detector | None = None,
            progress=None, context=None) -> AnalysisResult:
    """Analyse one source clip into a plan. Silent, offline, no network.

    `context` is the queue's job handle. Detecting people is a run of heavy steps on
    opens frames, so between samples the job is given the chance to stop: cancel and
    pause both take effect within one sample rather than at the end of the clip.
    """
    import reframe_plan

    path = Path(path)
    duration = probe_duration(path)
    source_w = source_h = 0
    cap = cv2.VideoCapture(str(path))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    source_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
    source_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    if not duration:
        duration = 0.0

    detector = detector or choose_detector()
    cuts = detect_scenes(path, duration) if duration else [0.0]
    times = sample_times(cuts, duration)

    samples: list[tuple[float, list]] = []
    for i, moment in enumerate(times):
        if context is not None:
            # Raises JobCancelled when cancelled, and blocks while paused.
            context.wait_if_paused()
            if context.cancelled:
                cap.release()
                from jobqueue import JobCancelled
                raise JobCancelled()
        frame = read_frame(cap, moment)
        if frame is None:
            continue
        boxes = detector.detect(frame)
        # Person boxes are numbered by position at each sample. Spatially adjacent
        # detections get the same identity, which is enough for "who is the main
        # subject" without the cost of running a full tracker.
        for n, box in enumerate(sorted(boxes, key=lambda b: b.cx)):
            box.subject = n
        samples.append((moment, boxes))
        if progress:
            progress(i + 1, len(times))
    cap.release()

    width = min(window_width_for_aspect(source_h or out_height, out_width, out_height),
                source_w or out_width)

    groups = reframe_track.group_by_scene(samples, cuts[1:] if len(cuts) > 1 else [])
    scene_results = []
    for index, group in enumerate(groups):
        scene_results.append(reframe_track.plan_scene(group, index, width, source_w or out_width))
    if not scene_results:
        scene_results = [reframe_track.plan_scene([], 0, width, source_w or out_width)]

    plan = reframe_plan.plan_from_scenes(scene_results, mode, out_width, out_height,
                                         source_w, source_h, detector.name)
    plan.duration = duration
    notes = []
    for result in scene_results:
        notes.extend(result.notes)
    if not any(s.notes for s in plan.scenes):
        notes.append('nothing was detected; every scene holds the middle')
    return AnalysisResult(plan=plan, scenes=scene_results, samples=len(samples),
                          detector=detector.name, duration=duration, notes=notes)
