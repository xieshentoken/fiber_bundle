"""Cross-process single execution slot; queued interactions precede heavy tasks.

Admission has no model effects. Owner-held ticket locks distinguish live waiters from
crashed processes without trusting PIDs. Existing task ledgers still forbid replay.
"""

import fcntl
import json
import math
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from .config import Config, InvalidInput, contained, make_directory

Priority = Literal["interactive", "heavy"]


def _root(config: Config) -> Path:
    path = contained(config.state_root, "execution-queue")
    try:
        make_directory(path)
    except FileExistsError:
        if path.is_symlink() or not path.is_dir():
            raise InvalidInput("execution_queue_invalid") from None
    return path


@contextmanager
def _queue_lock(root: Path) -> Iterator[None]:
    fd = os.open(contained(root, "queue.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _waiting(root: Path, own: str | None = None) -> list[tuple[int, float, str]]:
    result = []
    for path in root.glob("*.ticket"):
        if str(UUID(path.stem)) != path.stem:
            raise InvalidInput("execution_queue_invalid")
        fd = os.open(contained(root, path.name), os.O_RDWR | os.O_NOFOLLOW)
        try:
            if path.stem != own:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    pass
                else:
                    path.unlink()  # Abandoned admission, never a model attempt.
                    continue
            wire = os.read(fd, 1024)
            item = json.loads(wire)
            if (
                item["priority"] not in ("interactive", "heavy")
                or type(item["time"]) not in (int, float)
                or not math.isfinite(item["time"])
                or item["time"] <= 0
            ):
                raise InvalidInput("execution_queue_invalid")
            result.append((0 if item["priority"] == "interactive" else 1, item["time"], path.stem))
        finally:
            os.close(fd)
    return sorted(result)


def snapshot(config: Config) -> dict[str, int]:
    root = contained(config.state_root, "execution-queue")
    if not root.exists():
        return {"interactive_waiting": 0, "heavy_waiting": 0}
    with _queue_lock(root):
        rows = _waiting(root)
    return {
        "interactive_waiting": sum(x[0] == 0 for x in rows),
        "heavy_waiting": sum(x[0] == 1 for x in rows),
    }


@contextmanager
def admit(config: Config, priority: Priority, *, timeout: float = 600) -> Iterator[None]:
    if priority not in ("interactive", "heavy") or not 0 < timeout <= 600:
        raise InvalidInput("execution_admission_invalid")
    root = _root(config)
    identity = str(uuid4())
    path = contained(root, identity + ".ticket")
    fd = -1
    slot = os.open(contained(root, "slot.lock"), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        with _queue_lock(root):
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, json.dumps({"priority": priority, "time": time.time()}).encode())
        deadline = time.monotonic() + timeout
        while True:
            with _queue_lock(root):
                rows = _waiting(root, identity)
                if rows and rows[0][2] == identity:
                    try:
                        fcntl.flock(slot, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        pass
                    else:
                        path.unlink()
                        break
            if time.monotonic() >= deadline:
                raise InvalidInput("execution_queue_timeout")
            time.sleep(0.05)
        yield  # Native executors recheck authorization, cancellation, inputs and budget.
    finally:
        with _queue_lock(root):
            path.unlink(missing_ok=True)
        os.close(slot)
        if fd >= 0:
            os.close(fd)
