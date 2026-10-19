"""Content-addressed private location snapshots; progress rows never duplicate large outputs."""

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Any

from .. import file_exchange
from ..config import IntegrityError, contained, make_directory
from ..objects import digest


def read(path: Path, expected: str) -> str:
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise IntegrityError("maintenance_undo_blob_unavailable")
        raw = stream.read()
    if digest(raw) != expected:
        raise IntegrityError("maintenance_undo_blob_unavailable")
    return raw.decode("utf-8")


def pack(config: Any, row: Any, *, persist: bool = False) -> Any:
    if row is None:
        return None
    row = dict(row)
    raw = row["value"].encode("utf-8")
    identity = digest(raw)
    if persist:
        home = contained(config.state_root, "maintenance-undo-blobs")
        make_directory(home)
        path = contained(home, identity)
        if path.exists():
            read(path, identity)
        else:
            handle, temporary = tempfile.mkstemp(prefix=".snapshot-", dir=home)
            try:
                with os.fdopen(handle, "wb") as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                parent = file_exchange.parent_fd(home, identity)
                try:
                    file_exchange.rename(
                        parent, Path(temporary).name, parent, identity, exchange=False
                    )
                finally:
                    os.close(parent)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    row["value"] = {"undo_blob_sha256": identity}
    return row


def unpack(config: Any, value: Any) -> str:
    if isinstance(value, str):
        return value
    identity = value.get("undo_blob_sha256") if isinstance(value, dict) else None
    if not isinstance(identity, str) or not re.fullmatch("[a-f0-9]{64}", identity):
        raise IntegrityError("maintenance_undo_blob_unavailable")
    path = contained(config.state_root, "maintenance-undo-blobs/" + identity)
    try:
        return read(path, identity)
    except (FileNotFoundError, UnicodeError) as error:
        raise IntegrityError("maintenance_undo_blob_unavailable") from error
