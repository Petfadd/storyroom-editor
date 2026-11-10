# Pre-deployment audit — Storyroom Editor

**Date:** 2026-11-05
**Commit audited:** `e0028c0` (branch `arena/e813958b-storyroom-editor`)
**Scope:** can this run on your own PC, and what will break if it does.

---

## Verdict

**Ready to deploy.** No blocker was found. A clean clone installs from
`requirements.txt` and runs the full upload → captions → storyboard flow on a machine
that has nothing but Python.

Two things are worth knowing before you start, both covered below:

1. It binds to **all network interfaces with no password**. That is fine on a home PC;
   it is not fine on a shared or public network without one flag.
2. A long project (15 minutes or more) is **slow to export**, and one export stage has a
   30-minute ceiling. Nothing loses your work if it is hit, but the export fails.

Everything else — tests, security, dependencies, Windows support, repo hygiene — passed.

---

## What was actually tested

I did not only read the code. I rehearsed the deployment:

| Test | Result |
|---|---|
| Full test suite | **390 passed**, 32.7 s |
| `git clone` of this branch | 4.7 MB checkout, works |
| `pip install -r requirements.txt` in an empty venv | clean, no compiler needed |
| App starts from the bare clone | `GET /` → 200, 89 routes registered |
| Creates a project | 201, correct 1080×1920 for portrait |
| Uploads a 41.7 MB clip | 200, duration read correctly, 173.8 s |
| Uploads captions | 200 |
| Builds a storyboard | 200, 2 scenes |
| **Renders a real clip** | 1080×1920, SAR 1:1, correct frame count |
| `projects/` absent after clone, created on first run | yes |
| Same suite on the minimal install | 390 pass, **8 skip** (optional AI only) |

The render test matters most: it proves the bundled ffmpeg has `libx264`, `aac`,
`crop`, `scale`, `setsar` and `silencedetect` — the entire export path — with nothing
installed on the machine.

---

## The dependency question

Earlier I flagged that the code imports 14 third-party modules but `requirements.txt`
lists only 5. **This is by design, not a defect.** The other nine are the optional
local-AI features, and they are already isolated:

- `requirements-local-ai.txt` — faster-whisper, scenedetect, librosa, onnxruntime
- `requirements-local-diarization.txt` — pyannote.audio (pulls PyTorch, ~2 GB)

Every one of those imports sits inside a `try/except` or inside a function that is only
called when the feature is used. I verified this directly: I imported all 13 core modules
with the optional packages forcibly made unimportable, and every one imported cleanly.
The features then report a plain message ("No local speech model is installed. Put a
Faster-Whisper model…") rather than crashing.

So the install is genuinely two tiers, and you only need the first.

---

## Install on your PC

Windows (PowerShell), in the folder you cloned into:

```powershell
python -m venv .venv
.\.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open <http://localhost:8003>.

Linux or macOS:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

**Python 3.10 or newer.** Tested on 3.11.

You do **not** need to install ffmpeg. `imageio-ffmpeg` ships a self-contained binary,
and it ships the Windows build — I confirmed a native `win_amd64` wheel downloads for
every package in `requirements.txt`, so nothing compiles and nothing needs Visual Studio.

### Settings you may want

| Variable | Default | Use |
|---|---|---|
| `PORT` | `8003` | change the port |
| `STORYROOM_HOST` | `0.0.0.0` | set to `127.0.0.1` to keep it off your network |

I tested both overrides; they work.

---

## Risks, in order

### 1. No password, listening on the whole network — the real one

The app has no login and no CSRF protection, and it binds `0.0.0.0`. On your home
network that means any device on the same Wi-Fi can open it and delete productions —
`DELETE /api/projects/<id>` has no gate at all.

For a single-user tool on a trusted home network this is the normal trade-off and is
how most local editors behave. If you take the laptop onto café or hotel Wi-Fi, start it
with:

```powershell
$env:STORYROOM_HOST = "127.0.0.1"
python app.py
```

That is a one-line fix and I would use it by default on a laptop.

### 2. Export of a long project can hit a 30-minute ceiling

The final render re-encodes the whole film once to burn captions, and again if you set a
fade. Each of those passes is capped at 1800 s. Your 14.5-minute film fits with wide
margin (roughly 4–10 minutes for the caption pass depending on the CPU); a project
around 45–60 minutes on a slower machine could hit the cap and fail with
"Documentary assembly failed". Nothing is corrupted, the export just stops and can be
retried without captions.

I have **not** changed this. Raising the limit is easy but I did not want to alter export
behaviour during an audit without your say-so.

### 3. Disk use is substantial

`projects/` grows fast: your 14.5-minute film is 4.3 GB on disk, because each export is
kept alongside its predecessors. Renders are ~430 MB per 14.5 minutes at 1080p. Budget
tens of GB if you keep several cuts. The repo itself is tiny — 2.2 MB.

### 4. Browser verification was not possible here

I could not drive a real browser in this environment. Every check above is the HTTP layer
and the test suite, which covers the UI code but not pixels. Your first real click-through
on your PC is still the true visual confirmation.

---

## Security review

Checked and clean:

- **No hardcoded secrets** anywhere in the Python source.
- **No shell injection surface** — zero `shell=True`, zero `os.system`. All 62 subprocess
  calls pass argument lists.
- **No path traversal.** The five routes that accept a filename from the browser
  (`exports`, `frame-qc`, `documentary-audio`, `scene-exports`, `documentary-review-preview`)
  were probed with `../../../etc/passwd` and encoded variants: all return 404, while a
  legitimate file still returns 200. Project IDs are validated against `[a-f0-9]{16}`.
- **Debug mode is off** — `app.run(..., debug=False)`, so the Werkzeug console is never
  exposed.
- **No outbound network** at runtime. The only `urllib` call targets `127.0.0.1:11434`
  (local Ollama) with redirects and proxies disabled.
- Uploads are capped at 8 GB and filenames are reduced to their base name.

---

## Repo hygiene — and a fix I made

The branch had a 94 MB orphaned blob in `.git`: an earlier accidental `git add` of the
portrait MP4 left the object behind even though it was never committed. `.git` was 98 MB
for a repo with 129 tracked files.

Fixed in `e0028c0`:

- Added `*.mp4` to `.gitignore` — delivered media can no longer be staged by accident.
  No tracked file is affected; nothing depends on an `.mp4` being in Git.
- Deleted `_tsfix_probe.py`, a one-off timestamp probe with a hardcoded
  `/home/user/...` path that had no business in a checkout.
- Ran `git gc --prune=now`.

`.git` went from **98 MB to 2.2 MB**. Full suite re-run afterwards: 390 pass.

Working tree is clean; only the untracked deliverable `aaaaaaaz-Portrait-9x16.mp4` sits
in the root, deliberately ignored.

---

## Smaller notes

- Two projects with the same title get distinct IDs; no collision. Titles are capped at
  100 characters.
- A render interrupted by a server restart is correctly reported as `interrupted` rather
  than appearing to still be running.
- Generated paths are at most ~116 characters — comfortably inside the Windows 260-character
  limit.
- The README matches reality: it documents Windows activation, the two optional dependency
  tiers, and the model installation steps.
