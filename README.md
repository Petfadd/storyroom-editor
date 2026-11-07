# Storyroom Editor

A local Flask application that builds an SRT-driven video storyboard from uploaded narration, captions, a production script and MP4 source clips. It studies the uploaded videos, assembles clean-cut picture slots, and renders a continuous MP4 master for smooth Program-monitor playback.

## Features

- Upload-first workflow: SRT, voiceover, production script and MP4s, plus an optional
  per-SRT-block map that points each caption block at a footage source interval.
- Automatic media-use approval on upload; no manual source-review gate.
- Automatic source study and storyboard assembly.
- Automatic continuous-master rendering after assembly.
- **Render MP4** button remains available to start another export.
- Clips-only picture; no generated stills or blank slots.
- Clean cuts by default. Short dissolves are available per scene when a production
  allows them (`transition_policy`), and are validated: no dissolve off the last scene,
  no adjacent pairs, never longer than the incoming beat, and the export length never
  changes by a frame.
- Six-slot cooldown for overlapping source intervals, available perceptual hashes and
  explicitly declared `footage_group` labels. A group identifies a shot even when its
  intervals do not overlap. Matching filenames and descriptions alone do not prove two
  intervals are the same shot; they are not automatically grouped.
- Cut conformance: every picture beat is kept inside one decoded shot of its source, and cuts are eased into the narrator's breaths (see below).
- Original narration and SRT timing are preserved.
- Source audio is muted; added captions are off.
- Document/validator checks are separate from semantic identity or rights verification.

## Requirements

- Python 3.10+
- FFmpeg, supplied through `imageio-ffmpeg`
- The Python packages in `requirements.txt`

## Install and run

```sh
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8003`.

The server binds to `0.0.0.0:8003`. It creates a `projects/` directory beside `app.py` for local project data.

## Narration and footage flow

After a storyboard is built, `picture_flow.py` runs two passes and stores the result in
`project.json` and `reviews/Narration_pauses.json`:

1. **Picture flow.** Every beat is checked against the decoded shot index of its own
   source (`reviews/Full_source_analysis.json`). A beat whose requested interval merely
   *crosses* a shot change is slid to the nearest moment that holds the whole beat inside
   one continuous shot; a beat that returns to the take the previous beat was running is
   joined to it rather than jump-cutting in place.
2. **Narration flow.** Silences in the uploaded narration are decoded with `ffmpeg
   silencedetect`, and each picture change may move by at most 0.35 s to land inside a
   breath. Duration, SRT wording and the narration audio are never altered; a block that
   ends up straddling a cut is owned by exactly one scene so nothing is spoken twice.

Beats that cannot be placed cleanly are reported, never faked: nothing is trimmed,
stretched, looped or frozen. The report is available at
`GET /api/projects/<id>/flow-audit` (JSON, or CSV with `?download=1`) and is summarised in
the setup panel and the shot inspector. Any in-point you edit by hand overrides the
automatic conform for that beat. These checks describe continuity and cut placement only;
they say nothing about who or what a frame shows.

## One file or two

A production script may carry the narration SRT inside it, as a Markdown table with a
block number, a documentary `IN → OUT` clock and the exact wording. On upload the editor
recovers those blocks verbatim (`document_plan.srt_outline_blocks`): wording, order and
timing are copied, never rewritten or retimed, and the recovered SRT is also written to
`inputs/captions.srt`. The setup panel reports how many blocks were recovered and whether
anything was refused.

Recovery is structural and shows its work:

- columns are identified by content, so a beat table with source IN/OUT columns is never
  mistaken for narration;
- timing must be numbered 1..N in order and contiguous. A table with a gap or overlap is
  reported and **not** used — nothing is retimed to make it fit;
- an explicitly uploaded `.srt` always wins. Recovery only fills an empty SRT slot, and
  uploading a separate `.srt` later replaces the recovered one and clears its note.

A block map is still separate work: it needs a media filename and verified source
IN/OUT per block, which approximate sampled anchors in a guide cannot supply.

