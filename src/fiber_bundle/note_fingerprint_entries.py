"""Equivalent metadata inputs for a fingerprint, with one native iCloud process."""

import os
import stat
from collections.abc import Iterator
from pathlib import Path

from . import cloud_files, vault_scope
from .config import IntegrityError

MARKERS = {".fiber-publication.json", ".fiber-analysis.json"}


def signature(info: os.stat_result) -> list[int]:
    return [
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    ]


def entries(root: Path, scope: vault_scope.Scope) -> Iterator[list[object]]:
    """Preserve the existing canonical order and every stat field; never cache a pass."""
    if "Mobile Documents" not in root.parts:
        for path, info in vault_scope.entries(
            root,
            scope,
            hidden=True,
            batch=True,
            file_filter=lambda path: path.name.lower().endswith(".md") or path.name in MARKERS,
        ):
            if not stat.S_ISDIR(info.st_mode):
                yield [path.relative_to(root).as_posix(), *signature(info)]
        return
    report = cloud_files.scoped_note_inventory(root, scope.wire())
    rows = report.get("files")
    root_signature = report.get("root")
    if (
        not isinstance(rows, list)
        or len(rows) > 10000
        or not isinstance(root_signature, list)
        or len(root_signature) != 7
        or any(type(value) is not int for value in root_signature)
        or signature(root.lstat()) != root_signature
    ):
        raise IntegrityError("cloud_item_pending_stale_or_conflicted")
    seen = set()
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) != 8
            or not isinstance(row[0], str)
            or not row[0]
            or row[0] in seen
            or vault_scope.prefix(row[0]) != row[0]
            or not scope.allows(row[0])
            or any(type(value) is not int for value in row[1:])
            or stat.S_ISDIR(row[3])
            or stat.S_ISLNK(row[3])
            or any(part.startswith(".") and part not in MARKERS for part in row[0].split("/"))
            or not (row[0].lower().endswith(".md") or Path(row[0]).name in MARKERS)
        ):
            raise IntegrityError("cloud_item_pending_stale_or_conflicted")
        # Check local identity again after the native status pass; no body is opened.
        path = root / row[0]
        cloud_files.require_local(path)
        if signature(path.lstat()) != row[1:]:
            raise IntegrityError("cloud_item_pending_stale_or_conflicted")
        seen.add(row[0])
    yield from rows
