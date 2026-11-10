# Rebuild Runbook — rebuilding a lost production from scratch

Runtime data lives in `projects/`, which is **git-ignored on purpose** (uploads and
renders are counted in gigabytes). Anything in `projects/` is therefore *not* part of
the repository snapshot. If the workspace is rebuilt, every project, asset, SRT and
approval is gone, and only tracked source code comes back.

This file is tracked. It records, step by step, how to get back to a built timeline
after such a loss, so the work does not have to be rediscovered.

---

## 0. First, get the server up

`.venv` is also git-ignored, so it disappears too.

```bash
cd /home/user/storyroom-editor
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements.txt
PORT=8004 .venv/bin/python app.py          # bind 0.0.0.0; default port is 8003
```

`app.py` defaults to port **8003**. Set `PORT` explicitly, and run it as a managed
background process — a plain `nohup … &` from a one-shot shell is killed when the
call returns.

Confirm: `curl -s localhost:8004/api/projects` returns `{"profiles":{…},"projects":[]}`.

### 0a. Restore local sound-bite speech (optional, needed for spoken captions)

The ASR runtime lives in `.venv` and `.cache/`, so it is lost with the rebuild too.
Without both halves, the Saved sound bites panel shows *"The local faster-whisper
package is not installed. No download was attempted."*

```bash
.venv/bin/python -m pip install -r requirements-local-ai.txt     # faster-whisper
.venv/bin/python tools/install_local_whisper.py                 # pinned model, ~484 MB
```

The installer pulls from `api.github.com` (the mirror repo) and verifies every
SHA-256. `huggingface.co` and `raw.githubusercontent.com` are **not** reachable in
this sandbox, so `faster-whisper`'s own Hugging Face download will not work — use the
installer. Verify with:

```bash
.venv/bin/python -c "import bite_speech;print(bite_speech.available())"   # (True, '')
curl -s localhost:8004/api/projects/<pid>/documentary-bite-speech          # available: true
```

`available: true` is necessary but not sufficient — it does not transcribe anything.
Captions are saved per bite, on request, and are review-required:

```bash
# revision must equal the current plan revision (GET documentary-bite-speech -> revision)
curl -X POST localhost:8004/api/projects/<pid>/documentary-bites/<bite_id>/speech \
     -H 'Content-Type: application/json' -d '{"revision":1}'
```

Expect roughly 8–20 s per bite on CPU for a 27–33 s bite. `captions: 0` in the status
payload is the project's opt-in caption **flag**, not a segment count; the real signal
is `bites[].has_speech`.

---

## 1. Create the production

`POST /api/projects` with `edit_mode: "documentary"` (and the profile/format/size).
A project needs `inputs/`, `sources/`, `assets/`, `renders/`, `reviews/` — created
automatically. Note the new `id`; it is **not** the old one.

Then, in the UI: import the footage into the media library, upload the original
narration and the SRT. The app needs all three before it will plan anything
(`inputs_ready` requires `srt`, `production_script` and `assets`).

## 2. Load the production script

The script is what drives beat planning. Beats are read from it, so the exact text
matters: the storyboard rejects a mismatch via `script_sha256`, and every recorded
range below is keyed to that text.

## 3. Beat picture plan

Open **Review the Beat picture plan →**. For each beat that cannot use its
scripted range, pick the replacement source, then build.

The build call is:

```
POST /api/projects/<pid>/documentary-beat-storyboard
{ "confirmed": true, "project_version": <project.updated>,
  "plan_revision": <plan revision>, "script_sha256": "<sha256 of script text>",
  "scripted_review_beat": "007", "scripted_review_source": "S06.mp4" }
```

`fit()` trims/extends picture only inside the script-named file. It clamps an OUT
past the end of the file, snaps it down to the last real frame, and extends forward
from the scripted IN (or backward from the scripted OUT). It returns a blocking
issue rather than inventing a substitute — so a refusal is a real finding, not a bug.

