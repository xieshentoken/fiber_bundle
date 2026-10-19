"""Authenticate every encrypted record without writing plaintext or activating restored data."""

import hashlib
import json
from pathlib import Path

from ..backup_bundle import layout, section_entries
from ..backup_crypto import CHUNK, Records
from ..backup_progress import Progress, report
from ..config import IntegrityError


def verify(
    bundle: Path, password: str, *, full: bool = True, progress: Progress | None = None
) -> None:
    marker = layout(bundle)
    if marker["schema"] == "encrypted-incremental/v2":
        from ..backup_incremental.snapshot import verify as verify_incremental

        verify_incremental(bundle, password, full=full, progress=progress)
        return
    first = None
    done_files = done_bytes = 0
    report(progress, "verifying", files_done=0, bytes_done=0)
    for part in ("state", "vaults"):
        with (bundle / (part + ".fbenc")).open("rb") as handle:
            reader = Records(handle, password, write=False)
            header = reader.get()
            if first is not None and header != first:
                raise IntegrityError("restore_sections_do_not_match")
            first = header
            manifest = json.loads(header)
            if (
                manifest["bundle_id"] != marker["bundle_id"]
                or manifest["sha256"] != marker["scope_sha256"]
            ):
                raise IntegrityError("backup_marker_invalid")
            total_files = sum(e["kind"] == "file" for e in manifest["entries"])
            total_bytes = manifest["file_bytes"]
            for entry in section_entries(manifest, part):
                if entry["kind"] != "file":
                    continue
                remaining = entry["bytes"]
                digest = hashlib.sha256()
                while remaining:
                    value = reader.get()
                    if len(value) != min(CHUNK, remaining):
                        raise IntegrityError("restore_file_frame_invalid")
                    digest.update(value)
                    remaining -= len(value)
                if digest.hexdigest() != entry["sha256"]:
                    raise IntegrityError("restore_file_hash_invalid")
                done_files += 1
                done_bytes += entry["bytes"]
                report(
                    progress,
                    "verifying",
                    files_done=done_files,
                    files_total=total_files,
                    bytes_done=done_bytes,
                    bytes_total=total_bytes,
                )
            reader.finish(manifest["sha256"])
    report(
        progress,
        "verifying",
        files_done=done_files,
        files_total=total_files,
        bytes_done=done_bytes,
        bytes_total=total_bytes,
        complete=True,
    )
