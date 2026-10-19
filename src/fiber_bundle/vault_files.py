"""Bounded allowed-source inventory and portable local attachment diagnostics."""

import hashlib
import posixpath
import stat
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from . import note_index, source_import_markdown, vault_scope
from .config import IntegrityError
from .query_routing_files import read


def inventory(root: Path, scope: vault_scope.Scope) -> dict[str, Any]:
    files: list[dict[str, Any]] = []
    bodies = {}
    total = 0
    for path, info in vault_scope.entries(root, scope):
        relative = path.relative_to(root).as_posix()
        if not stat.S_ISREG(info.st_mode) or not scope.allows(relative):
            continue
        if info.st_nlink != 1:
            raise IntegrityError("vault_file_alias")
        if info.st_size > 64 * 1024 * 1024:
            raise IntegrityError("vault_file_size_limit")
        total += info.st_size
        # One independent no-follow read, rechecked through its pathname.
        data = read(path, max(1, info.st_size), allow_empty=True)
        if len(data) != info.st_size:
            raise IntegrityError("vault_source_changed")
        files.append(
            {
                "path": relative,
                "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest(),
                "dev": info.st_dev,
                "ino": info.st_ino,
                "mtime_ns": info.st_mtime_ns,
            }
        )
        if path.suffix.lower() == ".md":
            if info.st_size > note_index.Limits().note_bytes:
                raise IntegrityError("vault_note_limit")
            bodies[relative] = data.decode("utf-8-sig")
    available = {f["path"] for f in files}
    warnings: list[dict[str, str]] = []
    warning_count = 0
    reference_count = 0
    for note, body in bodies.items():
        for reference in source_import_markdown.references(body):
            reference_count += 1
            if reference_count > 100000:
                raise IntegrityError("vault_reference_limit")
            try:
                parsed = urlsplit(reference)
            except ValueError:
                raise IntegrityError("vault_reference_invalid") from None
            if parsed.scheme or parsed.netloc:
                continue
            target = unquote(parsed.path).split("|", 1)[0].split("#", 1)[0]
            if not target:
                continue
            if target.startswith("/") or "\\" in target:
                candidates: list[str] = []
                reason = "outside_scope"
            else:
                adjacent = posixpath.normpath(posixpath.join(posixpath.dirname(note), target))
                if adjacent == ".." or adjacent.startswith("../"):
                    candidates = []
                    reason = "outside_scope"
                else:
                    targets = {adjacent, target}
                    if not posixpath.splitext(target)[1]:
                        targets |= {p + ".md" for p in targets}
                    candidates = sorted(p for p in targets if p in available)
                    if not candidates and "/" not in target:
                        candidates = sorted(
                            p
                            for p in available
                            if posixpath.basename(p) in {target, target + ".md"}
                        )
                    reason = "ambiguous" if len(candidates) > 1 else "missing_or_excluded"
            if len(candidates) != 1:
                warning_count += 1
                if len(warnings) < 200:
                    warnings.append({"note": note, "reference": reference, "reason": reason})
    return {
        "files": files,
        "notes": len(bodies),
        "attachments": len(files) - len(bodies),
        "bytes": total,
        "warnings": warnings[:200],
        "warning_count": warning_count,
    }
