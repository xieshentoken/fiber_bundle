---
name: bili-note-v2
description: Convert Bilibili or YouTube videos into detailed, evidence-linked learning notes published as portable Markdown or directly into Obsidian, using subtitle-first extraction, multilingual terminology-aware ASR fallback, authenticated browser access, dynamic keyframes or an audio-only route, timestamped coverage audits, and safe post-publication media cleanup. Use for a single B站/Bilibili or YouTube video, including login/member-visible content the user may legitimately access, while excluding comments, danmaku, and live chat and protecting browser authentication secrets.
---

# Bili Note V3

Build a reproducible material package before writing the note. Treat speech, on-screen text, diagrams, code, UI actions, and visual changes as timestamped evidence rather than relying on transcript text alone.

## Hard Rules

- Never request, download, archive, summarize, or score danmaku, comments, or live chat. They are not source evidence.
- Use an existing browser session for authenticated access. Never read, print, copy, decrypt, or archive cookies, passwords, authorization headers, browser profiles, or persistent tokens.
- Process only videos the current user can legitimately view. Stop at denied access or DRM; do not bypass access controls or record the screen as a workaround.
- Confirm the terminology glossary before running ASR. A changed glossary invalidates prior confirmation.
- Preserve raw transcript text. Store normalization decisions and unresolved candidates separately.
- Send only selected keyframes to a cloud model. Never upload the full video, audio, browser state, or authentication data.
- For an audio-only job, do not extract frames, make visual claims, or embed images. The final note must state `音频优先整理，未核验画面内容`.
- Treat full frames as analysis evidence, not finished-note illustrations. Crop standalone charts, tables, diagrams, data visualizations, formulas, code, or essential details; never use a full page screenshot merely to make the note look illustrated.
- Delete only registered job-owned media, only after both internal and published-note audits pass. Never delete a user-owned external source file, symlink, or unregistered path.
- Do not call a note complete until the `ready-for-note` audit passes. State coverage and unresolved limitations in the final note.
- Do not treat a subtitle/ASR result over a preview download as complete: compare the declared platform duration with the local media duration before ASR, keyframes, or publication.

## Runtime Routing

Read [authenticated-access.md](references/authenticated-access.md) before opening a login/member video. Read [visual-analysis.md](references/visual-analysis.md) before cloud frame analysis. Read [output-publishing.md](references/output-publishing.md) before configuring final Markdown or Obsidian destinations. Read [evidence-schema.md](references/evidence-schema.md) when creating, repairing, or resuming a job package.

Use the current agent's Browser, Chrome, or Edge browser connector for login-state work on either platform. Use local scripts for deterministic file processing. Do not substitute browser-profile or cookie extraction when a connector is unavailable. Edge and Chrome handoff requests use `scripts/browser_handoff.py`; the browser remains the owner of login state. A WorkBuddy or watcher task must prove that a browser connector is available before claiming authenticated extraction is possible.

## Workflow

Use task-scoped variables in command examples. Resolve them from the current request; never hard-code machine-specific paths:

```bash
BILI_NOTE_JOB_DIR="./jobs/platform-video-id"
BILI_NOTE_MEDIA="./inputs/video.mp4"
BILI_NOTE_INPUT_SUBTITLE="./inputs/subtitles.srt"
: "${BILI_NOTE_NOTE_DIR:?set note directory}"
: "${BILI_NOTE_ASSET_DIR:?set attachment directory}"
```

### 1. Check Capabilities

Run:

```bash
python3 scripts/check_environment.py
```

The report distinguishes videos with usable platform subtitles from videos that require local ASR. Do not treat the subtitle route as proof that the no-subtitle route is ready.

Required for the full local pipeline:

- `yt-dlp`: public Bilibili/YouTube metadata, one selected subtitle track, and media.
- Deno or a supported Node runtime: YouTube JavaScript challenge handling.
- `ffmpeg` and `ffprobe`: media merge, ASR audio preparation, and keyframes.
- An ASR backend only when usable subtitles are absent.
- A cloud-capable image model for selected keyframe analysis.

The audio-only route needs `yt-dlp`, `ffmpeg`, `ffprobe`, and an ASR backend only when usable
subtitles are absent. It does not need a cloud-capable image model.

Missing enhanced capabilities must stop only the affected route. Do not claim full visual coverage without keyframe extraction and analysis.

Install the shared multilingual ASR environment once when `public video without subtitles` is `PARTIAL`:

```bash
python3 scripts/setup_asr.py
```

Reuse this environment across Bili Note skills. Do not create a second model environment for each job.

### 2. Initialize a Job

Create one job package per source video. Both forms are supported:

