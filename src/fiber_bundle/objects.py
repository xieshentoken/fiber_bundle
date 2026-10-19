"""Immutable object bytes. No database or user-supplied destination paths."""

import hashlib
import os
import re
import stat
from pathlib import Path
from uuid import UUID, uuid4

from fiber_bundle.config import (
    IntegrityError,
    InvalidInput,
    contained,
    make_directory,
    sync_directory,
)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def object_relative(vault_id: str, sha256: str) -> str:
    try:
        if str(UUID(vault_id)) != vault_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise IntegrityError("invalid object scope") from exc
    if not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise IntegrityError("invalid object digest")
    return f"objects/{vault_id}/{sha256[:2]}/{sha256}"


def read_object(root: Path, relative: str, expected_sha: str, expected_size: int) -> bytes:
    path = contained(root, relative)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
            raise IntegrityError("object type or length mismatch")
        data = handle.read()
    if digest(data) != expected_sha:
        raise IntegrityError("object hash mismatch")
    return data


def _write_bytes(fd: int, data: bytes) -> None:
    """Separate seam for write-failure and process-crash tests."""
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def put_object(root: Path, vault_id: str, data: bytes) -> str:
    if not isinstance(data, bytes):
        raise InvalidInput("object data must be bytes")
    sha = digest(data)
    relative = object_relative(vault_id, sha)
    destination = contained(root, relative)
    if destination.exists():
        read_object(root, relative, sha, len(data))
        return relative
    make_directory(destination.parent)
    staging = contained(root, f"staging/{uuid4()}.part")
    fd = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    # Failure deliberately preserves any staged bytes for explicit reconciliation.
    _write_bytes(fd, data)
    sync_directory(staging.parent)
    try:
        os.link(staging, destination, follow_symlinks=False)
    except FileExistsError:
        read_object(root, relative, sha, len(data))
    sync_directory(destination.parent)
    staging.unlink()
    sync_directory(staging.parent)
    return relative
