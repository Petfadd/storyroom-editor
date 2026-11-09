# Video intake — how to get footage to the agent

You cannot upload a video to a chat and expect it to be *watched*. What works is
turning the video into files an AI can read: labelled contact sheets of frames,
a waveform of the soundtrack, and a metadata report. That is what this folder
explains, and `tools/watch_video.py` does the work.

## What to upload

| Deliverable | Upload this | Why |
| --- | --- | --- |
| Review a cut / promos / rough edit | the `.mp4` itself | frames + audio are read directly |
| Feedback on a still or thumbnail | `.jpg` / `.png` | read at full detail |
| Multiple clips | one file per clip, same batch | no archive needed, see below |
| A script or shot list | `.txt` / `.md` / `.docx` | read as text |

**Fastest source format:** MP4 with H.264 video and AAC audio. MOV works too.
MKV, WebM, M4V, AVI and animated GIFs also work, but MP4/MOV is what the machine
decodes most reliably.

**Never send:** `.zip`, `.rar`, `.7z`, `.tar`. Archives are rejected by the
uploader. You do not need them — a folder of individual files works better,
because every file arrives with its real name and stays inspectable.

## Practical limits

- Keep each clip **under ~90 seconds** (a 30–60 s cut is ideal). A contact sheet
  samples the timeline; the shorter the clip, the better each frame is described.
- For a long edit, send the **first 60 s and the last 60 s** as separate files, or
  export at half resolution. A 10-minute 4K master is mostly dead weight.
- 1080p or 720p is plenty. 4K is re-scaled down anyway and only slows the run.
- Large files are fine on disk, but uploading several GB will time out before it
  finishes. If a file is above ~1.5 GB, export a smaller review copy first:

  ```sh
  ffmpeg -i master.mov -vf "scale=-2:720" -c:v libx264 -crf 23 -c:a aac -b:a 160k review.mp4
  ```

  (The editor already ships FFmpeg through `imageio-ffmpeg`, so no separate
  install is needed.)

## How to upload

1. Drop the files into the conversation with the attachment/paperclip control.
   Files land in the session workspace, not in a chat transcript.
2. If you are working locally, put the files in a folder in the repository —
   for example `incoming/` — and ask the agent to look in it.
3. Animated GIFs and `.mov`/`.mp4` files each work as their own upload; do not
   put several videos in one archive.

Nothing you upload this way is committed: `incoming/`, `video_review/` and
`projects/` are git-ignored, so your footage never enters the repository
history. Delete a clip from the workspace when you are done reviewing it.

## What the agent does with the video

```sh
python tools/watch_video.py incoming/promo_v3.mp4
```

That writes `video_review/promo_v3_<timestamp>/` containing:

| File | What the agent reads from it |
| --- | --- |
| `sheet_1.jpg` | frames every few seconds, each stamped `MM:SS.mmm #n` |
| `sheet_2.jpg` | a second pass for clips over 2 minutes (`--extend`) |
| `peaks.jpg` | frames at the strongest scene changes (cuts and montage density) |
| `audio.png` | waveform with a time ruler — silence, level, clipping |
| `audio.json` | peak / RMS loudness in dBFS and the share of silence |
| `metadata.json` | duration, resolution, fps, codecs, bitrate, sampled timestamps |
| `README.txt` | the same guide, scoped to that clip |

Useful flags:

```sh
# more frames for a dense montage
python tools/watch_video.py incoming/v3.mp4 --frames 48 --cols 6 --scale 400

# every clip in a folder, one run
python tools/watch_video.py incoming/ --extend

# skip the scene-change pass on a single static shot
python tools/watch_video.py incoming/interview.mp4 --no-peaks
```

Then ask for what you actually want, referring to the stamps:

> "Frame #7 at 00:31.875 — the caption sits too low. #9 is the shot we agreed to
> cut. The waveform dips at 01:12, is the narration missing there?"

The agent can pull any timestamp at full resolution:

```sh
ffmpeg -ss 00:31.875 -i incoming/v3.mp4 -frames:v 1 still.png
```

## Honest limits

- This is a **sampling** method, not playback. Motion, timing, and whether a cut
  *feels* right are inferred from frames at the chosen spacing, the audio
  waveform, and scene-change scores. Something that lasts under a second between
  two sampled frames can be missed — raise `--frames` for fast-cut material.
- Loudness is measured from a 8 kHz mono decode; it is a reliable sanity check,
  not a broadcast loudness standard (LUFS). Use `ffmpeg -af ebur128` if you need
  EBU R128 numbers.
- Streaming links (YouTube, Drive, Vimeo) cannot be downloaded here. Export the
  file and upload it.