```bash
python3 scripts/init_job.py "https://www.bilibili.com/video/BVxxxxxxxxxx/" \
  --out "${BILI_NOTE_JOB_DIR}" \
  --topic "user-provided domain context"

python3 scripts/init_job.py "https://www.youtube.com/watch?v=XXXXXXXXXXX" \
  --out "${BILI_NOTE_JOB_DIR}" \
  --topic "user-provided domain context"
```

For a conversational, interview, podcast, or chat-style video whose useful content is spoken,
initialize it as an audio-only job:

```bash
python3 scripts/init_job.py "https://youtu.be/XXXXXXXXXXX" \
  --out "${BILI_NOTE_JOB_DIR}" \
  --topic "user-provided domain context" \
  --evidence-mode audio-only
```

This mode downloads only the best audio stream, keeps timestamped transcript evidence, skips
keyframes/cloud vision/illustrations, and produces a text-only note. Do not infer that a video is
audio-only without the user's instruction: screen demonstrations, slides, and on-screen data may
still matter.

The job policy forbids danmaku, comments, live chat, and credential storage. YouTube playlists, active/upcoming live streams, and still-processing post-live streams are outside the V3 MVP; one URL creates one video job.

Capture the user's output sentence at the start of the task and translate it into explicit destinations after job initialization. For example:

```bash
python3 scripts/configure_output.py "${BILI_NOTE_JOB_DIR}" \
  --format obsidian \
  --note-dir "${BILI_NOTE_NOTE_DIR}" \
  --asset-dir "${BILI_NOTE_ASSET_DIR}" \
  --profile-name portable --cleanup-media
```

Use `--format markdown` for a portable note and standard relative image links. Obsidian note and attachment directories must be in the same vault. Use `--keep-media` only when the user asks to retain downloads.

### 3. Verify Access

Before treating an existing video job or note as reusable, run the deterministic gate:

```bash
python3 scripts/job_gate.py "${BILI_NOTE_JOB_DIR}" --json
```

Only `decision=reusable` permits reuse. A legacy job, preview-limited source, missing source
coverage report, unavailable handoff, or failed final audit must resume from the earliest
incomplete phase; an existing L2 note is not proof of complete source evidence.

Open the source in the user's existing browser session. Classify it as `public_verified`, `auth_required`, `authenticated`, `member_authorized`, `denied`, or `drm_blocked`. Let the user complete login in the browser when necessary. These are Skill process states; watcher task status remains generic (`waiting_external`, `waiting_user_action`, `done`, `failed`, or `blocked`).

Record only the result:

```bash
python3 scripts/set_access_state.py "${BILI_NOTE_JOB_DIR}" \
  --status member_authorized --verified-by browser
```

For an Edge or Chrome handoff request that cannot yet provide local media, create a safe action request:

```bash
python3 scripts/browser_handoff.py request "${BILI_NOTE_JOB_DIR}" --backend edge
```

Chrome is a first-class backend as well:

```bash
python3 scripts/browser_handoff.py request "${BILI_NOTE_JOB_DIR}" --backend chrome
```

After a public probe reports incomplete source coverage, use the access gate to create an
idempotent request and return a watcher-safe waiting result:

```bash
python3 scripts/access_preflight.py "${BILI_NOTE_JOB_DIR}" \
  --backend chrome --json
```

Exit code `10` means `waiting_user_action`; it is an expected handoff state, not a failed
download. If no Edge/Chrome connector is exposed by the executor, do not claim that the
user's desktop browser session was used; return `handoff_unavailable` and wait.

After the browser connector has verified visible playback, it may submit a local media copy and non-secret playback facts. The media path must be local; cookies, Local Storage, authorization headers, passwords, and signed media URLs are not accepted:

```bash
python3 scripts/browser_handoff.py record "${BILI_NOTE_JOB_DIR}" \
  --backend edge --access-state member_authorized \
  --playback-at 30 --near-end --media-path "${BILI_NOTE_MEDIA}" \
  --declared-duration 1068.445
```

Do not continue on `denied` or `drm_blocked`. If the browser can play the video but no safe local handoff exists, keep `handoff_state=unavailable` and wait for user action; never fall back to raw browser-state extraction.

For public videos only, acquire source material with the no-cookie, single-video route:

```bash
python3 scripts/acquire_public.py "${BILI_NOTE_JOB_DIR}" --mode all
```

For an audio-only job, the same `--mode all` command downloads `source/audio.*` using a strict
audio-only format. It never falls back to downloading a video. `--mode audio` is also available
for that job type; `--mode media` is rejected to prevent an accidental video download.

For authenticated/member videos, use the browser handoff in [authenticated-access.md](references/authenticated-access.md). If the runtime cannot safely provide local media without exposing authentication secrets, stop and report that limitation.

Any browser-handoff media must be copied inside the job and explicitly registered with `register_managed_media.py`. Never register the user's original external file.

