"""Fresh child metadata beneath one physically pinned, locally verified directory."""

import errno
import os
import stat
from pathlib import Path

from .cloud_observation import ObservationError, directory_changed
from .config import IntegrityError
from .note_read_session import stat_signature

_FD_AVAILABLE = os.stat in os.supports_dir_fd and all(
    hasattr(os, name) for name in ("O_DIRECTORY", "O_NOFOLLOW", "O_NONBLOCK")
)


def _ancestry(parent: Path) -> None:
    from .cloud_files import require_local

    try:
        require_local(parent)
    except IntegrityError as error:
        if str(error) != "cloud_symlink_not_allowed":
            raise
        raise ObservationError(
            "cloud_symlink_not_allowed", parent, "scope_changed", changed=["ancestor"]
        ) from error


def check(parent: Path, names: tuple[str, ...]) -> None:
    from .cloud_files import require_local

    if _FD_AVAILABLE:
        try:
            _pinned(parent, names)
            return
        except NotImplementedError:
            pass
        except OSError as error:
            if error.errno not in {errno.ENOTSUP, errno.ENOSYS, errno.EINVAL}:
                raise
    # Preserve the original complete per-child ancestry checks when directory
    # descriptors are unsupported. Native cloud checks still run in the caller.
    for name in names:
        require_local(parent / name)


def _pinned(parent: Path, names: tuple[str, ...]) -> None:
    from .cloud_files import SF_DATALESS

    if not names:
        return
    # This also installs the thread's materialization-denial policy. Keep every
    # ancestor check, but bind all children to the same verified physical parent.
    _ancestry(parent)
    before = parent.lstat()
    if not stat.S_ISDIR(before.st_mode):
        raise IntegrityError("cloud_directory_required")
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        expected = stat_signature(before)
        actual = stat_signature(os.fstat(fd))
        if actual != expected:
            raise directory_changed(parent, expected, actual)
        for name in names:
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            path = parent / name
            if stat.S_ISLNK(info.st_mode):
                raise ObservationError(
                    "cloud_symlink_not_allowed", path, "scope_changed", changed=["symlink"]
                )
            if getattr(info, "st_flags", 0) & SF_DATALESS or name.endswith(".icloud"):
                raise ObservationError("cloud_item_not_downloaded", path, "pending")
        actual = stat_signature(os.fstat(fd))
        if actual != expected:
            raise directory_changed(parent, expected, actual)
        actual = stat_signature(parent.lstat())
        if actual != expected:
            raise directory_changed(parent, expected, actual)
        # A parent path's ancestors may have been replaced while the descriptor
        # remained valid. Refuse it before native cloud metadata or content reads.
        _ancestry(parent)
        actual = stat_signature(parent.lstat())
        if actual != expected:
            raise directory_changed(parent, expected, actual)
    finally:
        os.close(fd)
