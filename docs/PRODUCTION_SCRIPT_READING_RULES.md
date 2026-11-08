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

## Current parser implementation and limits

The editor now recognizes an explicitly structured Markdown shot table with columns for shot ID, C clip reference, exact S source filename, narration timeline IN/OUT, source IN/OUT (or a source frame for a still), treatment, visual description and optional restrictions. The actual incoming production script has not yet been provided, so its format and compatibility cannot be verified; other detailed layouts must be reviewed and the parser adapted rather than guessed. Unsupported nonempty columns, extra non-shot tables, and prose outside shot tables are rejected rather than ignored (headings are allowed). The current assembly path requires the last picture OUT to coincide, at the project frame rate, with the final SRT block's OUT.

Recognized shots retain their authored order and the two separate clocks; they bypass automatic source retiming and cut snapping. A simultaneous block map is rejected rather than silently overriding the detailed script. Explicit archival reuse is permitted, while other source overlaps remain blocked. Exact matches are required even if a legacy source binding exists. Still holds and video freeze-frames are distinct; a requested frame is extracted at the nearest source frame and held without an invented camera move, only when stills are enabled. Footage, context, identity and source provenance still require human inspection and approval: parsing or decoding does not verify them.

Required captions, source-audio playback, transitions and speed changes are not automatically implemented in this strict path; recognized instructions for them block assembly rather than being silently ignored. Natural-language constraints cannot be comprehensively interpreted automatically and must be checked by an editor. The scene-clip ZIP uses authored C references for detailed scripts; if a C reference appears in more than one shot, ZIP creation stops instead of overwriting or inventing names. Existing legacy scripts continue through their prior parser.

A section-based Markdown script can also be read when it has a source clip register, one `Srt` section per uploaded SRT cue, and individually labelled shots with explicit placement, source, extraction, visual, continuity, caption, audio and limits fields. The editor exposes this as a reviewable shot plan, not an approved storyboard. It checks cue clocks and source-register references, reports wording differences from the uploaded SRT, and does not change either document. Caption layout and last-displayed-frame freeze selection remain unsupported and block assembly. Exact visible content, version matching and rights are not verified by this structural reading.

The optional script review timeline displays sourced frames and video ranges without changing the original script, SRT or uploaded filenames. It is explicitly marked as a draft: contextual caption text is shown separately for inspection, not burned in or laid out; requested crops, composition and exact freeze-frame behavior remain unverified. Rendering, smooth-preview creation and scene-clip export are blocked for this draft. Still extraction uses the source frame covering the authored instant for constant-frame-rate media; source version, variable-frame-rate handling, actual content and rights still require review.

For a user-directed moving-video-only review, authored VIDEO intervals remain on the timeline at normal speed, without looping. Sections that supply only a frame and no moving-video OUT are left unassigned; playback stops at those gaps, and the interface identifies the missing moving-video range. No still is extracted for a new moving-only review, and the editor does not invent video lengths or replace the original script, names or SRT. This incomplete review cannot be exported.
