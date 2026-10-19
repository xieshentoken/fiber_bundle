"""Fail closed on local placeholders and unresolved iCloud state; never request downloads."""

import ctypes
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from .config import IntegrityError, InvalidInput

# Apple sys/stat.h SF_DATALESS. Check each ancestor before stat of a child can materialize it.
SF_DATALESS = 0x40000000


def require_local(path: Path) -> None:
    # Explicitly deny materialization on this worker thread, including an eviction race
    # between metadata inspection and open(). Other threads are not reconfigured.
    if sys.platform == "darwin":
        library = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
        policy = library.setiopolicy_np
        policy.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_int]
        policy.restype = ctypes.c_int
        if policy(3, 1, 1) != 0:  # VFS_MATERIALIZE_DATALESS_FILES / THREAD / OFF
            raise InvalidInput("cloud_download_denial_unavailable")
    if not path.is_absolute():
        raise InvalidInput("cloud_path_invalid")
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        info = current.lstat()
        if stat.S_ISLNK(info.st_mode):
            raise IntegrityError("cloud_symlink_not_allowed")
        if getattr(info, "st_flags", 0) & SF_DATALESS or current.name.endswith(".icloud"):
            raise IntegrityError("cloud_item_not_downloaded")


def require_available(path: Path, *, tree: bool = False) -> None:
    require_local(path)
    if "Mobile Documents" not in path.parts:
        return
    helper = Path(os.environ.get("FIBER_CLOUD_HELPER", ""))
    expected = os.environ.get("FIBER_CLOUD_HELPER_SHA256", "")
    if (
        not helper.is_absolute()
        or helper.resolve() != helper
        or not helper.is_file()
        or len(expected) != 64
        or stat.S_IMODE(helper.stat().st_mode) & 0o022
    ):
        raise InvalidInput("cloud_status_helper_required")
    if hashlib.sha256(helper.read_bytes()).hexdigest() != expected:
        raise IntegrityError("cloud_status_helper_changed")
    try:
        result = subprocess.run(
            [str(helper), "--tree" if tree else "--file", str(path)],
            capture_output=True,
            timeout=20,
            check=True,
        )
        if len(result.stdout) > 4096:
            raise ValueError()
        value = json.loads(result.stdout)
        if (
            not isinstance(value, dict)
            or value.get("schema") != "cloud-status/v1"
            or value.get("available") is not True
        ):
            raise ValueError()
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IntegrityError("cloud_item_pending_stale_or_conflicted") from exc
