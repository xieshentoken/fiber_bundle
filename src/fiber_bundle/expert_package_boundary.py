"""Read-only package boundaries. Never traverse or execute a detected Skill as notes."""

import json
import os
import stat
from pathlib import Path

from . import file_exchange
from .config import StorageError
from .publication_file_io import read
from .vault_scope import Scope


def metadata(root: Path, relative: str) -> os.stat_result | None:
    """Pin ancestors and never follow a manifest/Skill symlink just to detect a boundary."""
    try:
        fd = file_exchange.parent_fd(root, relative)
        try:
            return os.stat(relative.rsplit("/", 1)[-1], dir_fd=fd, follow_symlinks=False)
        finally:
            os.close(fd)
    except FileNotFoundError:
        return None


def detected(root: Path, relative: str, scope: Scope) -> bool:
    marker, skill = relative + "/manifest.json", relative + "/SKILL.md"
    if not scope.allows(marker):
        return False
    info = metadata(root, marker)
    if info and stat.S_ISLNK(info.st_mode):
        return True  # Quarantine an unsafe manifest rather than indexing its payload.
    structural = scope.allows(skill) and metadata(root, skill) is not None
    if not info or not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
        return structural
    # A partial/corrupt copied Skill still must not become hundreds of ordinary notes.
    try:
        value = json.loads(read(root, marker)[0])
        return structural or str(value.get("schema_version", "")).startswith("tkc.expert-skill/")
    except (OSError, StorageError, ValueError, AttributeError, RecursionError):
        return structural
