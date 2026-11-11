# Running Storyroom on your own PC

Everything below is verified against `main` at commit `c6634dd`.

You need **Python 3.10+** and nothing else. FFmpeg is bundled with the app — do not
install it separately.

---

## 1. Get the code

Open PowerShell and pick a folder you want the app in, for example `C:\Storyroom`:

```powershell
cd C:\
git clone https://github.com/Petfadd/storyroom-editor.git Storyroom
cd Storyroom
```

If you do not have Git, install it from <https://git-scm.com/download/win>, or download
the ZIP from <https://github.com/Petfadd/storyroom-editor> (green **Code** button →
**Download ZIP**) and unzip it.

Check you got the right version:

```powershell
git log --oneline -1
```

It should say `c6634dd Merge pull request #7`.

---

## 2. Create a private Python environment

This keeps the app's libraries away from the rest of your machine. Do it once:

```powershell
python -m venv .venv
```

If `python` is not recognised, install Python from <https://www.python.org/downloads/>
and **tick "Add python.exe to PATH"** during setup.

---

## 3. Install the libraries

```powershell
.\.venv\Scripts\activate
pip install -r requirements.txt
```

Takes a minute or two. The `(.venv)` prefix in your prompt means it worked.

You do not need the optional AI packages. Those are only for local speech, diarization
and sound analysis, and are installed separately later if you ever want them.

---

## 3a. Optional: turn on real AI upscaling

Without this the app still works, and it will tell you so: the export page says
*"Real-ESRGAN is not installed, so pictures are resized with Lanczos."*

With it, stills are enlarged by a neural network rather than by interpolation. It is
free, runs on your own machine, and has no account or API key.

1. Download
   <https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-windows.zip>
2. Unzip it and put the **whole folder** here, so the file sits at
   `C:\Storyroom\tools\realesrgan\realesrgan-ncnn-vulkan.exe`
3. Restart `python app.py`

Check it took: <http://localhost:8003/api/picture-tools> should report
`"available": true`.

**It needs a Vulkan-capable graphics card** — NVIDIA from 2012, AMD from 2012, or Intel
HD 4000 and newer. Almost every machine that can play a 4K video qualifies. If yours
does not, nothing breaks: the app falls back to Lanczos and says so in the panel.

**Licence.** `realesrgan-ncnn-vulkan` is MIT licensed, which places no conditions on
your own work. Note that this is *not* Video2X, whose AGPL-3.0 licence would attach to
the app itself if it were bundled. Keeping AI upscaling in a separate executable is what
keeps your project free of that obligation.

---

## 4. Start it

```powershell
python app.py
```

Leave that window open — it is the server. Open your browser at:

**<http://localhost:8003>**

To stop it, press `Ctrl+C` in the PowerShell window, or just close the window.

---

## Day to day

Once set up, starting the app is only two lines:

```powershell
cd C:\Storyroom
.\.venv\Scripts\activate
python app.py
```

Close the window when you are done.

---

## Where your work lives

Everything you create goes into a `projects` folder **inside the app folder**:

```
C:\Storyroom\projects\<project-id>\
```

That folder holds your uploads, storyboards and exported MP4s. It is not in Git, so
`git pull` will never touch it.

**To back up your work**, copy the `projects` folder. That is the whole backup.

Be aware of size: a 14-minute film occupies roughly **4 GB** once you have exported it,
because each export is kept alongside the previous ones. Keep an eye on disk space.

---

## Getting updates later

```powershell
cd C:\Storyroom
git pull
```

Your `projects` folder is ignored by Git and stays untouched.

---

## One setting worth knowing

By default the app is reachable by any device on your network. That is convenient on
your home Wi-Fi and it is how a local editor normally behaves — but it has no password.

If you take the laptop somewhere with shared Wi-Fi (a café, a hotel, an office), start it
with this instead of plain `python app.py`:

```powershell
$env:STORYROOM_HOST = "127.0.0.1"
python app.py
```

That makes it reachable **only from that computer**. To make it permanent, set it before
each session, or ask me and I will add it to the startup file.

Other optional settings:

| Setting | Default | What it does |
| --- | --- | --- |
| `PORT` | `8003` | Use a different port, e.g. `$env:PORT = "8080"` |
| `STORYROOM_HOST` | `0.0.0.0` | `127.0.0.1` keeps it private to this PC |

---

## If something goes wrong

**`python` is not recognised**
Python is not on your PATH. Reinstall Python and tick *Add python.exe to PATH*, or use
`py -3` in place of `python`.

**`pip install` fails building a package**
It should never happen — every dependency has a pre-built Windows wheel, so nothing
compiles. If it does, you are probably on a Python version newer than 3.13; install
Python 3.11 or 3.12 instead.

**`Port 8003 is in use`**
Something else is using that port, often a previous copy of this app. Close it, or run
`$env:PORT = "8004"` first.

**The page will not load**
Check the PowerShell window — if it printed an error, that is the reason. If the window
is empty and still running, the address is wrong: it is `http://localhost:8003`, not
`https`.

**A long export fails with "Documentary assembly failed"**
The caption pass has a 30-minute ceiling. Projects up to about 15 minutes export with
plenty of room; very long ones on a slow machine can hit the limit. Retry with captions
turned off, or tell me and I will raise the ceiling.

---

## First run, what to expect

1. **New production** — give it a title, choose **Landscape 16:9** or **Portrait 9:16**.
2. Upload your **captions (.srt)**, **voiceover**, **production script** and **video clips**.
3. Build the storyboard — the app studies your footage and matches it to the captions.
4. **Render MP4** — choose the resolution first, then render. The export appears in the
   player, with a download control in its top-right corner.

### Choosing a resolution

The export page offers three cards:

| Choice | Landscape | Portrait |
| --- | --- | --- |
| 1080p Full HD | 1920 × 1080 | 1080 × 1920 |
| 2K QHD | 2560 × 1440 | 1440 × 2560 |
| 4K UHD | 3840 × 2160 | 2160 × 3840 |

Each card tells you what that choice costs your own footage. A 1080p film at 1080p says
**No upscale needed** and is not processed at all. The same film at 4K says
**Upscale ×2**.

The factor always comes from the source, never from the canvas. A 640 × 360 clip needs
×4 even at 1080p, and the card says so before you render rather than after.

Also on that page:

- **Framing** — *fit* (black bars), *crop* (fills the frame, trims edges) or *blur*
  (blurred copy behind). None of them stretch or distort.
- **Quality** — High, Standard or Compact. These are CRF 18/20/23 with 256/192/160k
  audio. Duration, frame rate and audio sync never change.
- **Checks** — the export verdict, now beside the render button rather than in the
  shot inspector.

Portrait projects render with square pixels, ready for TikTok and Reels. Landscape is
ready for YouTube.