## One editor, any programme

Nothing in the timeline is sized to a particular case, and nothing is weighted per minute.
One code path, one set of rules, one output shape: a 1-minute piece and a 60-minute piece
are built, reviewed, rebuilt and rendered by exactly the same functions. Measured on this
machine, each runtime built to its exact length with no validation errors:

| Runtime | SRT blocks | Beats | Longer runtime | Build | Timeline |
|---|---|---|---|---|---|
| 1 min | 11 | 11 | 1 | 124 s | 60.00 s, 0 errors |
| 5 min | 49 | 49 | 2 | 130 s | 300.00 s, 0 errors |
| 10 min | 96 | 96 | 4 | 173 s | 600.00 s, 0 errors |
| 18 min | 176 | 176 | 7 | 224 s | 1080.00 s, 0 errors |
| 25 min | 249 | 249 | 10 | 240 s | 1500.00 s, 0 errors |
| 30 min | 291 | 291 | 12 | 300 s | 1800.00 s, 0 errors |
| 60 min | 593 | 593 | 24 | 454 s | 3600.00 s, 0 errors |

That table is the point of this section: the numbers differ, the behaviour does not. Each
build produced a contiguous timeline starting at frame 0 and ending on the last SRT block,
with every scene carrying the block reference the plan gave it. Longer runtimes are slower
in proportion to the picture, not by a different route.

- **Beats and blocks are independent.** A map may give one row per SRT block, or let one
  beat span many blocks (the build merges their narration into the beat). Both are read
  the same way.
- **Narration is optional.** A programme can be carried by its text alone: the picture is
  built, approved and exported on the SRT timing, and the master is written without an
  audio stream. A narration track is still checked against the picture when one is
  supplied, to 0.25 s.
- **Cue clocks are read as tools write them.** A one- or two-digit hour field and one to
  three millisecond digits are both accepted, so a 47-minute programme exported as
  `0:00:00,000` imports. Nonsense — out of order, empty, zero-length — is still refused.
- **A plan and a narration must agree on length.** If a block map covers only part of the
  SRT (a map built for a 10-minute cut dropped onto an 18-minute one), readiness says
  exactly how far it reaches, how many blocks are unplaced and which is the first, and
  export readiness refuses with the same numbers. It never leaves the tail of a programme
  silently unplanned.
- **Capacity is judged on the plan you are building.** A range rebuild or a scoped
  feasibility query measures only the beats it is placing, against the footage that
  exists. If a runtime needs more picture than you hold, the report says by how much and
  asks for more footage — it does not repeat a shot, loop it, or shorten the narration.

- **Contiguous beats from one file are one continuous take.** Splitting a long take across
  consecutive beats is the normal way to cut it, so a repeat is only reported when two
  source intervals genuinely overlap; frame rounding is not treated as reuse.
- **Profile.** A production starts from any of the profiles in
  `blueprints/style_profiles.json` (documentary, educational, promotional, travel,
  interview, investigative, courtroom, memorial). A profile supplies default movement,
  a motion scale and optional wording hints; it decides nothing about content. Add a
  profile with your own hints rather than editing the app for one programme.
- **Wording hints are dictionaries, not a taxonomy.** Subject words only pick a movement
  family and an emotion label; anything unmatched falls through to neutral defaults.

### Cuts and dissolves

A production starts **clean cuts only**. That is the default because a cut cannot go
wrong: it needs no handle, no extra source footage, and it never moves a frame.

A short dissolve is available when you ask for it. Set the production's
`transition_policy` to `allow_dissolves`, then set `transition` on an individual scene;
the handle defaults to four frames. It is refused, with a reason, when it cannot be
honest:

- from the last scene (there is nothing to dissolve into),
- next to another dissolve (adjacent pairs are unsupported — keep the next exit a cut),
- longer than the picture beat coming in,
- when the source file has no footage left to borrow the handle from.