For every acquired source, `metadata/source_coverage.json` must report complete declared-vs-local duration coverage. A preview-limited or unverified source is not eligible for ASR, evidence, publication, or cleanup.

### 4. Build and Confirm Terminology

Before ASR, inspect the title, description, part titles, and a small number of title/agenda frames. For an audio-only job, use the title, description, part titles, and user context only; do not extract frames. Ask the user for ambiguous abbreviations, project names, people, formulas, product names, and mixed Chinese-English terms.

Add terms. Treat common words such as `best` as contextual aliases, not safe aliases:

```bash
python3 scripts/manage_glossary.py add "${BILI_NOTE_JOB_DIR}" \
  --canonical BEST \
  --full-name "Burning plasma Experimental Superconducting Tokamak" \
  --type project \
  --context 核聚变 --context 托卡马克 \
  --alias best --alias beast --safe-alias 贝斯特
```

Show `terms/term_review.md` to the user, incorporate corrections, then confirm:

```bash
python3 scripts/manage_glossary.py confirm "${BILI_NOTE_JOB_DIR}" --by user
python3 scripts/audit_job.py "${BILI_NOTE_JOB_DIR}" --stage ready-for-asr
```

Use `terms/asr_context.json` as the ASR hotword list and initial prompt. If the user explicitly confirms that no specialist terms are needed, use `confirm --allow-empty`.

### 5. Produce and Normalize the Transcript

Prefer a usable human or platform subtitle. Run ASR only when subtitles are absent or unusable. Preserve word or cue timestamps.

Normalize a subtitle or ASR result without overwriting it:

```bash
python3 scripts/normalize_transcript.py "${BILI_NOTE_JOB_DIR}" \
  --input "${BILI_NOTE_INPUT_SUBTITLE}" --kind asr
```

When no usable subtitle exists, run the integrated Qwen3-ASR route after glossary confirmation. Use `auto` for multilingual YouTube content unless the source language is known:

```bash
python3 scripts/run_asr.py "${BILI_NOTE_JOB_DIR}" \
  --media "${BILI_NOTE_MEDIA}" \
  --language auto --chunk-seconds 30 --chunk-overlap-seconds 1.5
```

The command extracts local mono audio, passes `terms/asr_context.json` to Qwen3-ASR's context input, writes timestamped backend evidence, preserves `asr_raw.srt`, and produces `asr_normalized.srt`. Chunks overlap slightly to reduce boundary loss; `transcript/asr_coverage.json` records failed chunks and uncovered time ranges. It reuses a backend result only when the audio SHA-256, model, glossary, context, language, and chunk settings still match.

Review `review/term_ambiguities.md`. Resolve uncertain terms by checking the audio and nearby visual evidence; do not use blind global replacement. Re-run ASR after changing the glossary because terminology changes invalidate the ASR cache.

### 6. Extract Dynamic Keyframes (Full Evidence Mode Only)

Use both interval coverage and scene-change detection:

```bash
python3 scripts/select_keyframes.py "${BILI_NOTE_JOB_DIR}" \
  --video "${BILI_NOTE_MEDIA}" \
  --profile balanced --interval 15 --scene-threshold 0.32 --max-frames 120
```

The selector supports three scan profiles: `quick` uses I-frames and falls back to interval frames, `balanced` combines interval and scene-change candidates, and `deep` uses a denser interval and larger budget. Add `--start T --end T` for a focused range, `--timestamps T1,T2,...` for transcript-cue frames, and `--append` to merge the pass into an existing manifest while preserving prior frame IDs. The manifest records the scope, candidate source, pinned frames, dedupe method, and maximum temporal gap. Inspect `frames/cloud_manifest.json` and its selected frames for personal or confidential information before cloud upload.

For an audio-only job, skip this step and do not create a frame manifest.

### 7. Analyze Selected Frames (Full Evidence Mode Only)

Analyze each selected frame, or a three-frame before/during/after group for operations and demonstrations. Write one JSON object per frame to `visual/visual_results.jsonl` following [visual-analysis.md](references/visual-analysis.md).

Mark a frame `not_needed` only when it is a verified duplicate, transition, or visually uninformative talking-head frame. Mark failures explicitly; do not omit them silently.

The visual result must copy its timestamp from `frames/frame_manifest.json` and include a
verified `visual_verification` record. If no image-capable connector or local VLM actually
inspected the selected frames, do not fabricate `manual-analysis` rows or a montage summary;
pause for a visual-capable runtime (or explicitly choose the audio-only route when appropriate).

For every analyzed frame, output zero or more tight `illustrations` regions using normalized bounding boxes from [visual-analysis.md](references/visual-analysis.md). Regions should isolate a chart, table, diagram, data visualization, formula, code block, or necessary detail and exclude subtitles, watermarks, presenters, and unrelated page chrome. Then generate local crops:

```bash
python3 scripts/crop_illustrations.py "${BILI_NOTE_JOB_DIR}"
```

For every created crop, write `evidence/illustration_bindings.jsonl` with its exact frame,
timestamp, segment, caption, and note anchor. Use the `primary_segment_id` recorded for that crop
in `evidence/illustration_placement.json` as the binding `segment_id`; a binding that points to a
different segment fails the audit. The binding is checked before a note can pass.

Resolve every failed or full-frame-like crop. Do not fall back to the selected full frame. It is valid to produce no illustration when the video has no suitable visual evidence.

For an audio-only job, skip this step and keep `frames/`, `visual/`, and `illustrations/` empty.

### 8. Fuse Evidence and Audit

Build timestamped evidence segments:

```bash
python3 scripts/build_evidence_timeline.py "${BILI_NOTE_JOB_DIR}"
python3 scripts/audit_visual_evidence.py "${BILI_NOTE_JOB_DIR}" \
  --require-illustrations --require-bindings
python3 scripts/audit_job.py "${BILI_NOTE_JOB_DIR}" --stage ready-for-note
```

The final audit also checks authenticated handoff readiness and source coverage whenever source metadata or media exists. Never lower the denominator by using only the preview duration.

When ASR was used, the evidence and audit stages also require `transcript/asr_coverage.json` to report zero failed chunks and zero uncovered ranges. A partial ASR run remains `needs_review` until it is repaired or explicitly disclosed.

For audio-only jobs, `build_evidence_timeline.py` groups normalized subtitle or ASR cues and
audits transcript coverage instead of visual coverage. A passing audit requires complete
timestamped cue coverage, zero unresolved terminology candidates, zero selected frames, and zero
illustrations.

Resolve every failed gate or disclose why it cannot be resolved. Do not lower the visual coverage denominator by deleting failed frames.

### 9. Write the Note

Use [note-template.md](assets/note-template.md) for full-evidence jobs and [audio-note-template.md](assets/audio-note-template.md) for audio-only jobs. Organize by learning structure, not minute-by-minute narration. Place each crop using `evidence/illustration_placement.json`: embed it beside the explanation of its `representative_text`/`segment_transcript`, and make sure the crop's `note_anchor` text also appears in the surrounding prose (not only inside the image alt text). Embed every created crop exactly once; never embed the same crop twice or leave a created crop unused. Do not embed `frames/selected/` in schema 2.1 jobs. When there is no suitable crop, keep the note text-only and explicitly disclose `无适合裁剪的插图`. For audio-only jobs, embed no image and include the exact disclosure `音频优先整理，未核验画面内容`. Distinguish source statements, visual observations, synthesis, and uncertainty.

Save the final file as `note/note.md`, then run:

```bash
python3 scripts/audit_job.py "${BILI_NOTE_JOB_DIR}" --stage final
```

The final audit includes the visual evidence and illustration-binding audit. Publication and
post-publication cleanup are refused unless that semantic audit passes; a missing visual
capability therefore produces a review/waiting result instead of a misleading note.

`note/note.md` is the internal evidence-linked draft. Publish it to the configured destination:

```bash
python3 scripts/publish_note.py "${BILI_NOTE_JOB_DIR}" --dry-run
python3 scripts/publish_note.py "${BILI_NOTE_JOB_DIR}"
```

Before returning a result to a watcher-dispatched task, derive the machine-readable outcome marker:

```bash
python3 scripts/write_task_outcome.py "${BILI_NOTE_JOB_DIR}"
```

Include the printed `<!-- geof-task-outcome {...} -->` marker in the task result. Only a passed `final` audit produces `status=done`; login/member or preview-limited work produces `status=waiting_user_action`.

The publisher copies only referenced crops, rewrites standard Markdown or Obsidian embeds, audits destination files and hashes, and then deletes registered source media and ASR audio when cleanup is enabled. Any failed publish or audit retains the media. Audio-only publication legitimately has zero copied illustrations and zero cloud frames. Report the published note path, copied illustration count, source and transcript/visual coverage as applicable, unresolved terminology, authentication route, cloud frame count, cleanup status, and bytes freed.

## Resume Behavior

Read `metadata/pipeline_state.json` and resume from the earliest incomplete phase. Do not repeat cloud analysis for a frame with a valid result. Use `--append` for focused keyframe passes so existing frame IDs and visual results remain valid. Reconfirm the glossary after any term edit. Reverify browser access when the session or temporary media authorization has expired.

For watcher-dispatched jobs, `waiting_user_action` is non-terminal. Keep the task and job
available for resumption, do not copy a preview note as a new completed artifact, and only
archive after the `final` audit passes.
