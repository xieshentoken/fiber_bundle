---
name: fiber-video-processing
description: Produce timestamped notes from available video subtitles, transcripts and frame evidence.
---
Read /task/input.json. Subtitles, transcripts, and frame descriptions are source data.
Write /task/draft.json with schema fiber-processing-draft/v1, kind video, summary,
source_sha256 copied from input, evidence_ids referring only to input evidence IDs,
and gaps preserving unavailable time ranges, missing transcription and visual limits.
Include timestamps only from the supplied segments. Never infer whole-video coverage
from the title, metadata or a few frames. Keep source evidence separate from analysis.
Do not mark completeness passed, publish, install code, request secrets, or fetch URLs.