Measured on an 11-beat programme: adding a dissolve changed the picture in the blend
region and left the master at exactly 1800 frames / 00:01:00.00. Switching back to
`clean_cuts_only` clears the dissolves on every scene and reports how many it cleared,
rather than leaving a scene claiming a transition the policy forbids.

Asking for a dissolve while the production is still clean-cuts-only is refused with an
explanation. It used to be silently rewritten to a cut, which looked like success and
left no way to tell why the dissolve never appeared.

### Export size

The master is not tied to one resolution. `format` picks the aspect and `size` the pixel
target, per production, changeable at any time. A "1080p" target names the short side, so
it means what people mean by it:

| Format | 720p | 1080p | 4K |
|---|---|---|---|
| Landscape 16:9 | 1280×720 | 1920×1080 | 3840×2160 |
| Portrait 9:16 | 720×1280 | 1080×1920 | 2160×3840 |
| Square 1:1 | 720×720 | 1080×1080 | 2160×2160 |
| Vertical 4:5 | 576×720 | 1080×1350 | 2160×2700 |
| Cinema 2.39:1 | 1720×720 | 2580×1080 | 5162×2160 |

`"1920x1080"` is also accepted as written. Measured on one 60-second, 11-beat programme:
exporting the **same cuts** at 1920×1080, 1080×1920, 720×720, 2580×1080 and 3840×2160
produced 1800 frames every time, with identical in/out points before and after each change
— the target moves pixels, never a cut, an approval or the duration.

### Rendering a long programme

A 30-minute, 291-beat programme at 1920×1080 on this machine (2 cores), with the per-stage
breakdown the renderer writes to `projects/<id>/render_timing.json`:

| Stage | Cold render | Warm re-render |
|---|---|---|
| Scene segments (291) | 218 s (83%) | 0.7 s (all reused) |
| Join | 1.2 s | 1.2 s |
| Mux (narration encoded, picture copied) | 42 s (16%) | 42 s |
| **Total** | **262 s** | **46 s** |

What made that possible:

- **Finished scene segments are cached** by a hash of everything that defines the picture
  (`engine.SEGMENT_VERSION`, source signature, in/out, frame count, motion plan, output
  settings). An edit re-encodes only the beats whose picture actually changed, and changing
  the export size re-encodes rather than mixing two sizes in one master.
- **The final mux stream-copies the picture** when captions are off, because the scene
  pass already encoded it to the final settings. Only the narration is encoded there. The
  exported frame count is verified against the timeline, so nothing is shortened silently.
- **The picture encoder is `ultrafast` at a high-quality target** (`-crf 16`) instead of
  `veryfast -crf 23`. Measured on a 1080p clip: 0.76 s vs 1.36 s per 6-second segment,
  **1.8× faster**, and closer to the source (SSIM 0.99998 vs 0.99964) at no larger a
  file — a faster preset spends its bits on fidelity instead of motion-search heuristics.
- **The join is skipped when there is nothing to blend.** Its only job is handle-backed
  dissolves; with clean cuts the segments already are the picture timeline, so joining
  them would write every frame twice for no change in output (42 s of pure copy at this
  length). The renderer's only transition policy is clean cuts, so that is the normal path.

Encoding the segments in parallel was measured and rejected: each libx264 encode already
saturates both cores, so two workers gained ~5% — not worth the complexity.

## Photographs and stills

A production is either **moving footage only** (the default) or **moving footage and
photographs**. Switch it in the setup panel; changing it resets scene approvals and needs a
rebuild. In the second mode:

- an uploaded JPG/PNG/WebP can cover a beat, laid out either fitted over a blurred copy of
  itself or cropped to fill the frame (`image_layout`);
- a block-map row marked `STILL` in its `status` column pulls **one frame** from an
  uploaded video at that row's stated source time, registers it as its own approved asset,
  and uses it for the beat.

