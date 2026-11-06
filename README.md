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
- Clean cuts only; no dissolves or transition handles.
- Six-slot cooldown against repeating the same shot or overlapping source interval.
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
