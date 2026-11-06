# Storyroom Editor — repository notes

The application lives at the repository root. `app.py` is the Flask server; the engine
modules beside it do the planning, conformance and rendering work. Nothing in the app
depends on this file.

## Run it

```sh
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open `http://localhost:8003`. The server binds to `0.0.0.0:8003` and creates `projects/`
beside `app.py` on first use.

## What is deliberately not in Git

- `projects/` — your uploads, per-production JSON, caches and rendered masters. A checkout
  always starts empty.
- `.venv/`, `__pycache__/`, `*.pyc` — local environment and build output.
- No uploaded media, narration, SRT, project file or exported MP4 is ever committed.

## Branch

Work on this repository's feature branch (`arena/e293b2d2-storyroom-editor`); `main` holds
the earlier state. To publish:

```sh
git push -u origin arena/e293b2d2-storyroom-editor
gh pr create --fill
```

## Tests

`tools/runtime_sweep.py` is the acceptance test. It generates its own fixtures, so it needs
no uploaded material, and it drives the real HTTP API:

```sh
python tools/runtime_sweep.py --runtimes 60,300,600,1080,1800,3600 --work /tmp/sweep --sources 26
```

Each runtime must build, rebuild one block, and export to an exact duration with no
repetition or capacity errors. Long runtimes need `--sources 26` so there is enough
footage to fill them.
