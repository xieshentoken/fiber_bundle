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
FILE_STATUS_TIMEOUT = 20
TREE_STATUS_TIMEOUT = 180


def _checked_status(raw: bytes) -> None:
    if len(raw) > 4096:
        raise ValueError()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema") != "cloud-status/v1":
        raise ValueError()
    if value.get("available") is not True:
        unknown = value.get("metadata_unavailable")
        if (
            type(unknown) is int
            and 0 < unknown <= 20000
            and value.get("errors") == unknown
            and all(
                value.get(key) == 0 for key in ("pending", "stale", "conflicts", "excluded_present")
            )
        ):
            raise IntegrityError("cloud_metadata_unavailable")
        raise ValueError()


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


def require_available(path: Path, *, tree: bool = False, exclude: tuple[str, ...] = ()) -> None:
    require_local(path)
    from . import display_observation as display

    observed = display.current.get() is not None and not tree
    if observed and display.reusable(path):
        return
    before = display.signature(path) if observed else None
    if "Mobile Documents" not in path.parts:
        if before is not None:
            display.remember(path, before)
        return
    helper = _helper()
    try:
        arguments = [str(helper), "--tree" if tree else "--file", str(path)]
        if exclude:
            from .vault_scope import prefix

            if not tree or any(prefix(value) != value or not value for value in exclude):
                raise InvalidInput("cloud_scope_invalid")
            arguments = [str(helper), "--tree-excluding", str(path), json.dumps(list(exclude))]
        result = subprocess.run(
            arguments,
            capture_output=True,
            timeout=TREE_STATUS_TIMEOUT if tree else FILE_STATUS_TIMEOUT,
            check=True,
        )
        _checked_status(result.stdout)
        if before is not None:
            display.remember(path, before)
    except subprocess.TimeoutExpired as exc:
        raise IntegrityError("cloud_status_check_timeout") from exc
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IntegrityError("cloud_item_pending_stale_or_conflicted") from exc


def _helper() -> Path:
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
    return helper


def require_available_many(parent: Path, names: tuple[str, ...]) -> None:
    """Check only already-authorized children, in bounded native metadata batches."""
    if any(
        not name or name in {".", ".."} or "/" in name or "\\" in name or "\0" in name
        for name in names
    ):
        raise InvalidInput("cloud_path_invalid")
    from . import display_observation as display

    if display.current.get() is not None:
        names = tuple(name for name in names if not display.reusable(parent / name))
    for name in names:
        require_local(parent / name)
    if not names or "Mobile Documents" not in parent.parts:
        return
    observations = (
        {parent / name: display.signature(parent / name) for name in names}
        if display.current.get() is not None
        else {}
    )
    helper = _helper()
    for offset in range(0, len(names), 256):
        payload = json.dumps(names[offset : offset + 256]).encode()
        if len(payload) > 262144:
            raise InvalidInput("cloud_scope_invalid")
        try:
            response = subprocess.run(
                [str(helper), "--items", str(parent)],
                input=payload,
                capture_output=True,
                timeout=TREE_STATUS_TIMEOUT,
                check=True,
            )
            _checked_status(response.stdout)
        except subprocess.TimeoutExpired as exc:
            raise IntegrityError("cloud_status_check_timeout") from exc
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise IntegrityError("cloud_item_pending_stale_or_conflicted") from exc

    for path, before in observations.items():
        display.remember(path, before)
