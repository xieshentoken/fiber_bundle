# Output Publishing

## Natural-Language Configuration

Use caller-supplied path variables in examples. Never embed a personal vault name, vault structure, or machine-specific path in the distributed skill.

```bash
BILI_NOTE_JOB_DIR="./jobs/example"
: "${BILI_NOTE_NOTE_DIR:?set note directory}"
: "${BILI_NOTE_ASSET_DIR:?set attachment directory}"
```

Accept destination intent in ordinary language, then translate it into explicit script
arguments. Do not let a free-form path sentence directly drive file operations. Show the
resolved note directory, attachment directory, format, conflict policy, and cleanup policy
before publication when any value is ambiguous.

Example request:

> Use Obsidian mode. Use the note and attachment directories I provide. Delete downloaded media after a successful publish.

Configure the job:

```bash
python3 scripts/configure_output.py "${BILI_NOTE_JOB_DIR}" \
  --format obsidian \
  --note-dir "${BILI_NOTE_NOTE_DIR}" \
  --asset-dir "${BILI_NOTE_ASSET_DIR}" \
  --profile-name portable \
  --cleanup-media
```

Both Obsidian destinations must resolve inside the same vault containing `.obsidian`.
Published embeds use full vault-relative wikilinks, so moving the note inside Obsidian does not
depend on the internal job directory.

For portable Markdown:

```bash
python3 scripts/configure_output.py "${BILI_NOTE_JOB_DIR}" \
  --format markdown \
  --note-dir "${BILI_NOTE_NOTE_DIR}" \
  --asset-dir "${BILI_NOTE_ASSET_DIR}" \
  --cleanup-media
```

Portable output uses standard relative Markdown image links.

## Publish and Verify

`note/note.md` is the internal, evidence-linked draft. Publish only after `audit_job.py --stage
final` passes:

```bash
python3 scripts/publish_note.py "${BILI_NOTE_JOB_DIR}" --dry-run
python3 scripts/publish_note.py "${BILI_NOTE_JOB_DIR}"
python3 scripts/audit_published_note.py "${BILI_NOTE_JOB_DIR}"
```

For full-evidence jobs, the final audit must include a passing
`visual_evidence_integrity` check and `metadata/audit_visual.json` must pass. That audit binds
each crop to the frame-manifest timestamp, evidence segment, caption, and note anchor; a manual
or text-only visual result cannot authorize publication.

The publisher copies only locally referenced files from `illustrations/`, rewrites embeds,
adds Obsidian frontmatter when requested, and records destination hashes in
`output/publish_manifest.json`.

## Cleanup Safety

Source media (video or audio) and generated ASR audio are deleted by default only when all of these are true:

1. The file is registered as job-owned in `metadata/media_manifest.json`.
2. The file is a regular non-symlink file inside the job directory.
3. The internal final note audit passed.
4. The visual-evidence/illustration-binding audit passed (full-evidence jobs).
5. The published note and every copied illustration passed hash and embed validation.

Use `publish_note.py --keep-media` or configure `--keep-media` to retain them. A failed publish
or failed audit always retains media. Transcripts, terminology records, selected evidence
frames, cropped illustrations, coverage data, and publication manifests are retained.

For an authenticated browser handoff, register only a copied file inside the job:

```bash
python3 scripts/register_managed_media.py "${BILI_NOTE_JOB_DIR}" \
  --path source/video.mp4 --kind source_video --origin browser-handoff
```

Never register or delete the user's original external media path.