### Verification targets

The last successful build produced these. They are a checklist, not inputs — the
plan is re-derived mechanically from the media.

| | |
|---|---|
| Scenes | 122 |
| Cue shots | 120 |
| Sound bites | 5 |
| Timeline length | 825.744 s (13:45.744) |
| Sub-frame gaps | 1, at 630.256 s |

Fitted ranges the builder chose:

| Beat | Source | IN → OUT (s) |
|---|---|---|
| 001 | S05 | 42.000 – 75.600 |
| 002 | S01 | 28.000 – 66.500 |
| 004 | S05 | 66.692 – 141.758 |
| 005 | S01 | 31.000 – 63.500 |
| 007 | S06 | 0 – 79.300 |
| 008 | S05 | 71.233 – 135.000 |
| 009 | S05 | 73.000 – 124.267 |
| 010 | S04 | 60.000 – 106.533 |
| 013 | S06 | 15.000 – 69.000 |
| 015 | S01 | 1.271 – 71.738 |
| 016 | S05 | 42.000 – 127.256 |

Sound bites: 003 (pause 72.1 s, 49.0–81.5) · 006 (179.666 s, 0–27) ·
011 (420.533 s, 99–165) · 012 (420.533 s, 47–73) · 014 (474.533 s, 27–71).

Then `POST /api/projects/<pid>/documentary-audio` → should report `done` at the
planned length.

## 4. Frame rates and source limits

Measure these from the container; do not assume.

- **S05 is genuinely 59.94 fps**; S01–S04 and S06 are 29.97. Output is `30000/1001`.
  S05 slots carry `frame_conversion: 'halve'` and a halved frame count of
  `(raw + 1) // 2`. Do not measure rate as `stream.frames / duration` — audio
  padding gives a false mismatch (59.881 vs 59.94). Use `average_rate` with a
  1/500 tolerance.
- Frame counts: S01 2150 · S02 2807 · S03 24928 · S04 3067 · S05 8497 · S06 4756.
- Limits (ms): S01 71738 · S02 93660 · S03 831764 · S04 158691 · S05 141758 ·
  S06 102335.

A sustained `source ended after N frames` error almost always means an OUT derived
from a rounded millisecond value rather than from `stream.frames`.

---

## 5. What a rebuild does *not* restore

Approvals, review decisions and reuse-rights records are stored per project and are
gone. The plan comes back as a **review-only draft**: picture and timing return,
but every shot needs its approvals re-recorded before export unblocks. Expect
`documentary-export-readiness` to report `blocked` with pending approvals and
source-capacity notes until that review is done.

Export is still not a rendered file — it produces the plan; the MP4 render page
remains a separate, asynchronous step.

---

## Standing constraints that still apply after a rebuild

- Only script-listed files and intervals, approved gaps and frame exceptions. No
  filename assumptions — a filename is not proof of content.
- Report missing files, OUT past source, unequal spans, overlaps or gaps; stop and
  ask rather than substituting.
- ≥90 % genuine motion, ≤10 % eligible stills. No extracted frames, freeze frames
  or faked motion. Video plays 1×; no looping, slow-motion, freeze or reverse
  outside approved repeats.
- Sound-bite captions come from the actual spoken audio (local ASR,
  review-required), never invented, and use the same font and style as narration
  captions.
- Narration and the uploaded SRT are never edited. Source audio stays muted unless
  the script says otherwise; timeline mutes are preview-only.
- No fabricated identity, content, provenance or rights.
- Fades are ignored in preview and omitted from export.

---

## 7. Background music and on-screen text

Two rules that changed after a real review pass, recorded here so they are not
accidentally reverted:

**Music under speech.** Narration runs almost continuously, so "stop the music
whenever there is speech" would silence the bed for the whole film. The bed
therefore treats the two kinds of speech differently:

| While this plays | The bed |
| --- | --- |
| Narration | dips to 35 % (`documentary_music._speech_gain`, `narration_ratio`) |
| A footage sound bite | **stops** in `speech_mode: "mute"` (the default), or dips in `"duck"` |
| Nothing | full saved level |

`speech_mode` is a saved project setting (`PATCH /documentary-music`), applied to
the preview and the export from the same value. The preview applies it in the
browser per animation frame (`docSyncMusic`); the export bakes the identical
gain curve into the bed. Verify a build with:

```bash
.venv/bin/python -c "
import app; from documentary import load_plan,annotate_plan,build_timeline
from documentary_music import build_bed,music_path
p=app.load('<pid>'); r=app.folder('<pid>')
t=build_timeline(p,annotate_plan(p,r,load_plan(r)))
print(build_bed(p,music_path(r),r/'renders'/'bed.wav',t['duration_s'],t['segments'])['mute_windows'])"
```

Measured expectation: RMS ≈ 0 inside a mute window, clearly non-zero just before
it (99.5 vs 0 on the reference project).

**The preview mix carries no music.** `AUDIO_PROCESSING_VERSION` is
`narration-loudness-match-v2`; v1 baked the bed into the preview `.m4a`, which
then played *on top of* the live music layer and doubled the bed. The preview mix
is narration plus bites only, and the bed is a single live layer. The final export
still mixes the bed, because `documentary_render_job` passes `music=True`.

**On-screen text is opt-in.** `burn_captions: false` on the project (Render MP4
page checkbox, `PATCH /api/projects/<pid>`) makes `documentary_render_plan.plan()`
return no caption cues, so `documentary_final_renderer` emits no `drawtext` and
the preview shows none. The script, the SRT, the saved bite transcripts and every
cut are untouched — only the burned-in text stops.

**Picture stall tolerance.** `docDirectSwitch` waits for metadata and seeks with a
6 s timeout that *resolves* rather than rejects, and a refused `play()` no longer
calls `pauseEditor()`. A slow or hung video can drop a frame, never stop the
narration clock. The only remaining stop in that path is genuinely missing
footage.

### Cut Action: fades and dissolves

`Cut Action: FADE IN FROM BLACK` in the script used to block export, because the
export route demanded `omit_fade: true`. That is gone. A fade is a **brightness
ramp on frames that already exist**, so it cannot move a cut, a frame count or the
reviewed timing — verified end-to-end: 90 in, 90 out, first frame 8/255, mid-ramp
126/255, full frame 255/255.

- `documentary_fades {enabled, in_s, out_s}` is a project setting (0–5 s each,
  `PATCH /api/projects/<pid>`), surfaced as a checkbox on the Render MP4 page.
- `documentary_render_plan.plan()` reports `fade_in_s` / `fade_out_s`;
  `documentary_final_renderer` applies them to the *joined* picture in one pass,
  then re-checks frame count and rate before continuing.
- The preview deliberately plays **without** the fade.
- `transition_policy` can now be `allow_dissolves`; dissolves blend pictures at
  original frame boundaries (`engine._xfade_command`) without overlapping
  narration. Turning it back to `clean_cuts_only` clears existing dissolves and
  reports how many (`dissolves_cleared`).

### On-screen text, one switch

`burn_captions:false` removes **all** monitor text: the scripted caption from the
schedule (`#doc-script-overlay`), the bite transcript, and the CC cue. It used to
gate only the bite path — the schedule caption was written from a different
variable (`caption`) and stayed on screen. Never reintroduce an ungated writer to
those three elements.

### Music during a bite (front end)

`docSpeechMode` reads **both** the frame-accurate schedule and the planning
segments, and a sound bite in either one silences the bed. Verified against the
real 5-bite schedule at 88.4 s, 225 s, 500 s, 560 s, 640 s (all `volume 0`) versus
narration at 60 s / 110 s / 600 s / 800 s (`0.355 × level`).
