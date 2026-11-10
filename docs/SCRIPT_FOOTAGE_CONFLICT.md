# Script vs. footage conflict — diagnosis for `b38d65fe1c95471d`

**Verdict: yes, the production script is the cause.** The app was reporting it badly, but
it was not inventing the problem. Every number below is measured from the files on disk.

## What the script asks for

| Beat | Kind | Source IN → OUT | Length |
|---|---|---|---|
| 001 | narration | 0.000 → 15.000 | 15.000 s |
| 002 | sound bite | 15.000 → 36.000 | 21.000 s |
| 003 | narration | 36.000 → 51.000 | 15.000 s |
| 004 | sound bite | 51.000 → 88.000 | 37.000 s |
| 005 | narration | 88.000 → 100.000 | 12.000 s |
| 006 | sound bite | 100.000 → 124.000 | 24.000 s |
| 007 | narration | 124.000 → 142.000 | 18.000 s |
| 008 | sound bite | 142.000 → **174.000** | 32.000 s |
| 009 | narration | **174.000 → 187.000** | 13.000 s |
| 010 | sound bite | **187.000 → 212.000** | 25.000 s |
| 011 | narration | **215.000 → 235.000** | 20.000 s |

Total picture requested: **232.000 s**.

## What the uploaded file actually is

`S01_PBSNewsHour_Sharapova_Press_Conference.mp4` — **173.740 s** (5207 frames at 30000/1001).

`232.000 − 173.740 = 58.260 s` of the script cannot exist in this file. The script's own
header says `TOTAL DURATION: ~04:45,000` (285 s), so it was written against a longer cut of
the press conference than the one uploaded here.

Beats 001–007 fit exactly. Beats 008–011 do not.

## The second conflict: the narration is 7 s longer than the script's clock

| | Time |
|---|---|
| Script's last narration OUT (Beat 011) | 93.000 s |
| Uploaded narration file (`inputs/voiceover.mp3`) | 100.049 s |
| Last spoken word (transcribed) | 99.800 s |

The words from 93.0 s to 100.0 s are real speech — *"…to the court, the confession on that
Los Angeles stage forever altered the legacy of one of Tennis's biggest icons."* The script
has no Beat for those 7 seconds, so Beat 011's narration clock ends too early.

Note the two conflicts also compete for the same footage: fixing the picture overrun
correctly means Beats 008–011 all have to end at 173.740 s, which leaves them all showing
the same closing seconds.

## What changed in the app so this does not recur

Nothing about the script is edited automatically. What changed is that the app now says
what is wrong, once, with numbers — at the moment the pieces first meet.

1. **Upload preflight.** `POST /api/projects/<id>/upload` now returns `script_preflight`
   with any duration conflict, and the studio shows it in the upload feedback line
   immediately, instead of leaving it for a later review panel.
2. **One root cause per file, not one error per Beat.** Nine near-identical
   "Source OUT exceeds video duration" lines became one message naming the file, its real
   length, the highest time the script asks for, and which beats are affected.
3. **No more "not implemented".** The panel used to open with text that read like a broken
   deployment. It now says the Beat script goes through explicit review.
4. **Sound bites are fitted too.** `documentary_beat_plan.fit()` only adjusted narration
   beats; a bite pointing past the end of its file was carried into the storyboard and
   failed later with a bare "source OUT exceeds video duration". Bites are now clamped with
   a recorded adjustment, or blocked with a message naming the unused seconds of the file.
5. **Repeated frames are refused, not offered for approval.** When a file is too short,
   auto-fitting replayed the same closing seconds in several beats and listed them as
   "repeated footage to approve". Identical ranges are now a blocking issue that says the
   project needs a longer source or one more video.
6. **Uncovered narration gets a proposed range.** The build required an explicit gap
   decision for narration no Beat covers, but the panel offered none, so the build could
   only ever fail with *"Explicit gap decisions do not match every uncovered narration
   interval."* The proposal now lists each hole with a source range taken from seconds no
   other Beat uses, and the build sends them automatically.
7. **The replacement picker names the right Beat.** It was hard-coded to Beat 007; it now
   reads the Beat that is actually blocking (here, 010).

## The two ways to clear it

**Option A — re-upload the full-length source.** The script wants 232 s of picture; the
uploaded cut is 173.740 s. A source of at least **235 s** makes Beats 001–011 fit as
written. The narration still needs the Beat 011 clock extended to **1:40.050** (see B2).

**Option B — fit the script to this 173.740 s cut.**

1. Beats 008–011 must all end at **173.740 s**, the last decodable frame:
   - 008 sound bite: `142.000 → 173.740` (31.740 s, not 32)
   - 009 narration: `160.740 → 173.740` (13 s)
   - 010 sound bite: `160.740 → 173.740` (13 s, not 25)
   - 011 narration: `153.740 → 173.740` (20 s)
   If you update `Source OUT`/`Duration` in the script, keep the sound-bite `Duration`
   field equal to the new range or the reader will reject the file.
2. Beat 011's `VO Clock` must run to **00:01:40,050**, not 00:01:33,000, so the closing
   7 seconds have a Beat.

Be aware that B1 makes Beats 009 and 010 show the same 13 seconds of picture. That is a
consequence of the file being 58 s too short, not something the app can resolve without
either a longer source or a second video. The app now states this instead of silently
repeating the footage.

## Verification

- `python -m unittest discover -s tests` → **237 tests, OK (skipped=3)**
- `node --check static/documentary.js`, `node --check static/studio.js` → clean
- The live project still reports exactly three messages, and `script_preflight` reaches
  the browser on upload.
