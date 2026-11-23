"""Cheap local metadata versions for optimistic, read-only storage boundaries."""

import os
import stat
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .organization import store
from .storage_version_reads import names


def _files(directory: Path, *, recursive: bool = False) -> Iterator[tuple[Path, os.stat_result]]:
    # Only directory names can be reused. Every candidate file gets a new stat;
    # its contents, identity, permissions and timestamps remain part of the stamp.
    for name in names(directory):
        path = directory / name
        try:
            info = path.stat()
        except FileNotFoundError:
            continue  # DirEntry.is_file also skips vanished files and broken links.
        if stat.S_ISREG(info.st_mode):
            yield path, info
        elif recursive and stat.S_ISDIR(info.st_mode) and not path.is_symlink():
            yield from _files(path, recursive=True)


def stamp(config: Any, vault: str) -> Any:
    root = config.state_root
    paths = [
        (root / name, (root / name).stat())
        for name in names(root)
        if name.endswith((".sqlite3", ".sqlite3-wal", ".sqlite3-journal", ".json"))
        and not name.startswith(
            ("message-channels.sqlite3", "organization.sqlite3", "vault-sync.sqlite3")
        )
        and name not in {"backend.json"}
    ]
    for name in ("configs", "query-policies", "model-secrets"):
        directory = root / name
        if directory.exists():
            paths.extend(_files(directory))
    for name in ("moc-files", "moc-note-files"):
        directory = root / name
        if directory.exists():
            paths.extend(_files(directory, recursive=True))
    result: dict[str, Any] = {
        str(p): (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns) for p, s in paths
    }
    # Book discovery updates its display status independently of organization.
    # Semantic package/decision/job changes still invalidate the read, including
    # additions and deletions. Store writes always advance each row's `updated`.
    with store.connection(config) as db:
        if db is not None:
            info = (root / "organization.sqlite3").stat()
            result["organization_identity"] = (info.st_dev, info.st_ino)
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            result["organization"] = {
                table: [
                    tuple(r)
                    for r in db.execute(
                        f"SELECT id,updated FROM {table} WHERE vault=? ORDER BY id", (vault,)
                    )
                ]
                for table in sorted((store.TABLES & tables) - {"package_scans"})
            }
    return result
