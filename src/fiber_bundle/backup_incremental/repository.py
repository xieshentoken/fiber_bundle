"""A recovery-key namespace derives once, with independent per-object authenticated keys."""

import hashlib
import hmac
import json
import os
import stat
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..backup_crypto import Records
from ..config import IntegrityError, InvalidInput, contained, sync_directory
from ..inputs import canonical
from ..query_routing_files import read as bounded_read


def uuid(value: Any) -> str:
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise InvalidInput("backup_incremental_identity_invalid")
    return value


def digest(value: Any) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise InvalidInput("backup_incremental_identity_invalid")
    return value


def regular(path: Path) -> os.stat_result:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise InvalidInput("backup_repository_unexpected_content")
    return info


def private_file(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


class Repository:
    def __init__(self, root: Path, password: str, namespace: str | None = None):
        from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

        if not isinstance(password, str) or not 16 <= len(password.encode()) <= 1024:
            raise InvalidInput("backup_password_length")
        if root.resolve(strict=True) != root or not root.is_dir():
            raise InvalidInput("backup_bundle_path_invalid")
        self.root = root
        shared = contained(root, ".objects")
        if namespace is None:
            shared.mkdir(mode=0o700, exist_ok=True)
            namespace = str(uuid4())
            home = contained(shared, namespace)
            home.mkdir(mode=0o700)
            descriptor = {
                "schema": "fiber-objects/v2",
                "namespace": namespace,
                "salt": os.urandom(16).hex(),
            }
            private_file(home / "repository.json", canonical(descriptor).encode())
            sync_directory(home)
            sync_directory(shared)
        self.namespace = uuid(namespace)
        self.home = contained(shared, self.namespace)
        try:
            descriptor = json.loads(bounded_read(self.home / "repository.json", 4096))
            if descriptor["schema"] != "fiber-objects/v2" or descriptor["namespace"] != namespace:
                raise ValueError()
            salt = bytes.fromhex(descriptor["salt"])
            if len(salt) != 16:
                raise ValueError()
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise IntegrityError("backup_incremental_repository_invalid") from exc
        self.master = Scrypt(salt=salt, length=32, n=32768, r=8, p=1).derive(password.encode())

    def identity(self, sha: str, size: int) -> str:
        return hmac.digest(
            self.master, (digest(sha) + ":" + str(size)).encode(), hashlib.sha256
        ).hex()

    def path(self, identity: str) -> Path:
        return contained(self.home, digest(identity) + ".fbobj")

    def records(self, handle: Any, *, write: bool) -> Records:
        return Records(handle, "", write=write, master=self.master)
