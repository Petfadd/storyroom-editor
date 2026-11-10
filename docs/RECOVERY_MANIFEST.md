# Recovery Manifest — Maliyah Bass production

Written after the workspace rebuild that wiped `projects/`. Everything here was
recovered from files that survived, or is recorded so the next rebuild costs
minutes instead of hours.

**Status: rebuildable.** The source naming scheme is documented, so the lost
project can be reconstructed. Media must be re-acquired (it was never in git).

---

## 1. What survived

| Asset | Location | Notes |
|---|---|---|
| Production guide + 120-block SRT map | `/tmp/arena-coding-uploads/Maliyah_Bass_Production_Guide_and_SRT_Map.md` | **Not snapshotted** (in `/tmp`). Copy it into `docs/` before it is lost. |
| Two workflow documents | same folder | Process rules, script block format. |
| Rebuilt SRT (120 cues) | `docs/rebuilt/maliyah_bass_srt_rebuilt_from_guide.srt` | Tracked. Verified: parses as 120 blocks. |
| Rebuild runbook | `docs/REBUILD_RUNBOOK.md` | Tracked. |

Lost and **not recoverable from disk**: the six source videos, the narration
audio, the delivered SRT file, the production script text, and all per-project
approval records.

---

## 2. Source register — what to re-download

The guide's section 3 maps stable `V` IDs to planned local filenames. The six
files the last build used map as follows.

| Local file | Publisher | YouTube | Resolves to |
|---|---|---|---|
| `S01.mp4` | KHOU 11 | `https://www.youtube.com/watch?v=nnqkO6Nh56I` | V25 |
| `S02.mp4` | KHOU 11 | `https://www.youtube.com/watch?v=64go-yx2Byc` | V15 |
| `S03.mp4` | — | `https://www.youtube.com/watch?v=VSU5c1AjLBI` | V22 |
| `S04.mp4` | KHOU 11 | `https://www.youtube.com/watch?v=OfT1Nkg3SFw` | V1 |
| `S05.mp4` | — | `https://www.youtube.com/watch?v=YsP3DQPjMYs` | V23 |
| `S06.mp4` | — | `https://www.youtube.com/watch?v=tiCXsZlGxK8` | V19 |

The guide is explicit that the `V` IDs are **leads, not clearance**: publisher,
relevance, identity, chronology and rights each still need verification.
`S` numbers are the local working filenames, not proof of content.

### Expected durations — the verification check after re-download

These are exact and were read from the container headers of the lost files. If a
re-download does not match, it is a **different cut of the video** and must be
reported rather than used.

| File | Frames | fps | Limit (ms) |
|---|---:|---|---:|
| S01 | 2150 | 29.97 | 71738 |
| S02 | 2807 | 29.97 | 93660 |
| S03 | 24928 | 29.97 | 831764 |
| S04 | 3067 | 29.97 | 158691 |
| S05 | 8497 | **59.94** | 141758 |
| S06 | 4756 | 29.97 | 102335 |

S05 is genuinely 59.94 fps and needs the supported one-way 2× frame-drop
conversion (`frame_conversion: 'halve'`, halved count `(raw + 1) // 2`).

---

## 3. Structure that must come back

Verified arithmetic from the surviving inputs:

```
SRT narration        630.256 s
5 sound bites        195.500 s   (49.0–81.5, 0–27, 99–165, 47–73, 27–71)
                     ---------
                     825.756 s
recorded timeline    825.744 s   (difference 0.012 s = closing cue tail)
```

So the last build was **120 narration shots + 5 sound bites = 122 scenes**. That
composition is confirmed, not guessed.

Beat-to-source fitting from the lost script — reuse if the same script text comes
back:

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

Speakers, **as claims only** — never treat as established identity:
003/006 Travion Thompson · 011 Chief Art Acevedo · 012 Sahara Ervin ·
014 Rosalie Jimerson.

---

## 4. What cannot be recovered by any means

- **The production script.** This drove beat planning and carried the sound-bite
  cues. The SRT supplies narration timing, but not `Video File` / `Source IN` /
  `Source OUT` per beat. It has to be re-supplied.
- **Approvals and reuse-rights records.** Per-project; the rebuilt plan returns as
  a review-only draft with approvals pending.
- **The narration audio.** Timing survived in the SRT; the audio did not.
- **The 16:00 runtime decision.** The script that carried it is gone.

## 5. Rebuild order

1. Copy the guide out of `/tmp` into `docs/` — it is the single source for
   everything above and is currently one cleanup away from being lost.
2. Re-download the six sources; check frames/duration against §2.
3. Upload media, narration, `docs/rebuilt/…srt`, and the production script.
4. Build the beat picture plan, resolve refusals, then `documentary-audio`.
5. Verify against 122 scenes / 825.744 s; then record approvals.

## 6. Persistence note

`projects/` and `.venv` are git-ignored, so they vanish on rebuild. This file and
`docs/rebuilt/` are tracked and survive. If projects should also survive, they
must be stored outside the repo or backed up — that decision is still open.