A still never pretends to move: no imposed pan, zoom, trim or freeze, and the sample strip
in the cut inspector shows one picture labelled as a still rather than an invented
middle/last frame. A photograph deliberately held across consecutive beats counts as one
continuous shot, not as repeated footage; picking the same photograph up again later in the
cooldown window is still reported. The decoded frame check verifies that a still opens and
is not black — it still says nothing about who or what it shows.

## Review in bulk: approval and frame QC

- **Approve all ready beats** (`POST /api/projects/<id>/scenes/approve`) reviews every
  scene with the *same* function export readiness uses, and approves only the ones with
  no blocking item. Anything that would fail the export a moment later is left
  unapproved and returned with its reason. `"scope": "all"` is all-or-nothing: if any beat
  cannot be approved, nothing is approved and the first reason is reported. The
  attestation flag is required, and a per-scene `approval_record` is written.
  Editing a scene still resets only that scene's approval.
- **Frame QC sheet** (`GET /api/projects/<id>/frame-qc?sheet=N&per_sheet=20`) decodes one
  frame per beat into a contact sheet, each cell stamped with `#scene · SRT blocks ·
  HH:MM:SS.mmm`, the source filename, and — when the scene's in-point no longer matches
  the plan — `moved +1.250s` plus an amber bar across the cell. Undecodable beats are
  drawn as an empty box, listed in the response, never silently skipped. Sheets are
  cached per project state and renderer version.
  The sheet is a provenance record: it says where each frame came from and whether the
  cut moved. It makes no claim about who or what appears in the picture and approves
  nothing.

## Re-editing without losing review

A storyboard is not finished when it is first built; it is finished after a few rounds of
"this shot is wrong, that line needs a different picture". Both rounds are cheap now.

- **A rebuild keeps the review you already did.** Every rebuild compares each new beat
  with the saved one on the fields that decide what the picture is — SRT blocks, in/out
  frames, source, in-point, transition and its handles, narration, person, category,
  context-only flag, focus. A beat that matches keeps its approval, stamped
  `carried_from_rebuild`; a beat that changed comes back unapproved so it is looked at
  again. Nothing is approved that a person had not approved before. On a 120-beat
  programme, a full rebuild carried 119 approvals and asked for exactly one review.
- **Rebuild one range.** `POST /api/projects/<id>/storyboard` with
  `{"replace": true, "blocks": [40, 41, 42]}` rebuilds just those blocks. Only beats
  touching those blocks are replaced (a block can belong to several beats, and all of them
  are replaced together); every other beat keeps its id, timing, approval and notes. The
  rebuilt beats are shifted so the range starts exactly where it used to, and the request
  is **refused with a plain reason** — rather than saving a gap — if the new range would
  change the length of the surrounding timeline. The response carries `rebuild_report`
  (`scope`, `beats`, `approvals_carried`, `needs_review`, `rounding_frames`). In the editor
  this is the **Rebuild this block** button on each beat. Measured: **0.4 s per block** on
  a 240-beat, 30-minute programme — blocks 1, 57, 160 and 240 each rebuilt in place with
  239 of 240 approvals carried, the other beats unchanged, the timeline contiguous and the
  duration still exactly 1800.00 s; the rendered master measured 54000 frames / 30:00.00.
  Re-basing a range onto frame 0 can leave a **1-frame** rounding residue, which is
  absorbed at the end of the rebuilt range and reported; a larger change is refused.
- **A block map is required for a range rebuild**, because a script-style plan has no
  per-block rows to rebuild by.

## When nothing seems to happen

Auto-assembly used to be silent: inputs present, no plan, status `idle`, no message. It is
now impossible to miss. `automatic_readiness` is computed once and published — to the
project (`automatic_status.json`) and to every project read as `automatic_readiness` — and
answers "why is nothing happening" with one of:

| status | meaning | what the studio offers |
|---|---|---|
| `blocked` | an input is missing, the map cannot be read, or it names a file that is not uploaded | the missing piece, by name |
| `needs_plan` | media, SRT and voice are all present but no plan says which picture goes where | block-map template, upload a map, upload a script |
| `manual` | the timeline was hand-edited; auto-assembly will no longer overwrite it | rebuild deliberately |
| `paused` | auto-assemble is switched off for this project | — |
| `ready` | everything is present and a plan exists | assembly runs |
| `built` | picture beats exist and can be reviewed | — |

The notice names the plan it found and the inputs it still wants, in plain words, in the
studio. Assembly cannot fail without saying why.

## Planning: shot index, feasibility and the map template

Three aids, all read-only. None of them places a picture for you.

- **Shot index** (`GET /api/projects/<id>/shot-index`) lists every decoded continuous
  shot per source with its exact IN/OUT, length, frame count and whether it contains
  black. A shot is a span between detected changes; the list says nothing about who or
  what appears in it.
- **Feasibility** (`GET /api/projects/<id>/feasibility`) compares, per source, the
  seconds the plan demands against the file's own length and its decoded shots. It flags
  a source that is short, a beat longer than the longest single shot, or a plan that
  reuses footage to manufacture length. Before a file is decoded it uses only that
  file's reported duration and says so.
- **Candidates** (`GET /api/projects/<id>/candidates?length_s=&handle_s=`) offers only
  shots that can hold a picture of exactly that length, ignoring black spans. When
  nothing fits, it returns nothing: a gap is reported, never covered with an
  approximately-right shot.
- **Map template** (`GET /api/projects/<id>/block-map-template`, `.csv` by default)
  writes one row per SRT block with every clock, block reference and parent beat filled,
  and every picture column deliberately blank. A blank row carries a `gap_note`, so an
  honest half-finished map imports as a list of gaps rather than a silent choice. The
  shot table shows the matching `HH:MM:SS.mmm` values for the `source_in`/`source_out`
  columns.

The build also checks that check itself: if the plan asks one file for more picture than
it holds, export readiness reports it rather than rendering the same shot on repeat.

## Picture plan input tolerance

The block-map reader accepts the ways people actually write timecodes and columns:

- `00:00:05.400` and `00:00:05,400` are the same instant; an unquoted decimal comma no
  longer splits a CSV cell;
- the media column may be called `media_file`, `media_filename`, `media`, `file`,
  `source_file` or `source`, and `source_out_excl` still means the exclusive OUT used by
  older beat tables;
- a plan row without a `block` column is read as a beat table instead of a block map.

## Optional SRT block map

In **Production setup → SRT block map**, choose **Create block map** after importing
an SRT, or **Edit block map** to revise an existing map. Navigate one block at a time,
choose or type the original source filename, and enter source IN and exclusive OUT as
`HH:MM:SS.mmm`. Leave a source blank with a gap note if it is not ready. Optional picture
description and `footage_group` record editorial intent; use the same group only for
intervals you have established belong to the same shot. Other imported instructions are
preserved. Save stores the map and resets storyboard approvals; rebuild to apply it.
The editor does not verify content or automatically choose footage. If the SRT changes
while a draft is open, saving is refused so the new timing can be reviewed.


`srt_block_map.csv` is the only picture plan the editor accepts: one row per caption
block (`block,start,end,beat,media_file,source_in,source_out`), with an optional parent
`beat` number that carries the production script's treatment and end transition.
It must cover SRT blocks 1..N in order and the complete timeline. Upload it on the setup
panel (or through the alternative upload controls); rebuilding applies it. Removing it
returns the build to the production script's own beat table. An unsupplied map is not an
error, and a map never certifies what a shot shows: it supplies explicit source intervals
only.

## Validate a production package

```sh
python validate_production_package.py production_script.md captions.srt /path/to/original_named_sources
```

The third argument may instead be a JSON map of original filename to duration in seconds. This validator checks document structure, timing and source availability. It is **not** face recognition, copyright clearance, or visual proof of what a shot depicts.

## Repository layout

