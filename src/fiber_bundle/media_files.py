"""Private media snapshots with exclusive writes and bounded, nonredirected reads."""

import json
import os
import stat
from pathlib import Path
from typing import Any

from .config import InvalidInput, sync_directory

NAMES = {"source", "subtitle", "request.json", "result.json", "normalized.srt"}
MAX_RESULT_BYTES = 256 * 1024


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def decode(raw: bytes) -> dict[str, Any]:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise InvalidInput("media_duplicate_field")
            result[key] = value
        return result

    try:
        result = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs)
        if not isinstance(result, dict) or canonical(result) != raw:
            raise InvalidInput("media_json_contract")
        return result
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidInput("media_json_contract") from exc


def read(root: Path, name: str, maximum: int) -> bytes:
    if name not in NAMES or root.is_symlink() or root.resolve() != root:
        raise InvalidInput("media_file_scope")
    fd = os.open(root / name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or not (0 < before.st_size <= maximum)
        ):
            raise InvalidInput("media_file_type_or_size")
        data = stream.read(maximum + 1)
        after = os.fstat(stream.fileno())
        if len(data) != before.st_size or (before.st_size, before.st_mtime_ns) != (
            after.st_size,
            after.st_mtime_ns,
        ):
            raise InvalidInput("media_file_changed")
        return data


def write(root: Path, name: str, data: bytes) -> None:
    if name not in NAMES or root.is_symlink() or root.resolve() != root:
        raise InvalidInput("media_file_scope")
    fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(root)
