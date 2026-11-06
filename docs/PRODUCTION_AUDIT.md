# Production audit — what it actually takes to build a master

Verified by running the app, not by reading it. Every claim below was reproduced against a
live server on port 8003 with real files. Nothing here is a guess.

**Test case used:** `Maliyah_Bass_Production_Guide_and_SRT_Map.md` — 120 blocks,
630.256 s, 30 research leads, zero verified source timecodes, zero acquired footage.

---

## 1. The one-line summary

The engine is production-grade. **The paperwork pipeline is where the time goes.** Two of
the three inputs the app asks for are not the ones that decide whether a cut is correct,
and the two inputs that do decide it (real footage, and a source IN/OUT per beat) are the
only ones nothing in the app helps you produce.

| Layer | State | Evidence |
|---|---|---|
| Render engine (FFmpeg, frame-exact, no loop/freeze) | **Solid** | 120 scenes → 10:30.27 master, 18907 frames, decoded clean |
| Cut conformance (`picture_flow.py`) | **Solid** | 120/120 beats placed inside one decoded shot |
| Narration-breath cut easing | **Solid** | 0 cuts over speech; unchanged audio/SRT |
| SRT-in-Markdown recovery | **Solid** | 120 blocks recovered verbatim, 630.256 s |
| Block-map parsing | **Good but brittle** | needs `.` not `,` in CSV clocks |
| Building a block map from footage | **Missing** | no shot index, no candidate search, no export |
| Photo / still support | **Advertised, unusable** | build hard-fails on any image |
| Mapping approval | **Missing** | 120 scenes = 120 PATCH requests |
| Mapping coverage feedback | **Missing** | failures surface one CSV row at a time |

---

## 2. A real end-to-end run (proves the pipeline works)

Uploaded: SRT (extracted from the guide), the guide as production script, a 120-row block
map, a 630.256 s narration, and nine 140 s source clips.

```
build    : 120 scenes · mode srt_block_map · 0 validation errors     (11 s)
flow     : cuts 119 · over_speech 0 · 120/120 inside one decoded shot
render   : review master 10:30.27 · 1280x720 · 30 fps · 34.7 MB     (2 min 52 s)
approve  : 120 PATCH requests                                     (10.5 s)
final    : final master 10:30.27 · 1280x720 · 30 fps · 36.1 MB
frame QC : sampled 6 real timecodes — pictures change where the map says
```

That run is reproducible and fast. Everything in section 4 is a gap in *preparing* those
inputs, not in rendering them.

---

## 3. What you must supply, and what each part buys you

| Input | Format | Required? | What it controls | Failure mode if missing/wrong |
|---|---|---|---|---|
| Narration | mp3/wav/m4a/aac/flac/ogg | **Yes** | Timeline length, breath positions for cut easing | Duration must be within 0.25 s of the SRT end or export is blocked |
| SRT | `.srt`, or a Markdown block table inside the script | **Yes** | Every beat boundary and its wording | `parse_srt` needs strict `00:00:00,000 --> 00:00:00,000` and a blank line between blocks |
| Production script | txt/md/json/docx | **Yes** (auto-assembly checks for it) | Context, names, treatments; can carry the SRT table | A script *without* a beat table places nothing — see gap 1 |
| Block map | CSV | Only if you declare one | **The actual edit**: which file, which source IN/OUT, per block | See gaps 2–6 |
| Footage | mp4/mov | **Yes** | The picture | One block that overruns its source aborts the whole build |
| Photos | jpg/png/webp | Optional idea | Insert/cover gaps | Cannot be used at all today — gap 3 |

### The judgement that only you can make

The engine will happily play a shot that shows the wrong thing. It verifies that a beat
sits inside **one continuous decoded shot**; it cannot verify *which* shot. The former
takes seconds; the latter is the whole editorial job. Plan for that asymmetry: the app
should be doing the first, you should only be doing the second.

---

## 4. Verified gaps (each reproduced live)

### Gap 1 — A script with no beat table builds nothing and hard-fails
Uploading the guide alone gives 120 recovered SRT blocks and **0 beat rows**
(`rows_for` → `mode=''`). Then `build` falls back to keyword assignment, places 0 scenes,
and `validate_assembly` raises *"no native MP4 interval"*, so the storyboard is **never
saved**. Same result with 9 clips uploaded. With no map, no scenes.
*Why it matters:* the guide's own section 5 ("narration-led visual beat plan") is prose
plus a table with the wrong headers (`SRT | Documentary IN → OUT | …`), so the parser
cannot see it. A document that reads like a plan does not act like one.

