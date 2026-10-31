"""Pinned native FSEvents stream, decoded into durable scope-filtered work."""

import hashlib
import json
import os
import selectors
import subprocess
from pathlib import Path
from typing import Any

from ..config import Config, IntegrityError
from . import store


def helper() -> Path | None:
    value = os.environ.get("FIBER_EVENTS_HELPER")
    if not value:
        return None
    path = Path(value)
    if (
        not path.is_absolute()
        or path.is_symlink()
        or not path.is_file()
        or hashlib.sha256(path.read_bytes()).hexdigest()
        != os.environ.get("FIBER_EVENTS_HELPER_SHA256")
    ):
        raise IntegrityError("vault_sync_helper_changed")
    return path


class Stream:
    def __init__(self, config: Config, row: dict[str, Any], executable: Path) -> None:
        self.config, self.identity = config, row["id"]
        self.scope = row["scope"]
        self.child = subprocess.Popen(
            [str(executable), row["root"], row["cursor"]],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert self.child.stdin is not None and self.child.stdout is not None
        self.child.stdin.write(row["scope"].encode())
        self.child.stdin.close()
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.child.stdout, selectors.EVENT_READ)
        os.set_blocking(self.child.stdout.fileno(), False)
        self.buffer = b""

    def drain(self) -> None:
        if self.child.poll() is not None:
            raise IntegrityError("vault_sync_listener_stopped")
        for key, _ in self.selector.select(0):
            self.buffer += os.read(key.fd, 1024 * 1024)
            if len(self.buffer) > 4 * 1024 * 1024:
                raise IntegrityError("vault_sync_event_limit")
            while b"\n" in self.buffer:
                line, self.buffer = self.buffer.split(b"\n", 1)
                value = json.loads(line)
                if value.get("ready") is True:
                    store.enqueue(self.config, self.identity, [], value["cursor"])
                    continue
                events = value["events"]
                if not isinstance(events, list) or any(
                    not isinstance(e, list)
                    or len(e) != 2
                    or not isinstance(e[0], str)
                    or type(e[1]) is not int
                    or not 0 <= e[1] <= 31
                    for e in events
                ):
                    raise IntegrityError("vault_sync_event_invalid")
                store.enqueue(
                    self.config, self.identity, [(e[0], e[1]) for e in events], value["cursor"]
                )

    def close(self) -> None:
        self.selector.close()
        if self.child.poll() is None:
            self.child.terminate()
            try:
                self.child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.child.kill()
                self.child.wait(timeout=2)
        if self.child.stdout:
            self.child.stdout.close()
