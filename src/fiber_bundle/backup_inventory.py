"""Bounded, no-follow file inventory for an observed offline backup scope."""

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .backup_progress import Progress, SourceChanged, report
from .config import InvalidInput, contained
from .vault_scope import Scope


@dataclass(frozen=True)
class Limits:
    entries: int = 20000
    file_bytes: int = 256 * 1024 * 1024
    total_bytes: int = 2 * 1024 * 1024 * 1024
    depth: int = 32

    def validate(self) -> None:
        if any(type(v) is not int or v < 1 for v in vars(self).values()):
            raise InvalidInput("backup_limits_invalid")


def signature(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_mode,
        info.st_nlink,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


class Inventory:
    """One budget across all roots; contents are hashed, never returned or logged."""

    def __init__(
        self,
        limits: Limits,
        cache: dict[str, Any] | None = None,
        *,
        metadata_only: bool = False,
        progress: Progress | None = None,
        state_overrides: dict[str, Path] | None = None,
    ):
        limits.validate()
        self.limits = limits
        self.entries: list[dict[str, Any]] = []
        self.observed: dict[Path, tuple[int, ...]] = {}
        self.byte_count = 0
        self.cache = cache or {}
        self.captured: dict[str, Any] = {}
        self.hashed_bytes = 0
        self.reused_bytes = 0
        self.state_overrides = state_overrides or {}
        self.metadata_only = metadata_only
        self.progress = progress
        self.file_count = 0
        self.file_records: dict[Path, dict[str, Any]] = {}
        self.directory_children: dict[Path, tuple[str, ...]] = {}
        self.directory_filters: dict[Path, tuple[Path, str, frozenset[str], Scope | None]] = {}

    def tree(
        self,
        root: Path,
        section: str,
        *,
        skip: frozenset[str] = frozenset(),
        scope: Scope | None = None,
    ) -> None:
        self._visit(root, root, "", section, skip, scope)

    def file(self, root: Path, relative: str, section: str) -> None:
        path = contained(root, relative)
        if not path.is_file():
            raise InvalidInput("backup_configuration_file_invalid")
        self._visit(root, path, relative, section, frozenset(), None)

    def directory(self, root: Path, relative: str, section: str) -> None:
        """Include one explicitly scoped project resource directory, retaining its prefix."""
        path = contained(root, relative)
        if not path.is_dir():
            raise InvalidInput("backup_configuration_file_invalid")
        self._visit(root, path, relative, section, frozenset(), None)

    def _visit(
        self,
        root: Path,
        path: Path,
        relative: str,
        section: str,
        skip: frozenset[str],
        scope: Scope | None,
    ) -> None:
        if relative in skip or (scope is not None and not scope.reachable(relative)):
            return
        if len(self.entries) >= self.limits.entries:
            raise InvalidInput("backup_entry_limit")
        if len(Path(relative).parts) > self.limits.depth:
            raise InvalidInput("backup_depth_limit")
        if relative:
            path = (
                self.state_overrides.get(relative, contained(root, relative))
                if section == "state"
                else contained(root, relative)
            )
        from .cloud_files import require_local

        require_local(path)
        info = path.lstat()
        record: dict[str, Any] = {"section": section, "path": relative, "kind": "directory"}
        if stat.S_ISDIR(info.st_mode):
            self.entries.append(record)
            self.observed[path] = signature(info)
            with os.scandir(path) as children:
                names = []
                for child in children:
                    child_relative = f"{relative}/{child.name}".lstrip("/")
                    if child_relative in skip or (
                        scope is not None and not scope.reachable(child_relative)
                    ):
                        continue
                    names.append(child.name)
                    if len(self.entries) + len(names) > self.limits.entries:
                        raise InvalidInput("backup_entry_limit")
                names.sort()
            self.directory_children[path] = tuple(names)
            self.directory_filters[path] = (root, relative, skip, scope)
            for name in names:
                self._visit(
                    root, path / name, f"{relative}/{name}".lstrip("/"), section, skip, scope
                )
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            if scope is not None and not scope.allows(relative):
                return
            if info.st_size > self.limits.file_bytes:
                raise InvalidInput("backup_file_limit")
            self.file_count += 1
            self.byte_count += info.st_size
            report(
                self.progress,
                "capacity" if self.metadata_only else "scanning",
                files_done=self.file_count,
                bytes_done=self.byte_count,
            )
            if self.byte_count > self.limits.total_bytes:
                raise InvalidInput("backup_total_limit")
            if self.metadata_only:
                self.entries.append({**record, "kind": "file", "bytes": info.st_size})
                return
            saved = self.cache.get(str(path))
            if (
                isinstance(saved, dict)
                and saved.get("signature") == list(signature(info))
                and isinstance(saved.get("entry"), dict)
                and all(
                    saved["entry"].get(k) == v
                    for k, v in {**record, "kind": "file", "bytes": info.st_size}.items()
                )
                and isinstance(saved["entry"].get("sha256"), str)
                and len(saved["entry"]["sha256"]) == 64
                and all(c in "0123456789abcdef" for c in saved["entry"]["sha256"])
            ):
                self.entries.append(dict(saved["entry"]))
                self.file_records[path] = self.entries[-1]
                self.observed[path] = signature(info)
                self.captured[str(path)] = saved
                self.reused_bytes += info.st_size
                return
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            with os.fdopen(fd, "rb") as handle:
                if signature(os.fstat(handle.fileno())) != signature(info):
                    raise SourceChanged(section, relative)
                digest = hashlib.sha256()
                count = 0
                while data := handle.read(256 * 1024):
                    count += len(data)
                    if count > info.st_size:
                        raise SourceChanged(section, relative)
                    digest.update(data)
                if count != info.st_size or signature(os.fstat(handle.fileno())) != signature(info):
                    raise SourceChanged(section, relative)
            self.entries.append(
                {**record, "kind": "file", "bytes": count, "sha256": digest.hexdigest()}
            )
            self.file_records[path] = self.entries[-1]
            self.observed[path] = signature(info)
            self.hashed_bytes += count
            self.captured[str(path)] = {
                "signature": list(signature(info)),
                "entry": self.entries[-1],
            }
        else:
            raise InvalidInput("backup_requires_independent_regular_files")

    def unchanged(self) -> None:
        for path, expected in self.observed.items():
            actual = signature(path.lstat())
            if actual == expected:
                continue
            entry = self.file_records.get(path)
            if entry is not None:
                valid = actual[:5] == expected[:5]
                digest = hashlib.sha256()
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as handle:
                    valid = valid and signature(os.fstat(handle.fileno()))[:5] == expected[:5]
                    for block in iter(lambda: handle.read(256 * 1024), b""):
                        digest.update(block)
                    after = signature(os.fstat(handle.fileno()))
                if not valid or after[:5] != expected[:5] or digest.hexdigest() != entry["sha256"]:
                    raise SourceChanged(entry["section"], entry["path"])
                self.observed[path] = after
                self.captured[str(path)]["signature"] = list(after)
                continue
            root, relative, skip, scope = self.directory_filters[path]
            names = []
            with os.scandir(path) as children:
                for child in children:
                    child_relative = f"{relative}/{child.name}".lstrip("/")
                    if child_relative in skip or (
                        scope is not None and not scope.reachable(child_relative)
                    ):
                        continue
                    names.append(child.name)
            # Size and timestamps can change when excluded SQLite journals appear.
            # Device/inode/type/permissions and the included entry set must agree.
            # APFS directory link counts also change for excluded entries.
            if actual[:3] != expected[:3] or tuple(sorted(names)) != self.directory_children[path]:
                raise SourceChanged(
                    "state" if root.name in {".local-state", ".state"} else "vault", relative
                )
