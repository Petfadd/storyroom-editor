"""Who is in the frame, and where.

Two backends behind one interface:

* `MediaPipeDetector` -- face, person and pose landmarks. Preferred. It needs model
  files that MediaPipe downloads on first use, so it is genuinely optional: on a
  machine without them, or without the package, the app must still reframe.
* `OpenCVDetector`   -- HOG people detection plus Haar face cascades, both shipped
  inside OpenCV. No downloads, no network, and measurably able to find a small figure
  in a wide landscape shot (58ms/frame at 640px).

The interface is deliberately small: `detect(frame) -> [Box]`. Everything above this
layer -- tracking, smoothing, scene handling -- is identical for both backends, which is
what makes the fallback a real fallback rather than a second implementation.

Detection is expensive, so `sample_times` exists to decide WHEN to look. The rule is
one look per second by default, plus one at the start of a scene; between looks the
tracker carries the subject forward.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from reframe_geometry import Box

# Detectors work at this width. Measured on a real full-body photograph: 480px gave
# confidence 1.24, 640px 1.56, 800px 1.79, and 1000px dropped back to 1.29 while costing
# twice as long. 800 is both the most reliable and still under 100ms per frame, so it is
# where a wide landscape frame is scanned before boxes are scaled back up.
ANALYSIS_WIDTH = 800

# Below this, a detection is not trusted. HOG scores are an SVM margin, not a
# probability, so this number is calibrated against measured output, not theory.
HOG_MIN_SCORE = 0.35


@dataclass
class DetectorInfo:
    backend: str
    available: bool
    detail: str


class Detector:
    """Common behaviour: scale the frame down, detect, scale the boxes back up."""

    name = 'base'

    def __init__(self):
        self._scale_cache: dict[tuple[int, int], float] = {}

    # -- subclass hooks ----------------------------------------------------
    def _detect_full(self, frame: np.ndarray) -> list[Box]:
        raise NotImplementedError

    def info(self) -> DetectorInfo:
        raise NotImplementedError

    # -- public ------------------------------------------------------------
    def detect(self, frame: np.ndarray) -> list[Box]:
        """Boxes in ORIGINAL frame pixels, whatever the internal working scale."""
        if frame is None or frame.size == 0:
            return []
        h, w = frame.shape[:2]
        scale = self._analysis_scale(w)
        if scale < 1.0:
            small = cv2.resize(frame, (max(1, int(w * scale)), max(1, int(h * scale))),
                               interpolation=cv2.INTER_AREA)
        else:
            small = frame
        boxes = self._detect_full(small)
        if scale >= 1.0:
            return boxes
        inverse = 1.0 / scale
        return [Box(x=b.x * inverse, y=b.y * inverse, w=b.w * inverse, h=b.h * inverse,
                    kind=b.kind, score=b.score) for b in boxes]

    def _analysis_scale(self, width: int) -> float:
        if width in self._scale_cache:
            return self._scale_cache[width]
        scale = 1.0 if width <= ANALYSIS_WIDTH else ANALYSIS_WIDTH / width
        self._scale_cache[width] = scale
        return scale


class OpenCVDetector(Detector):
    """HOG bodies plus Haar faces. Ships with OpenCV, so it always works offline."""

    name = 'opencv'

    def __init__(self):
        super().__init__()
        self._hog = None
        self._faces = None
        self._profiles = None

    def _load(self):
        if self._hog is not None:
            return
        self._hog = cv2.HOGDescriptor()
        self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
        self._faces = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        self._profiles = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_profileface.xml')

    def info(self) -> DetectorInfo:
        try:
            self._load()
        except Exception as error:                       # pragma: no cover
            return DetectorInfo('opencv', False, f'OpenCV detectors unavailable: {error}')
        return DetectorInfo('opencv', True,
                            'HOG people and Haar faces from OpenCV; no downloads needed.')

    def _detect_full(self, frame: np.ndarray) -> list[Box]:
        self._load()
        boxes: list[Box] = []
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

        # Faces first: a face is the strongest evidence that a person matters, and its
        # position is what keeps heads inside the frame.
        for cascade, kind in ((self._faces, 'face'), (self._profiles, 'face')):
            try:
                found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5,
                                                 minSize=(24, 24))
            except cv2.error:                            # pragma: no cover
                continue
            for (x, y, w, h) in found:
                boxes.append(Box(float(x), float(y), float(w), float(h),
                                 kind=kind, score=0.8))

        # Bodies: this is what finds a distant person in a wide shot, where no face is
        # legible. It is the expensive half, so it runs once per sampled frame only.
        rects, weights = self._hog.detectMultiScale(
            frame, winStride=(8, 8), padding=(8, 8), scale=1.05)
        for rect, weight in zip(rects, (weights.flatten() if len(weights) else [])):
            score = float(weight)
            if score < HOG_MIN_SCORE:
                continue
            x, y, w, h = (float(v) for v in rect)
            boxes.append(Box(x, y, w, h, kind='body',
                             score=min(1.0, score / 2.0)))
        return boxes


class MediaPipeDetector(Detector):
    """MediaPipe Tasks face and person detection.

    MediaPipe 1.x removed the `mp.solutions` API, so this uses the Tasks API and needs
    external `.task` model files. Those are downloaded by MediaPipe on first use, which
    means this backend can be unavailable offline and MUST fail softly.
    """

    name = 'mediapipe'

    def __init__(self, models_dir: Path | str | None = None):
        super().__init__()
        self.models_dir = Path(models_dir) if models_dir else None
        self._face = None
        self._pose = None
        self._error = ''

    def _find_model(self, *names: str) -> Path | None:
        if not self.models_dir or not self.models_dir.is_dir():
            return None
        for name in names:
            candidate = self.models_dir / name
            if candidate.is_file() and candidate.stat().st_size > 0:
                return candidate
        return None

    def _load(self) -> bool:
        if self._face is not None:
            return True
        if self._error:
            return False
        try:
            import mediapipe as mp
        except Exception as error:
            self._error = f'MediaPipe is not installed: {error}'
            return False
        face_model = self._find_model('blaze_face_short_range.tflite',
                                      'face_detector.task')
        pose_model = self._find_model('pose_landmarker_lite.task')
        if not face_model and not pose_model:
            self._error = ('MediaPipe is installed but its model files are not. '
                           'Put blaze_face_short_range.tflite or '
                           'pose_landmarker_lite.task in the models folder.')
            return False
        try:
            vision = mp.tasks.vision
            base = mp.tasks.BaseOptions
            if face_model:
                options = vision.FaceDetectorOptions(
                    base_options=base(model_asset_path=str(face_model)),
                    min_detection_confidence=0.4)
                self._face = vision.FaceDetector.create_from_options(options)
            if pose_model:
                options = vision.PoseLandmarkerOptions(
                    base_options=base(model_asset_path=str(pose_model)),
                    min_pose_detection_confidence=0.4)
                self._pose = vision.PoseLandmarker.create_from_options(options)
            return True
        except Exception as error:                       # pragma: no cover
            self._error = f'MediaPipe models could not be loaded: {error}'
            return False

    def info(self) -> DetectorInfo:
        if self._load():
            return DetectorInfo('mediapipe', True,
                                'MediaPipe face and pose detection.')
        return DetectorInfo('mediapipe', False, self._error or 'MediaPipe unavailable.')

    def _detect_full(self, frame: np.ndarray) -> list[Box]:
        if not self._load():
            return []
        import mediapipe as mp
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
        h, w = frame.shape[:2]
        boxes: list[Box] = []
        if self._face is not None:
            try:
                result = self._face.detect(image)
                for detection in result.detections:
                    bbox = detection.bounding_box
                    boxes.append(Box(float(bbox.origin_x), float(bbox.origin_y),
                                     float(bbox.width), float(bbox.height),
                                     kind='face',
                                     score=float(detection.categories[0].score)
                                     if detection.categories else 0.7))
            except Exception:                            # pragma: no cover
                pass
        if self._pose is not None:
            try:
                result = self._pose.detect(image)
                for landmarks in result.pose_landmarks or []:
                    xs = [p.x * w for p in landmarks if p.visibility > 0.5]
                    ys = [p.y * h for p in landmarks if p.visibility > 0.5]
                    if not xs:
                        continue
                    boxes.append(Box(min(xs), min(ys), max(xs) - min(xs),
                                     max(ys) - min(ys), kind='body', score=0.9))
            except Exception:                            # pragma: no cover
                pass
        return boxes


def choose_detector(models_dir: Path | str | None = None) -> Detector:
    """MediaPipe when it is genuinely usable, OpenCV otherwise.

    This never raises and never blocks: an editor that cannot analyse footage must
    still open the project.
    """
    if os.environ.get('STORYROOM_DETECTOR', '').lower() == 'opencv':
        return OpenCVDetector()
    try:
        media = MediaPipeDetector(models_dir)
        if media.info().available:
            return media
    except Exception:                                    # pragma: no cover
        pass
    return OpenCVDetector()


def describe() -> dict:
    """What this machine can actually do, for the UI to report honestly."""
    models = Path(__file__).resolve().parent / 'models'
    media = MediaPipeDetector(models).info()
    opencv = OpenCVDetector().info()
    return {
        'mediapipe': {'available': media.available, 'detail': media.detail},
        'opencv': {'available': opencv.available, 'detail': opencv.detail},
        'preferred': 'mediapipe' if media.available else 'opencv',
    }
