"""Restored projects remain stopped until explicit single-host activation on this machine."""

import functools
import hashlib
import json
import os
import platform
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .config import IntegrityError, InvalidInput, contained
from .query_policy_store import write_private


@functools.lru_cache(maxsize=1)
def machine_id() -> str:
    if platform.system() == "Darwin":
        result = subprocess.run(
            ["/usr/sbin/ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        match = re.search(rb'"IOPlatformUUID"\s*=\s*"([A-Fa-f0-9-]{36})"', result.stdout)
        if match is None:
            raise InvalidInput("restore_machine_identity_unavailable")
        identity = match[1]
    else:
        path = Path("/etc/machine-id")
        if not path.is_file():
            raise InvalidInput("restore_machine_identity_unavailable")
        identity = path.read_bytes().strip()
    return hashlib.sha256(b"fiber-host/v1\0" + identity).hexdigest()


def read(root: Path) -> dict[str, Any] | None:
    path = contained(root, "restore-gate.json")
    if not path.exists():
        return None
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or info.st_size > 16384
            or stat.S_IMODE(info.st_mode) != 0o600
            or info.st_uid != os.getuid()
        ):
            raise IntegrityError("restore_gate_invalid")
        try:
            value = json.load(handle)
        except ValueError:
            raise IntegrityError("restore_gate_invalid") from None
    if not isinstance(value, dict) or value.get("schema") != "restore-gate/v1":
        raise IntegrityError("restore_gate_invalid")
    return value


def permit(root: Path) -> None:
    value = read(root)
    if value is None:
        return
    if value.get("state") != "active" or value.get("machine_id") != machine_id():
        raise InvalidInput("restore_requires_explicit_single_host_activation")


def save(root: Path, value: dict[str, Any]) -> None:
    write_private(
        contained(root, "restore-gate.json"),
        json.dumps({"schema": "restore-gate/v1", **value}).encode(),
    )
