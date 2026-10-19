"""Small per-job progress files avoid rewriting the large observation/policy ledger."""

import json
import os
import stat
import time
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ..config import Config, contained
from . import store


def _path(config: Config, job_id: str) -> Path:
    identity = str(UUID(job_id))
    return contained(store.directory(config), "progress-" + identity + ".json")


def read(config: Config, job: dict[str, Any]) -> dict[str, Any]:
    try:
        path = _path(config, job["id"])
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536:
                return {}
            value = json.loads(stream.read(65537))
        if value["attempt"] != job["attempts"]:
            return {}
        result = {"progress": value["progress"], "stages": value["stages"]}
        if job["state"] == "running":
            result["phase"] = value["progress"]["phase"]
        return result
    except (FileNotFoundError, ValueError, KeyError, TypeError):
        return {}


class Reporter:
    def __init__(self, config: Config, job_id: str, attempt: int):
        self.path = _path(config, job_id)
        self.attempt = attempt
        self.phase = ""
        self.last_write = 0.0
        self.stages: dict[str, Any] = {}

    def __call__(self, value: dict[str, Any]) -> None:
        now = time.monotonic()
        phase = value["phase"]
        if phase == self.phase and not value.get("complete", False) and now - self.last_write < 1:
            return
        detail = {**value, "updated_at": time.time()}
        self.stages[phase] = detail
        wire = json.dumps({"attempt": self.attempt, "progress": detail, "stages": self.stages})
        temporary = self.path.with_name("progress-" + str(uuid4()) + ".tmp")
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(wire)
        os.replace(temporary, self.path)
        self.phase, self.last_write = phase, now
