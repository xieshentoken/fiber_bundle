"""Cross-database write reservation plus existing file stability checks; no process stops."""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .. import database
from ..backup_inventory import Limits
from ..config import Config, InvalidInput, contained


@contextmanager
def _reservations(config: Config, limits: Limits) -> Iterator[None]:
    import json
    import os
    import selectors
    import subprocess
    import sys

    from ..backup_preflight import DATABASES

    # Raw inventory readers must not share a process with SQLite's POSIX locks.
    # The pipe-owned child keeps all reservations until the frozen context ends.
    names = list(DATABASES)
    index = contained(config.state_root, "note-search")
    if index.is_dir():
        names.extend(p.relative_to(config.state_root).as_posix() for p in index.glob("*.sqlite3"))
    if len(names) > limits.entries:
        raise InvalidInput("backup_entry_limit")
    env = {
        **os.environ,
        "PYTHONPATH": os.pathsep.join(
            filter(
                None,
                [str(Path(__file__).resolve().parents[2]), os.environ.get("PYTHONPATH", "")],
            )
        ),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    child = subprocess.Popen(
        [sys.executable, "-B", "-s", "-m", "fiber_bundle.backup_automation.database_lease"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        env=env,
        close_fds=True,
    )
    assert child.stdin is not None and child.stdout is not None
    try:
        child.stdin.write(
            json.dumps({"root": str(config.state_root), "names": sorted(names)}).encode() + b"\n"
        )
        child.stdin.flush()
        with selectors.DefaultSelector() as selector:
            selector.register(child.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=15):
                raise InvalidInput("backup_database_busy")
        if child.stdout.readline(100) != b'"ready"\n' or child.poll() is not None:
            raise InvalidInput("backup_database_busy")
        yield
        if child.poll() is not None:
            raise InvalidInput("backup_database_busy")
    finally:
        child.stdin.close()
        child.stdout.close()
        try:
            child.wait(timeout=5)
        except subprocess.TimeoutExpired:
            child.terminate()
            child.wait(timeout=5)


@contextmanager
def lease(config: Config, limits: Limits) -> Iterator[None]:
    with database.lock_wait(2), database.locked(config), _reservations(config, limits):
        yield


@contextmanager
def snapshots(config: Config, limits: Limits) -> Iterator[dict[str, Path]]:
    """Reserve databases only while capturing independent local copies.

    The global store lease still spans the complete snapshot. Transport writers
    may resume after this short capture; encrypted readers use the fixed copies.
    APFS clones do not copy data blocks or expose plaintext outside the private
    OS temporary directory. Other hosts use a bounded regular-file copy.
    """
    import ctypes
    import os
    import stat
    import sys
    import tempfile
    import time

    from ..backup_inventory import signature
    from ..backup_preflight import DATABASES
    from ..backup_progress import SourceChanged

    local_temp = Path(tempfile.gettempdir()).resolve(strict=True)
    with (
        database.lock_wait(2),
        database.locked(config),
        tempfile.TemporaryDirectory(prefix="fiber-database-snapshot-", dir=local_temp) as temporary,
    ):
        copies: dict[str, Path] = {}
        identities = {}
        with _reservations(config, limits):
            deadline = time.monotonic() + 2
            names = list(DATABASES)
            index = config.state_root / "note-search"
            if index.exists():
                names.extend(
                    p.relative_to(config.state_root).as_posix() for p in index.glob("*.sqlite3")
                )
            for name in sorted(names):
                source = contained(config.state_root, name)
                if not source.exists():
                    continue
                before = signature(source.lstat())
                target = contained(Path(temporary), name)
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                cloned = False
                if sys.platform == "darwin":
                    clone = ctypes.CDLL(None, use_errno=True).clonefile
                    clone.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
                    clone.restype = ctypes.c_int
                    cloned = clone(os.fsencode(source), os.fsencode(target), 0) == 0
                if not cloned:
                    # Bound non-APFS capture too; never hold receiver writers
                    # for the duration of scanning/encryption/verification.
                    with source.open("rb") as src, target.open("xb") as dst:
                        for block in iter(lambda: src.read(1024**2), b""):
                            if time.monotonic() > deadline:
                                raise InvalidInput("backup_database_busy")
                            dst.write(block)
                target.chmod(0o600)
                after = signature(source.lstat())
                if before[:6] != after[:6]:
                    raise SourceChanged("state", name)
                info = target.lstat()
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_nlink != 1
                    or (info.st_dev, info.st_ino) == before[:2]
                ):
                    raise InvalidInput("backup_database_busy")
                identities[name] = after[:4]
                copies[name] = target
                if time.monotonic() > deadline:
                    raise InvalidInput("backup_database_busy")
        yield copies
        for name, before in identities.items():
            if signature(contained(config.state_root, name).lstat())[:4] != before:
                raise SourceChanged("state", name)


def restored_journals(config: Config) -> None:
    """Only called after authentication into a fresh, stopped destination."""
    from ..backup_preflight import DATABASES

    names = list(DATABASES)
    root = contained(config.state_root, "note-search")
    if root.is_dir():
        names.extend(p.relative_to(config.state_root).as_posix() for p in root.glob("*.sqlite3"))
    for name in names:
        path = contained(config.state_root, name)
        if path.exists():
            with sqlite3.connect(path, isolation_level=None) as db:
                db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                if db.execute("PRAGMA journal_mode=DELETE").fetchone()[0] != "delete":
                    raise InvalidInput("backup_database_busy")


def active(config: Config) -> bool:
    """A native executor lease, not a possibly stale JSON state, bounds HTTP waits."""
    import fcntl
    import os
    import stat

    from .store import directory

    path = contained(directory(config), "runner.lock")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            return True
    finally:
        os.close(fd)
