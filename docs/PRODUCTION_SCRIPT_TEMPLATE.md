# Reusable Production Script Template

Use this template for any subject or runtime. Replace every item in square brackets and every example table row before importing. Do not invent source contents, identities, events, or permissions. This document is a planning guide; it does not verify footage or clear rights.

## 1. Production details

- **Working title:** [Title]
- **Subject:** [Who or what the story is about]
- **Purpose:** [What the viewer should understand]
- **Audience:** [Intended audience]
- **Target runtime:** [Duration; use the actual narration/SRT duration, not a fixed number of scenes per minute]
- **Format:** [16:9 / 9:16 / other]
- **Tone and approach:** [e.g., factual, restrained, chronological]
- **Picture policy:** [Moving footage only, or moving footage and stills]
- **Transition policy:** [Clean cuts, unless otherwise explicitly planned]
- **Narration file:** [Exact uploaded filename]
- **SRT file:** [Exact uploaded filename]

## 2. Editorial instructions

- Follow the supplied narration and SRT in their original order. Do not rewrite, duplicate, shorten, or loop the narration.
- Use footage only where it can actually cover the assigned time. Do not loop footage or freeze the last frame to fill a gap.
- Avoid repeated source intervals. A continuous shot may span consecutive beats only when the selected source intervals actually run on from one another.
- Match the picture to the narration without implying an unverified identity, event, date, or location. Flag uncertain or unavailable footage for review rather than guessing.
- Keep the narration continuous across picture cuts. Use clean cuts unless another supported transition is explicitly requested.
- Follow the selected picture policy. If moving footage only is selected, do not substitute extracted still frames.
- Review the edit, source context, identities, and rights before approving or exporting. An uploaded filename or an instruction in this document is not proof.
- Other instructions: [Add project-specific requirements or delete this line.]

## 3. Source register

List each file that you actually upload. The filenames below are examples only. Record rights and descriptions as your claims until independently reviewed.

| Source ID | Local file | YouTube video / original source | Publisher | Role in this cut | Rights state |
|---|---|---|---|---|---|
| v1 | v1.mp4 | [Source URL or offline origin] | [Publisher] | [Observed content and intended use] | [Pending / reviewed; record basis] |
| v2 | v2.mp4 | [Source URL or offline origin] | [Publisher] | [Observed content and intended use] | [Pending / reviewed; record basis] |

## 4. Picture-beat plan

**The rows below demonstrate the required format; they are not a plan for your project.** Replace them with rows covering your entire timeline. The editor recognises the exact column headings and the `→` arrow shown below. For this Markdown beat-table format, use uploaded filenames such as `v1.mp4` or `r1.mp4` (or `.mov`); do not infer a match from a similar filename. For other filenames, create or upload an SRT block map in the app and select the actual files explicitly.

| # | SRT | Timeline in → out | Source → file | Src IN → OUT | Visual required | Status | Match rationale · limit on implication | Review | Treatment · transition |
|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 00:00:00.000 → 00:00:05.000 | v1 → v1.mp4 | 00:00:10.000 → 00:00:15.000 | [Describe the visible picture] | context | [Why it fits; what must not be implied] | [Needs review] | Native footage · cut |
| 2 | 2 | 00:00:05.000 → 00:00:10.000 | v2 → v2.mp4 | 00:00:20.000 → 00:00:25.000 | [Describe the visible picture] | context | [Why it fits; what must not be implied] | [Needs review] | Native footage · cut |

### Beat-plan rules

1. Start the timeline at `00:00:00.000`. Each row's timeline OUT must equal the next row's timeline IN; the final OUT must cover the end of the SRT timeline. Use as many rows as the material needs—there is no fixed number per minute.
2. Enter the actual SRT block number(s) covered by each beat, in order, for example `1` or `2-3`. Check the references against the uploaded SRT and the actual timeline range.
3. Use exactly one uploaded `vN.mp4`, `rN.mp4`, `vN.mov`, or `rN.mov` filename in each Markdown beat row, matching the filename exactly. If the intended footage is missing, upload it or explicitly select the correct file in the app; never assume a similarly named clip is equivalent.
4. For moving footage, source OUT is **exclusive** and must be source IN plus the beat duration. It must fit inside the uploaded clip. For example, a five-second timeline beat uses a five-second source interval.
5. Do not reuse overlapping source intervals unless reuse is explicitly permitted by the project and editorially appropriate. Do not pad a shortage with a loop or frozen frame.
6. End the treatment cell with `· cut`. Dissolves require the project to allow them and sufficient transition handles; do not use a dissolve on the last beat or on adjacent exits. Prefer clean cuts for this template.
7. The plan remains provisional until the footage, identity, context, and rights are reviewed. If a suitable source is unavailable, leave the decision unresolved in your notes and use the app's block-map editor to record a gap rather than inserting a fictional filename.

## 5. Narration and timing

- Use the original SRT as the timing and wording reference. Upload it separately along with the narration file.
- Preserve the actual full runtime, including the ending. Check that the narration duration and final timeline duration agree.
- Captions are a timing reference unless you explicitly choose a captioned export; do not assume they are burned into the picture.

## 6. Review and delivery checklist

- [ ] All intended source files and narration are uploaded and named correctly.
- [ ] Every beat has an appropriate, playable source interval and no unintended repetition.
- [ ] Still-image use agrees with the chosen picture policy.
- [ ] Every visible subject and claim has been checked against the actual footage.
- [ ] Rights and editorial approvals have been reviewed and recorded.
- [ ] Timeline is continuous, matches the SRT and narration, and covers the complete runtime.
- [ ] App validation issues are resolved; review the resulting cut before final export.
