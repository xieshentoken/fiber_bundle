"""Pinned local destination identity; never recreate a missing mount or traverse a source."""

import json
import os
from pathlib import Path
from typing import Any

from .. import database
from ..config import Config, InvalidInput, contained, sync_directory
from ..inputs import canonical


def identity(config: Config, text: str) -> dict[str, Any]:
    path = Path(text).expanduser()
    if not path.is_absolute() or path.resolve(strict=True) != path or not path.is_dir():
        raise InvalidInput("backup_target_invalid")
    # Config roots include detached history; they must not become recursive targets either.
    sources = [config.project_root, config.state_root, config.project_root / ".backup-automation"]
    sources.extend(config.vault_path(r) for _, r in config.vaults)
    with database.connect(config.state_root, readonly=True) as db:
        rows = [dict(r) for r in db.execute("SELECT * FROM vaults")]
    for row in rows:
        if row.get("absolute_root"):
            sources.append(Path(row["absolute_root"]))
    for source in sources:
        if path == source or source in path.parents or path in source.parents:
            raise InvalidInput("backup_destination_overlaps_source")
    info = path.stat()
    return {"path": str(path), "device": info.st_dev, "inode": info.st_ino}


def checked(config: Config, value: dict[str, Any]) -> Path:
    try:
        current = identity(config, value["path"])
    except OSError as exc:
        raise InvalidInput("backup_target_unavailable") from exc
    if current != value:
        raise InvalidInput("backup_target_identity_changed")
    return Path(value["path"])


def repository(config: Config, target: dict[str, Any], owner: str) -> Path:
    parent = checked(config, target)
    root = contained(parent, "FiberBundle-" + owner)
    marker = root / "owner.json"
    expected = {"schema": "fiber-backup-repository/v1", "owner": owner}
    if not root.exists():
        root.mkdir(mode=0o700)
        fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(canonical(expected))
            handle.flush()
            os.fsync(handle.fileno())
        sync_directory(root)
        sync_directory(parent)
    if marker.is_symlink() or json.loads(marker.read_text()) != expected:
        raise InvalidInput("backup_repository_not_owned")
    return root