| Path | Purpose |
|---|---|
| `app.py` | Flask routes, uploads, project state and render endpoints |
| `automatic.py` | Automatic source study, storyboard assembly and continuous-master render |
| `engine.py` | FFmpeg render/concat job |
| `document_plan.py`, `edl.py`, `production.py`, `conform.py` | Production-script / block-map parsing and source binding |
| `approval.py`, `reviewed_library.py`, `import_status.py` | Automatic approval and upload-progress helpers |
| `cut_audit.py`, `validate_production_package.py` | Timing/source checks |
| `picture_flow.py` | Shot-continuity conform and narration-breath cut easing |
| `static/` | Editor and studio UI |
| `templates/` | Single-page editor shell |
| `blueprints/` | Style profiles, project schema and editorial rules |
| `projects/.gitkeep` | Empty project-data directory placeholder |

## Media policy

No uploaded MP4s, voiceovers, SRT files, generated renders, thumbnails or case-specific projects are included in this repository. `.gitignore` excludes all user media and generated output. Add your own source files through the editor after cloning.

One-off, case-specific helpers that embed a project ID, local absolute paths, or human frame-review notes are intentionally excluded. The reusable runtime engine, automatic workflow, UI, blueprints and validator are included.

## License

No `LICENSE` file is included. If this is a public repository and you want others to reuse the code, add a license before publishing. Without one, standard copyright applies.

## Honest scope

The engine can decode frames and follow explicit machine-readable source intervals. It does not independently certify people, exact exhibits, copyright status, or editorial truth. `YES` flags and automatic approval do not equal visual identity verification.

## Local media understanding (experimental)

Production setup → **Local audio & video understanding** opens an independent,
per-file report. It does not change the SRT, make editorial approvals, or affect
storyboard construction. Use **Analyse locally** for narration or an uploaded video.
The JSON report is also available at `GET /api/projects/<id>/understanding/<target>`
where target is `narration` or a video asset ID. The shot inspector also shows source
observations that overlap the saved beat, mapped to its timeline times; these do not
change the edit. Replacing a source marks its report stale. Processing is serialized to reduce CPU and memory pressure.

Setup on the computer running Storyroom:

1. Install the optional libraries into the app's Python environment:
   `pip install -r requirements-local-ai.txt`. FFmpeg is supplied by imageio-ffmpeg.
2. Download a CTranslate2 Faster-Whisper model to a local directory using a model
   provider's download tooling, and set `STORYROOM_WHISPER_MODEL_DIR` to that directory
   before starting Storyroom. For example, install a converted `Systran/faster-whisper-small`
   model (the directory must contain `model.bin`, `config.json` and `tokenizer.json`).
   Storyroom checks for these files to prevent Faster-Whisper's tokenizer download
   fallback, loads only local files and uses CPU int8; it never downloads a model.
3. Install and run Ollama locally, and separately download `qwen3-vl:2b` using
   `ollama pull qwen3-vl:2b`. Storyroom sends sampled frames only to
   `http://127.0.0.1:11434`; it does not use a remote endpoint or web inference.
   `STORYROOM_OLLAMA_MODEL` can select another model already installed locally.
4. To queue analysis on future uploads, start Storyroom with
   `STORYROOM_UNDERSTANDING_AUTO=1`. By default use the manual control so additional
   model workloads do not interrupt existing editing. Analysis can take much longer
   than the media duration on a small CPU; `STORYROOM_VISION_MAX_FRAMES` controls the
   number of sampled frames (default 60, maximum 300).

Model downloads require an internet connection once, but analysis is local and requires
no paid inference. Ollama and the Faster-Whisper models need extra disk and RAM; the
Qwen 2B quantized weights alone are approximately 1.9 GB, before runtime memory. This
sandbox has only 3.8 GiB RAM and two CPU cores, so running this model here may be slow
or exceed memory. Your own computer's hardware was not assessed. AMD acceleration is
not configured or verified; the transcription path is explicitly CPU-only.

