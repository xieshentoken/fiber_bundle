# Selected-Frame Cloud Analysis

## Upload Boundary

Read `frames/cloud_manifest.json`. Upload only listed local images. Never upload the video, audio, browser screenshots containing account controls, cookies, headers, or unrelated local content.

Inspect frames before upload. Crop or redact unrelated personal data when it does not remove evidence needed for the note. Record the redaction in the result.

## Analysis Strategy

- Slides and boards: recover hierarchy, labels, formulas, arrows, and the conclusion of the visual.
- Code and terminals: preserve visible code or command text, changed values, execution results, and error messages.
- Software demos: describe the target control, action, state change, and resulting state.
- Charts and tables: identify axes, units, trends, comparisons, and limitations. Do not invent unreadable values.
- Physical demonstrations: use before/during/after frames to describe the action and result.
- Talking heads and transitions: mark `not_needed` only after confirming there is no unique visual evidence.

Treat the selected frame as evidence, not as the finished note illustration. For each useful
chart, table, diagram, data visualization, formula, code block, UI detail, or physical detail,
identify a tight standalone crop. Exclude burned-in subtitles, watermarks, window chrome,
presenters, decorative margins, and unrelated page content whenever possible. Do not create a
crop candidate for title-only slides or generic full-screen screenshots.

OCR text is evidence, while a visual summary is interpretation. Keep them separate.

## Output Contract

Write UTF-8 JSONL to `visual/visual_results.jsonl`. Each selected frame must have exactly one row.
`frames/frame_manifest.json` is the sole timestamp authority: copy its `timestamp` and
`selected_path` by `frame_id`; never use a default timestamp or a timestamp invented by a
downstream writer.

```json
{"frame_id":"F0001","timestamp":18.42,"analysis_status":"analyzed","visual_type":"slide","summary":"The slide introduces the BEST fusion project.","ocr_text":["BEST","Burning plasma Experimental Superconducting Tokamak"],"claims":["BEST is expanded on the slide"],"action":null,"illustrations":[{"region_id":"R01","kind":"diagram","bbox_norm":[0.12,0.18,0.88,0.79],"caption":"BEST project concept diagram","recommended":true}],"confidence":0.97,"redactions":[],"cloud_provider":"provider name","cloud_model":"model name","visual_verification":{"verified":true,"method":"cloud_vision","model":"provider model","input_sha256":"sha256 copied from frame_manifest"}}
```

`visual_verification` is mandatory for `analyzed` and `not_needed` rows. `method` must be
`cloud_vision`, `local_vlm`, or explicitly reviewed `human`; human review also records a
`reviewer`. A `manual`/`manual-analysis` provider is never accepted as visual evidence. The
`input_sha256` must match the selected frame hash when the manifest supplies one. This prevents
an unlabeled montage, a stale frame, or a text-only model response from being presented as
per-frame visual analysis.

`bbox_norm` is `[x_min, y_min, x_max, y_max]` in normalized `0..1` image coordinates.
Use one region per standalone visual. Regions must be tight enough that their area is no more
than 85% of the frame; refine larger regions instead of requesting a full-frame fallback.
Allowed `kind` values are `chart`, `table`, `diagram`, `data_visualization`, `formula`, `code`,
`ui_detail`, and `physical_detail`. Use an empty `illustrations` array when no visual deserves
to be placed in the note.

Allowed `analysis_status` values:

- `analyzed`: Useful visual evidence was interpreted.
- `not_needed`: Verified duplicate, transition, talking-head, or no unique visual information.
  Add a structured `skip_reason_code`; duplicates also include `duplicate_of`, and other skips
  include `skip_evidence` or `review_note`.
- `failed`: The image could not be interpreted. Add `error`; this remains an uncovered frame.

Do not use model self-confidence as the only reliability signal. Prefer agreement among OCR, transcript context, neighboring frames, and visible source text.

Before building evidence segments, run:

```bash
python3 scripts/audit_visual_evidence.py "${BILI_NOTE_JOB_DIR}"
```

The audit rejects missing/duplicate rows, timestamp drift, fabricated manual provenance, and
unverified `not_needed` rows.
