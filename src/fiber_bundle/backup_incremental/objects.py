"""Immutable encrypted objects; unchanged source bytes and ciphertext are reused."""

import hashlib
import json
import os
import stat
from pathlib import Path
from typing import Any
from uuid import uuid4

from ..backup_bundle import file_sha
from ..backup_crypto import CHUNK
from ..backup_inventory import signature
from ..config import IntegrityError
from ..inputs import canonical
from .repository import Repository, regular


def check(
    repo: Repository, identity: str, entry: dict[str, Any], info: dict[str, Any], *, full: bool
) -> dict[str, Any]:
    path = repo.path(identity)
    try:
        current = regular(path)
    except FileNotFoundError as exc:
        raise IntegrityError("backup_incremental_object_missing") from exc
    if not full and info.get("signature") == list(signature(current)):
        return info
    if file_sha(path) != info["cipher_sha256"]:
        raise IntegrityError("backup_ciphertext_changed")
    with path.open("rb") as handle:
        reader = repo.records(handle, write=False)
        if json.loads(reader.get()) != {
            "id": identity,
            "bytes": entry["bytes"],
            "sha256": entry["sha256"],
        }:
            raise IntegrityError("backup_incremental_object_invalid")
        remaining, digest = entry["bytes"], hashlib.sha256()
        while remaining:
            value = reader.get()
            if len(value) != min(CHUNK, remaining):
                raise IntegrityError("restore_file_frame_invalid")
            digest.update(value)
            remaining -= len(value)
        if digest.hexdigest() != entry["sha256"]:
            raise IntegrityError("restore_file_hash_invalid")
        reader.finish(entry["sha256"])
    if signature(path.lstat()) != signature(current):
        raise IntegrityError("backup_ciphertext_changed")
    return {**info, "signature": list(signature(current))}


def save(
    repo: Repository, source: Path, entry: dict[str, Any], prior: dict[str, Any] | None
) -> tuple[str, dict[str, Any], bool]:
    identity = repo.identity(entry["sha256"], entry["bytes"])
    path = repo.path(identity)
    if path.exists():
        info = prior or {"cipher_sha256": file_sha(path)}
        return identity, check(repo, identity, entry, info, full=prior is None), False
    temporary = repo.home / (".pending-" + str(uuid4()))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        writer = repo.records(handle, write=True)
        writer.put(
            canonical({"id": identity, "bytes": entry["bytes"], "sha256": entry["sha256"]}).encode()
        )
        descriptor = os.open(source, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as original:
            before = os.fstat(original.fileno())
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_nlink != 1
                or before.st_size != entry["bytes"]
            ):
                raise IntegrityError("backup_source_changed")
            count, digest = 0, hashlib.sha256()
            while value := original.read(CHUNK):
                count += len(value)
                if count > entry["bytes"]:
                    raise IntegrityError("backup_source_changed")
                digest.update(value)
                writer.put(value)
            if (
                count != entry["bytes"]
                or digest.hexdigest() != entry["sha256"]
                or signature(before) != signature(os.fstat(original.fileno()))
            ):
                raise IntegrityError("backup_source_changed")
        writer.finish(entry["sha256"])
        handle.flush()
        os.fsync(handle.fileno())
    # The owning repository runner is exclusive. Never replace an existing object.
    if path.exists():
        raise IntegrityError("backup_repository_changed")
    temporary.rename(path)
    info = {"cipher_sha256": file_sha(path)}
    return identity, check(repo, identity, entry, info, full=True), True
