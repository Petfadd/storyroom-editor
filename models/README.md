# Detector model files

The editor works without anything in this folder. OpenCV's HOG people detector and Haar
face cascades ship inside OpenCV itself, so subject detection is available offline from
the moment the app runs.

Put files here only if you want MediaPipe's face and pose detection, which is more
accurate than HOG on faces that are turned or partly hidden.

## What to put here

Either or both:

- `blaze_face_short_range.tflite` — MediaPipe face detection
- `pose_landmarker_lite.task` — MediaPipe pose landmarks

MediaPipe normally downloads these on first use. That download is why they are optional:
this app never assumes a network connection, so if the files are absent the OpenCV
backend is used and the editor reports which backend is active rather than failing.

## Licensing

MediaPipe is Apache 2.0 and so are its model files. If you redistribute this app with
model files in this folder, ship the Apache 2.0 licence and the MediaPipe NOTICE
alongside them. Do not commit model files to this repository unless those files are
added with them.

## Why there is no model in this checkout

`storage.googleapis.com`, where MediaPipe hosts these files, is not reachable from the
build environment this code was written in. So the MediaPipe backend is implemented,
tested for its failure path, and left unconfigured here rather than pretending it works.
