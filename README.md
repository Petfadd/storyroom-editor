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