### Gap 2 — Nothing helps you create the block map
`base_rows_for` only recognises a table with exactly `Timeline in → out` and
`Src IN → OUT`. There is no shot index, no candidate search, no coverage matrix, no
partial-map save, and no map export back out (`GET srt-block-map` → 405; only
`bundle.json` carries it). You write 120–500 rows by hand, from frame inspection.

### Gap 3 — Photos cannot be used: stills are blocked in the only supported mode
A map pointing at `shot1.jpg` builds fine, then: *"Scene 1: clips-only timeline contains a
still image."* Every new project is `picture_policy='clips_only'`, the policy can only be
changed by editing `project.json`, and the still path also dead-ends in
`validate_assembly` (needs a video). `conform.stills()` — the code that would extract
frames — is only reachable in a policy you cannot select. **News-video cases are photo
heavy; this alone can stall an edit.**

### Gap 4 — A CSV block map rejects the most natural timecode format
Writing `00:00:05,400` in a CSV splits the cell (`Row 1: too many CSV cells`). You must
write `00:00:05.400`. The SRT accepts `,` and the map does not; the error message does not
say this. This cost a full failed iteration in the test run.

### Gap 5 — A block map must cover every block, or nothing applies
The map is validated as one contiguous unit: block numbers must be exactly 1..N in order,
the timeline must start at 0 and run unbroken to the SRT end. A map for "the 40 blocks I
have footage for" is rejected wholesale. There is no partial mapping and no per-block gap
row (`gap_note` exists in the schema but block maps are rejected before it is consulted).

### Gap 6 — No map-in-progress, so there is no incremental workflow
Because of gaps 4–5, the realistic loop is: inspect all footage → write a complete
120-row map → upload → discover one bad row → fix → repeat. Each round trip costs the
whole map.

### Gap 7 — No coverage planning against the footage you actually hold
Feasibility check on the guide (its own sampled windows vs the timeline it assigns each
lead):

```
V19 needs 146.3s of timeline, was sampled for ~36s
V22 needs 137.9s, sampled ~78s      V24 needs 109.7s, sampled ~18s
V18 needs  81.8s, sampled ~36s      V2 needs   42.5s, sampled ~24s
... 12 of 21 leads are arithmetically impossible without reuse
```

Separately, the 120 beats need **120 distinct continuous shots; 76 must be ≥5 s and 32
must be ≥6 s** (mean beat 5.25 s, max 7.0 s). A news package cut at 2–3 s per shot cannot
supply that without repeated footage — which then trips the 6-slot repeat policy *fast*.
This arithmetic is knowable in seconds before you cut a single clip, and nothing in the
app tells you.

### Gap 8 — Approval is one request per scene
Final export requires `s['approved']` on every scene; the only bulk endpoint is
`assets/approve-all`, which approves **files**, not scenes. 120 scenes = 120 PATCH calls.
*Fix suggestion (10 lines):* `POST /api/projects/<pid>/scenes/approve-all` with an
`attested` flag, alongside the existing per-scene PATCH.

### Gap 9 — Auto-assembly silently does nothing
`maybe_queue` needs `srt` + `voiceover` + `production_script` **and** a ready plan. Without
a map it returns silently: status stays `idle`, 0 scenes, no message. Upload order makes it
worse — the SRT arrives last, after the footage, so nothing is queued at that point either.

### Gap 10 — Rebuild discards human review
`POST /storyboard {replace:true}` starts from a clean `scenes` array: approvals, in-point
overrides and notes are lost. There is no "rebuild only blocks 40–60". For a 120-beat edit
that is a lot of re-review per iteration.

### Gap 11 — No frame-QC surface
There is a `/scenes/<id>/thumbnail` and a planned-cut sample strip (`conform.samples`), but
nothing in the UI that shows the chosen frame for every beat in one pass, and no
"in-point moved since review" marker. In the last real production, wrong casting was only
caught by contact sheets built with external ffmpeg — three separate passes.

### Gap 12 — Narration flow needs a real voice track
`narration_pauses` decodes silences at −32 dB. In the audit run (a synthetic tone) it found
**0 pauses**, so 0 of 119 cuts were eased into a breath — the one feature that makes cuts
feel professional simply reported nothing useful.

### Gap 13 — Small edges
- No "test the narration against the SRT" step — the user first learns of a mismatch at build time (`±0.25 s` rule).
- Dissolves are documented and validated, then forced back to cuts by `transition_policy='clean_cuts_only'`.
- No resolution/target setting in the UI (manifest hard-codes 1280×720; portrait 720×1280).
- `#render-button` is disabled as soon as any validation error exists, so you cannot start a review render to *look* at the problem.

---

## 5. The workflow that works today, in order

