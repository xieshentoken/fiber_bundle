"""Owned fixed-entry container boundary shared by offline media and compiler tooling."""

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from . import executor_container
from .config import InvalidInput


def run(
    root: Path,
    attempt: str,
    image: str,
    entry: str,
    timeout: int,
    guard: Callable[[], None],
    *,
    asr: dict[str, Any] | None = None,
    network: Literal["none", "bridge"] = "none",
    skill_mounts: list[str] | None = None,
) -> None:
    if entry not in {"video_capture", "video_link_entry", "expert_authoring_entry"}:
        raise InvalidInput("processing_tool_entry_invalid")
    command = executor_container.command(image, root, attempt, network=network)
    pos = command.index(image)
    command[pos:pos] = [
        "--entrypoint",
        "python",
        "--mount",
        "type=bind,src=" + str(Path(__file__).parent) + ",dst=/opt/fiber/fiber_bundle,readonly",
        *(skill_mounts or []),
    ]
    command[command.index("1g")] = str(asr["memory_mib"]) + "m" if asr else "1g"
    if asr:
        command[command.index("--cpus") + 1] = str(asr["cpus"])
    command[command.index("/tmp:rw,nosuid,nodev,size=128m,mode=1777")] = (
        "/tmp:rw,nosuid,nodev,size=256m,mode=1777"
    )
    command += ["-m", "fiber_bundle." + entry]
    deadline = time.monotonic() + timeout
    process = None
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        while process.poll() is None:
            guard()
            if time.monotonic() >= deadline:
                raise InvalidInput("video_segment_timeout")
            time.sleep(0.1)
        output, _ = process.communicate(timeout=5)
        if process.returncode:
            known = {
                "video_source_duration_mismatch",
                "video_transcription_capability_missing",
                "video_capture_failed",
                "video_capture_invalid",
            }
            code = output.decode(errors="replace").strip()
            if entry == "video_link_entry":
                from .media_link_failure import REASONS

                raise InvalidInput("video_link_" + (code if code in REASONS else "unclassified"))
            if entry == "expert_authoring_entry":
                from .processing_errors import MESSAGES

                raise InvalidInput(
                    code
                    if code.startswith("expert_authoring_") and code in MESSAGES
                    else "expert_authoring_tool_failed"
                )
            raise InvalidInput(code if code in known else "video_tool_failed")
    finally:
        executor_container.stop(attempt)
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
