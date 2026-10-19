"""Instance lease, atomic heartbeats, and fail-closed process-health inspection."""

import fcntl
import json
import os
import time
from pathlib import Path
from types import TracebackType
from typing import Any
from uuid import uuid4

from .config import InvalidInput, sync_directory


def write(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name("." + str(uuid4()) + ".tmp")
    try:
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


class Instance:
    def __init__(
        self, home: Path, account_id: str | None = None, *, platform: str = "feishu"
    ) -> None:
        self.home = home
        self.identity = str(uuid4())
        self.started = time.time()
        self.fd: int | None = None
        self.account_id = account_id
        self.platform = platform

    def __enter__(self) -> "Instance":
        fd = os.open(
            self.home / "fiber-instance.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
        )
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            raise InvalidInput("Feishu process instance already running") from None
        self.fd = fd
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps({"instance_id": self.identity, "pid": os.getpid()}).encode())
        os.fsync(fd)
        return self

    def status(
        self,
        state: str,
        budgets: dict[str, int],
        *,
        processing: str = "healthy",
        limits: dict[str, int] | None = None,
    ) -> None:
        write(
            self.home / "fiber-status.json",
            {
                "version": 2,
                "state": state,
                "pid": os.getpid(),
                "instance_id": self.identity,
                "started_at": self.started,
                "heartbeat_at": time.time(),
                "platform": self.platform,
                "account_id": self.account_id,
                "processing": processing,
                "download_remaining": budgets.get("download", 0),
                "store_remaining": budgets.get("store", 0),
                "model_remaining": budgets.get("model", 0),
                "model_limit": (limits or {}).get("model", 0),
                "publication_limit": 0,
                "outbound_remaining": budgets.get("outbound", 0),
                "outbound_limit": (limits or {}).get("outbound", 0),
            },
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


def inspect(home: Path, *, maximum_age: float = 6, platform: str = "feishu") -> dict[str, Any]:
    """Report lease and heartbeat separately; a stale heartbeat never proves readiness."""
    result: dict[str, Any] = {
        "state": "unavailable",
        "available": False,
        "platform": platform,
        "process_state": "unknown",
        "heartbeat_fresh": False,
        "observed_at": time.time(),
        "reason": "status_invalid",
    }
    try:
        fd = os.open(home / "fiber-instance.lock", os.O_RDWR | os.O_NOFOLLOW)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lease = json.loads(os.read(fd, 4096))
                if (
                    not isinstance(lease, dict)
                    or set(lease) != {"instance_id", "pid"}
                    or not isinstance(lease["instance_id"], str)
                    or type(lease["pid"]) is not int
                    or lease["pid"] <= 1
                ):
                    raise ValueError("invalid lease") from None
                result.update(lease, process_state="running", reason="heartbeat_missing")
            else:
                return {**result, "process_state": "stopped", "reason": "process_exited"}
        finally:
            os.close(fd)
        path = home / "fiber-status.json"
        if path.is_symlink() or path.stat().st_size > 4096:
            raise ValueError("invalid status file")
        status = json.loads(path.read_bytes())
        if (
            not isinstance(status, dict)
            or status.get("version") != 2
            or {key: status.get(key) for key in ("instance_id", "pid")} != lease
        ):
            return {**result, "reason": "instance_mismatch"}
        heartbeat = status.get("heartbeat_at")
        if not isinstance(heartbeat, (int, float)) or isinstance(heartbeat, bool):
            raise ValueError("invalid heartbeat")
        fields = (
            "version",
            "state",
            "pid",
            "instance_id",
            "started_at",
            "heartbeat_at",
            "account_id",
            "processing",
            "download_remaining",
            "store_remaining",
            "model_remaining",
            "model_limit",
            "outbound_remaining",
            "outbound_limit",
            "publication_limit",
        )
        result.update({key: status[key] for key in fields if key in status}, platform=platform)
        if not 0 <= time.time() - heartbeat <= maximum_age:
            return {**result, "available": False, "reason": "heartbeat_expired"}
        result["heartbeat_fresh"] = True
        if status["state"] != "connected" or status["processing"] != "healthy":
            return {**result, "reason": "transport_or_processing_unavailable"}
        return {**result, "available": True, "reason": None}
    except FileNotFoundError:
        return {
            **result,
            "process_state": result["process_state"] if "pid" in result else "stopped",
            "reason": "heartbeat_missing" if "pid" in result else "not_started",
        }
    except (OSError, ValueError, KeyError, TypeError):
        return {**result, "available": False, "reason": "status_invalid"}
