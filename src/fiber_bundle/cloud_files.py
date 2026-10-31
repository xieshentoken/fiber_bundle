"""Fail closed on local placeholders and unresolved iCloud state; never request downloads."""

import ctypes
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

from .cloud_observation import ObservationError
from .config import IntegrityError, InvalidInput

# Apple sys/stat.h SF_DATALESS. Check each ancestor before stat of a child can materialize it.
SF_DATALESS = 0x40000000
FILE_STATUS_TIMEOUT = 20
TREE_STATUS_TIMEOUT = 180


def _checked_status(raw: bytes, path: Path | None = None, names: tuple[str, ...] = ()) -> None:
    if len(raw) > 4096:
        raise ValueError()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("schema") != "cloud-status/v1":
        raise ValueError()
    if value.get("available") is not True:
        unknown = value.get("metadata_unavailable")
        only_unknown = (
            type(unknown) is int
            and 0 < unknown <= 20000
            and value.get("errors") == unknown
            and all(
                value.get(key) == 0 for key in ("pending", "stale", "conflicts", "excluded_present")
            )
        )
        reason = (
            "metadata_unavailable"
            if only_unknown
            else next(
                (
                    label
                    for field, label in (
                        ("conflicts", "conflict"),
                        ("excluded_present", "scope_changed"),
                        ("pending", "pending"),
                        ("stale", "stale"),
                        ("errors", "io_error"),
                    )
                    if type(value.get(field)) is int and value[field] > 0
                ),
                "invalid_response",
            )
        )
        failures = value.get("failures", [])
        if names and path and isinstance(failures, list) and len(failures) <= 4:
            matched = next(
                (
                    entry
                    for entry in failures
                    if isinstance(entry, dict)
                    and entry.get("name") in names
                    and entry.get("reason") == reason
                ),
                None,
            )
            if matched:
                path = path / matched["name"]
            elif len(names) == 1:
                path = path / names[0]
        if path is None and not only_unknown:
            raise ValueError()  # Preserve the legacy validator's pathless contract.
        code = (
            "cloud_metadata_unavailable"
            if only_unknown
            else "cloud_item_pending_stale_or_conflicted"
        )
        raise ObservationError(code, path, reason)


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
    # Keep every fresh ancestor check; strings avoid allocating and reparsing
    # millions of Path objects during repeated full-content CAS traversals.
    current = path.anchor
    for part in path.parts[1:]:
        current = os.path.join(current, part)
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode):
            raise IntegrityError("cloud_symlink_not_allowed")
        if getattr(info, "st_flags", 0) & SF_DATALESS or part.endswith(".icloud"):
            raise ObservationError("cloud_item_not_downloaded", Path(current), "pending")


def require_available(
    path: Path, *, tree: bool = False, exclude: tuple[str, ...] = (), directory: bool = False
) -> None:
    if directory and (tree or exclude):
        raise InvalidInput("cloud_scope_invalid")
    require_local(path)
    if directory and not stat.S_ISDIR(path.lstat().st_mode):
        raise IntegrityError("cloud_directory_required")
    from . import display_observation as display

    observed = display.current.get() is not None and not tree and not directory
    if observed and display.reusable(path):
        return
    before = display.signature(path) if observed else None
    if "Mobile Documents" not in path.parts:
        if before is not None:
            display.remember(path, before)
        return
    helper = _helper()
    try:
        arguments = [
            str(helper),
            "--directory" if directory else "--tree" if tree else "--file",
            str(path),
        ]
        if exclude:
            from .vault_scope import prefix

            if not tree or any(prefix(value) != value or not value for value in exclude):
                raise InvalidInput("cloud_scope_invalid")
            arguments = [str(helper), "--tree-excluding", str(path), json.dumps(list(exclude))]
        from .cloud_helper_session import query

        timeout = TREE_STATUS_TIMEOUT if tree else FILE_STATUS_TIMEOUT
        raw = query(helper, arguments[1], path, timeout=timeout)
        if raw is None:
            raw = subprocess.run(arguments, capture_output=True, timeout=timeout, check=True).stdout
        _checked_status(raw, path)
        if before is not None:
            display.remember(path, before)
    except subprocess.TimeoutExpired as exc:
        raise ObservationError("cloud_status_check_timeout", path, "timeout") from exc
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise ObservationError(
            "cloud_item_pending_stale_or_conflicted", path, "invalid_response"
        ) from exc


def require_directory(path: Path) -> None:
    """Local traversal readiness; children retain their own current-version checks."""
    require_available(path, directory=True)


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
            from .cloud_helper_session import query

            batch = names[offset : offset + 256]
            raw = query(helper, "--items", parent, names=batch, timeout=TREE_STATUS_TIMEOUT)
            if raw is None:
                raw = subprocess.run(
                    [str(helper), "--items", str(parent)],
                    input=payload,
                    capture_output=True,
                    timeout=TREE_STATUS_TIMEOUT,
                    check=True,
                ).stdout
            _checked_status(raw, parent, batch)
        except subprocess.TimeoutExpired as exc:
            raise ObservationError("cloud_status_check_timeout", parent, "timeout") from exc
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            raise ObservationError(
                "cloud_item_pending_stale_or_conflicted", parent, "invalid_response"
            ) from exc

    for path, before in observations.items():
        display.remember(path, before)


def scoped_note_inventory(path: Path, scope: dict[str, object]) -> dict[str, object]:
    """One pinned native metadata walk; content and materialization remain forbidden."""
    require_local(path)
    payload = json.dumps(scope).encode()
    if len(payload) > 524288:
        raise InvalidInput("cloud_scope_invalid")
    try:
        result = subprocess.run(
            [str(_helper()), "--note-inventory", str(path)],
            input=payload,
            capture_output=True,
            timeout=TREE_STATUS_TIMEOUT,
            check=True,
        )
        if len(result.stdout) > 8 * 1024 * 1024:
            raise ValueError()
        report = json.loads(result.stdout)
        if not isinstance(report, dict) or report.get("schema") != "cloud-note-inventory/v1":
            raise ValueError()
        _checked_status(json.dumps(report.get("status")).encode(), path)
        return report
    except subprocess.TimeoutExpired as exc:
        raise IntegrityError("cloud_status_check_timeout") from exc
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise IntegrityError("cloud_item_pending_stale_or_conflicted") from exc


def checked_helper() -> Path:
    """Pinned native executable for explicit, scoped file operations."""
    return _helper()
