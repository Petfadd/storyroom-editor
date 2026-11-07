# Production script reading requirements

These are the user's requirements for their forthcoming detailed production script. They describe the desired behavior; they are **not a claim that all of it is already implemented or visually verified**. The script itself and its actual format must be inspected and tested before asserting compatibility.

## Identifiers and timing

- `Srt01`, `Srt02` and so on identify original subtitle/narration blocks and determine their order. Work through all blocks in their actual order; the user's 120-block example is not a hard-coded runtime or block count.
- `S01.mp4`, `S02.mp4` and so on identify uploaded full source videos. Match the specified filenames exactly; missing numbers are intentional and do not imply a renumbering or equivalent file. A different file is not a match without an explicit user selection.
- `C01`, `C02` and so on identify researched source segments, not playback order. `Srt01-A`, `Srt01-B`, etc. identify shots within a block and determine their order there. Do not conflate source, clip and narration identifiers.
- Narration timeline IN/OUT specify placement in the finished programme. Source IN/OUT specify the source portion to use. OUT is exclusive. Preserve the authored timelines, review cut points at the project frame rate, and report gaps, overlaps, short sources and conflicts. Do not silently move a requested source or timeline position.

## Assembly and verification

Read the complete supplied script first. In narration order, follow the listed shots within each block. For each, match the source, review the visual description, treatment and restrictions, extract the instructed interval or frame, and check context, identity, continuity and duration. Filenames and clip identifiers do not verify visible content or imply playback order. An SRT boundary alone is not a shot change; continuous specified source intervals should continue smoothly, with narration uninterrupted.

- **VIDEO:** play the specified source segment at normal speed unless instructed otherwise.
- **CONTEXT VIDEO:** use case-related footage without representing it as a recording of the narrated event.
- **ARCHIVAL REUSE:** use the specified reuse deliberately; do not present it as new evidence or add loops.
- **STILL HOLD:** extract the specified existing photograph/frame and hold for the assigned timeline duration.
- **EDITORIAL FREEZE-FRAME:** freeze the specified video frame for the assigned duration, without implying additional source motion.
- Include explicitly required contextual captions. Do not invent captions or imply an event is documented when it is not.

## Identity, context and source limitations

Show the correct person when identified and preserve any “not pictured” limitation. Do not misidentify a victim, suspect, relative, investigator or attorney. Do not represent apartment exteriors as kitchen evidence photos, external news cameras as police bodycam, interviews as confessions or 911 audio, or court footage as automatically depicting a trial, verdict or plea. Later location footage does not establish earlier events.

Use only the user's supplied, approved official news-channel sources; approval and provenance require review, not an inference from a filename. Do not introduce stock footage, unrelated cases, independent creators or third-party reuploads. Do not fabricate CCTV, documents, timestamps, social posts or police audio. No unlisted loops, speed changes, holds or silent substitutions: flag missing, mismatched, unverified or conflicting material for the user.

Narration is the main audio. Mute source news voiceover unless explicitly authorized in the script. Keep YouTube URLs out of the production script; use the saved filenames and clip references. Check continuous preview, frame alignment and complete coverage before export. Contextual footage is not proof of the narrated event.
