# Storyroom Editor

A local Flask application that builds an SRT-driven video storyboard from uploaded narration, captions, a production script / EDL and MP4 source clips. It studies the uploaded videos, assembles clean-cut picture slots, and renders a continuous MP4 master for smooth Program-monitor playback.

## Features

- Upload-first workflow: SRT, voiceover, production script / EDL, and MP4s.
- Automatic media-use approval on upload; no manual source-review gate.
- Automatic source study and storyboard assembly.
- Automatic continuous-master rendering after assembly.
- **Render MP4** button remains available to start another export.
- Clips-only picture; no generated stills or blank slots.
- Clean cuts only; no dissolves or transition handles.
- Six-slot cooldown against repeating the same shot or overlapping source interval.
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
| `document_plan.py`, `edl.py`, `production.py`, `conform.py` | Production-script / EDL parsing and source binding |
| `approval.py`, `reviewed_library.py`, `import_status.py` | Automatic approval and upload-progress helpers |
| `cut_audit.py`, `validate_production_package.py` | Timing/source checks |
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