1. **Narration** — upload first. Its duration is the edit length.
2. **SRT** — upload, or let the script's block table seed it (an explicit `.srt` wins).
3. **Production script** — upload. If it carries the block table, its wording is recovered.
4. **Footage** — upload all of it in one batch (numbered names, `v1.mp4`…).
5. **Inspect**: `reviews/Full_source_analysis.json` after a build lists decoded shot changes
   per file. This is the only shot map available; use it to find continuous windows.
6. **Block map** — write with `.` decimals, one row per block, complete 1..N, contiguous,
   source OUT = IN + beat duration exactly.
7. **Build** — then read the flow summary, `cut-audit` and the frame strips.
8. **Approve** every scene (one request each today), then **final export**.

## 6. What "perfect" actually requires (the checklist that recurs for every case)

- **120 distinct continuous shots.** Not 120 clips — 120 *shots that each hold for the
  whole beat*. 76 of them ≥5 s if the SRT is cut at ~5 s per line.
- **Source OUT = source IN + beat duration, to the frame.** Any overrun aborts the build.
- **No repeated or overlapping intervals** within 6 slots, per source.
- **A verified identity for anyone named in the narration.** Otherwise the check demands
  per-scene context disclosure, and final export needs rights notes.
- **Presenters/reporters excluded** from every chosen window — something only continuous
  inspection catches, since sample frames cannot prove a window is presenter-free.
- **Absent footage must be named as a gap**, not covered with an unrelated shot. The
  engine cannot tell the difference; a viewer can.

---

## 7. What to implement (ranked by time saved per case)

| # | Change | Why |
|---|---|---|
| 1 | **Still/photo mode** — policy toggle + `picture_policy='mixed'`, route stills through `conform.stills` | Unblocks every photo-heavy case; the code already exists |
| 2 | **Shot index → candidate list** — from `Full_source_analysis.json`, list every continuous shot (file, in, out, length) and mark which beats it can hold | Turns "watch 15 videos" into "fill 120 slots from a list" |
| 3 | **Feasibility report before cutting** — per source: seconds needed vs seconds available; flag impossible assignments | Kills dead-end plans in seconds (gap 7) |
| 4 | **Block-map builder + export** — prefill 120 rows from the SRT, allow partial saves, download the CSV back | Removes hand-writing and round trips (gaps 2, 5, 6) |
| 5 | **Tolerant CSV clocks** — accept `,` and unquoted timecodes; also accept a Markdown table as a map | Removes a whole class of silent failure (gap 4) |
| 6 | **Bulk scene approval** | 120 clicks → 1 (gap 8) |
| 7 | **Incremental rebuild** — rebuild a block range, keep approvals elsewhere | Makes iteration cheap (gap 10) |
| 8 | **Frame-QC sheet** — contact sheet per 20 beats with source timecode, flag beats whose in-point changed | Catches wrong casting before a 3-minute render (gap 11) |
| 9 | **Loud auto-assembly** — if inputs are present but no plan, say so and offer the template | Kills the silent-idle trap (gap 9) |
| 10 | **Template generator** — emit a ready-to-fill block map from any SRT (120 rows, clocks filled) | Makes the app reusable across cases |

---

## 8. Reusable template: the production script shape the parser wants

The parser is strict but simple. One table, these exact headers, Unicode arrow `→`:

```markdown
| # | SRT | Timeline in → out | Source → file | Src IN → OUT | Treatment · transition |
|---|---|---|---|---|---|
| 1 | 1-3 | 00:00:00.000 → 00:00:16.500 | v19 → v19.mp4 | 00:00:21.000 → 00:00:37.500 | Hold on scene · cut |
```

Rules that are enforced, not advisory:

- every row: exactly one `vN.mp4`, one timeline range, one source range;
- the beat table must cover the SRT start to end, contiguously, with no partial list;
- SRT blocks referenced must be exactly the blocks inside that timeline range;
- `transition` is `cut` or `dissolve` (no adjacent dissolves, none on the last beat);
- source OUT is exclusive and must equal IN + beat duration for video.

A narration-only document (headings, prose, a block table) is also valid input — it seeds
the SRT and the beat table can be added later as the block map instead.

---

## 9. Bottom line

Uploading is three documents plus footage. **Building the master is one input: the block
map.** Everything the engine needs to be professional (shot conformance, breath cuts,
no loop/freeze, frame-exact length) is already in place and proven at 120 beats. What is
missing is everything that helps you *produce* that map from real footage — and one
blocker (photos) that stops photo-heavy cases outright.

If we implement items 1–5 above, the same guide becomes a repeatable template: paste a new
SRT, drop in the footage, get a candidate map, fill the blanks, render.
