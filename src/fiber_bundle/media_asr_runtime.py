"""Host-side allowlist for the actually tested, content-bound local ASR runtime."""

import json
from pathlib import Path
from typing import Any

from .config import InvalidInput


def require(resources: dict[str, Any]) -> None:
    lock = json.loads((Path(__file__).parent / "vendor/asr-runtime.json").read_bytes())
    if (
        lock.get("state") != "qualified"
        or resources["image"] != lock.get("image")
        or (
            resources["model_lock_sha256"] != lock.get("model_lock_sha256")
            or not resources["qualified"]
        )
    ):
        raise InvalidInput("media_processing_runtime_unqualified")
    if (
        resources["memory_mib"] < lock["minimum_memory_mib"]
        or resources["cpus"] < lock["minimum_cpus"]
    ):
        raise InvalidInput("media_processing_runtime_resources")
