"""Reuse a prior chunk set only when the complete current file observation matches."""

import json
import os
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path

from . import note_index
from .config import IntegrityError, contained
from .inputs import canonical


def observation(root: Path, relative: str, maximum: int) -> str:
    from .cloud_files import require_available

    path = contained(root, relative)
    require_available(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > maximum:
            raise IntegrityError("note index source type or size")
        return canonical(
            [
                info.st_dev,
                info.st_ino,
                info.st_mode,
                info.st_nlink,
                info.st_size,
                info.st_mtime_ns,
                info.st_ctime_ns,
                getattr(info, "st_birthtime", None),
            ]
        )
    finally:
        os.close(fd)


def load(
    root: Path, relative: str, maximum: int, old: sqlite3.Connection | None, reused: set[str]
) -> note_index.Note:
    current = observation(root, relative, maximum)
    compatible = (
        old is not None
        and old.execute("SELECT 1 FROM sqlite_master WHERE name='observations'").fetchone()
    )
    if compatible and old is not None:
        row = old.execute(
            "SELECT n.metadata,o.file_stat FROM notes n "
            "JOIN observations o ON o.path=n.path WHERE n.path=?",
            (relative,),
        ).fetchone()
        if row is not None and row[1] == current:
            value = json.loads(row[0])
            value["file_key"] = tuple(value["file_key"]) if value["file_key"] is not None else None
            reused.add(relative)
            return replace(
                note_index.Note(**value),
                publication_id=None,
                publication_path=None,
                identity=None,
                text="",
            )
    return note_index.read_note(root, relative, maximum)
