"""Current cloud metadata shared only across one read-only inventory traversal."""

from collections import defaultdict
from pathlib import Path

from . import cloud_files
from .cloud_observation import changed
from .config import IntegrityError
from .note_fingerprint_entries import signature


class CloudReadBatch:
    """Read bodies normally; batch native status checks and verify every observed file again."""

    def __init__(self, root: Path):
        self.root = root
        self.observed: dict[Path, list[int]] = {}

    def preflight(self, parent: Path, paths: list[Path]) -> None:
        cloud_files.require_local(parent)
        before = {path: signature(path.lstat()) for path in paths}
        cloud_files.require_available_many(parent, tuple(path.name for path in paths))
        for path, value in before.items():
            after = signature(path.lstat())
            if after != value:
                raise changed(path, value, after)
            self.observed[path] = value

    def check(self, path: Path) -> None:
        if not path.is_relative_to(self.root):
            raise IntegrityError("cloud_item_pending_stale_or_conflicted")
        cloud_files.require_local(path)
        if path not in self.observed:
            before = signature(path.lstat())
            cloud_files.require_available(path)
            after = signature(path.lstat())
            if after != before:
                raise changed(path, before, after)
            self.observed[path] = before
        after = signature(path.lstat())
        if after != self.observed[path]:
            raise changed(path, self.observed[path], after)

    def verify(self) -> None:
        grouped: dict[Path, list[Path]] = defaultdict(list)
        for path in self.observed:
            grouped[path.parent].append(path)
        for parent, paths in grouped.items():
            cloud_files.require_available_many(parent, tuple(path.name for path in paths))
            for path in paths:
                after = signature(path.lstat())
                if after != self.observed[path]:
                    raise changed(path, self.observed[path], after)