**Current limits:** this is not yet complete audio/video understanding. The visual
pass samples still frames across the video, up to the sample cap, so it can miss actions
and text between frames. Descriptions and timestamps can be wrong; names and identities
are not established. Librosa reports RMS, spectral centroid and coarse low-energy
windows and estimated low-energy intervals, not guaranteed silence. Optional YAMNet
adds estimated music and sound-event labels in three-second windows; scores are not
proof, and short or overlapping events may be missed.
Optional pyannote diarization requires a separately installed local pipeline and has not
been tested here with real models. Its speaker labels are anonymous, per-file estimates,
not verified identities. The model may require license acceptance and authentication
for the one-time download; inference itself is local. Missing
models/dependencies and failures appear per stage, and partial results are labelled as
such. Full transcription, visual inference and diarization need real-model testing before
this capability should be treated as complete. The optional sound classifier was tested
on a handful of sample files, not benchmarked for real-world accuracy.

### Windows 11: CPU-first setup

These commands run on **your computer**, not the remote preview. In PowerShell, from
this repository's folder, with Python 3.11 installed:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -r requirements-local-ai.txt
.\.venv\Scripts\hf.exe download Systran/faster-whisper-small --local-dir "$HOME\storyroom-models\faster-whisper-small"
$env:STORYROOM_WHISPER_MODEL_DIR = "$HOME\storyroom-models\faster-whisper-small"
```

Install the official Windows Ollama application separately, start it and run
`ollama pull qwen3-vl:2b`. Then start Storyroom in the **same PowerShell window**:

```powershell
$env:STORYROOM_UNDERSTANDING_AUTO = "1"  # optional: analyse future uploads automatically
.\.venv\Scripts\python.exe app.py
```

Model downloads use the internet but analysis does not send media to a hosted service.
The automatic option queues each upload, and on CPU may compete with the editor for
resources; leave it unset to choose when to analyse. Start with short test clips.

Optional speaker diarization (experimental): install
`.\.venv\Scripts\python.exe -m pip install -r requirements-local-diarization.txt`.
After accepting the community-1 model's terms with its provider and downloading the
**complete** `pyannote/speaker-diarization-community-1` repository, set
`$env:STORYROOM_PYANNOTE_MODEL_DIR` to that local directory before starting Storyroom.
The diarization subprocess forces Hugging Face offline mode and disables pyannote
telemetry. No credentials are entered into Storyroom. This path is not yet tested here
with the actual pipeline, especially on Windows, and may require additional dependency
setup. `SPEAKER_00` labels are anonymous and cannot be compared across files.

Faster-Whisper and diarization run on CPU. Ollama controls its own device selection;
AMD GPU support depends on the exact GPU, driver and Ollama version. Consult
[Ollama's hardware support](https://docs.ollama.com/gpu) and use `ollama ps` while a
model is loaded to see whether it is using GPU. No AMD acceleration has been verified
in this environment. On an unsupported GPU, Ollama can fall back to CPU.

### Optional local sound-event tagging

Set `STORYROOM_YAMNET_MODEL` before starting Storyroom to a local YAMNet ONNX graph
with a float32 waveform input shaped `[1, 48000]` (three seconds at 16 kHz) and a
`scores` output with 521 AudioSet classes. For example in PowerShell:

```powershell
$env:STORYROOM_YAMNET_MODEL = "C:\path\to\yamnet_3s.onnx"
```

The model file is not bundled or downloaded by Storyroom. Only use a model whose
license permits your use. Output is an estimated list of sounds and music for each
three-second window; class scores can be wrong, are not verified probabilities, and do
not identify a sound's source. Short tails are not classified. The compatibility test
used the `yamnet_3s.onnx` waveform graph from
[Choise-ieee/yamnet_onnx_cpp_audio_speech_classification](https://github.com/Choise-ieee/yamnet_onnx_cpp_audio_speech_classification);
that repository does not state a license, so it is **not redistributed here**. The
included class labels and their license are documented in `data/YAMNET-NOTICE.md`.
