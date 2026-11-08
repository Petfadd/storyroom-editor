# Production script reading requirements

The production script, SRT and uploaded files remain unchanged. A source filename and its explicit time range specify what to play; a separate clip reference is not required. These rules describe the intended review workflow, not a claim that video content or publication rights are verified.

## Source-based instructions

- Use exactly one shot per SRT cue, in original SRT order. Each shot covers its cue’s complete timeline IN/OUT and uses the cue ID (for example, Srt001), not A/B/C subdivisions. Preserve narration wording, names and timestamps. If an older script splits a cue or spans cues, report it for review rather than silently merging or splitting its instructions.
- Identify each full source by its exact uploaded filename, such as `S01.mp4`. Numbering may have gaps and does not establish playback order. Do not rename sources or silently substitute a different upload.
- For each moving shot specify its timeline IN/OUT and its source IN/OUT. Source OUT is exclusive. Use the exact source interval at 1.0×, with looping off; keep continuous footage advancing across subtitle boundaries.
- Separate clip labels are optional in supported scripts and are not displayed on the storyboard. Older scripts that contain them remain readable; their original text and references are preserved. Where an older script provides a linked register, conflicting instructions are reported rather than silently overridden.
- The editor can read supported labelled plain-text cue instructions, cue-per-section, section-per-SRT shot and detailed shot-table formats. For labelled plain-text cues, a `PROPOSED:` source remains provisional and requires human inspection. A source-only version of those formats can omit separate clip references. Unsupported or ambiguous formats and instructions require review; the editor must not guess source times. The current structured plan parser also requires a contiguous timeline starting at zero, so SRTs with gaps cannot yet be conformed to this rule automatically.

## Safety and editorial review

- Match footage to the narrative, identity, chronology and context. A filename match or decoded frame does not verify what a video shows, its provenance, or rights to publish it.
- Never use loops, screenshots, extracted video frames, freeze-frames, false motion, unlisted footage or unrelated repetition to fill gaps. Static photograph or document intervals, if explicitly scheduled under the approved workflow, count toward the still budget and need separate eligibility and rights checks.
- The current moving-only review leaves stills and unresolved source ranges unassigned. Playback pauses at missing or invalid shots; it does not guess a replacement. The review is not a finished edit or export.
- Scripted captions appear as review text, not rendered captions. Caption layout, visual verification, media conformity and rights decisions remain outstanding. No final render is promised until required checks pass.
