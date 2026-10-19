"""One offline processing container per parent task; guarded timeout/cancellation and cleanup."""

import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import Config, InvalidInput, contained, make_directory
from .media_files import canonical
from .media_processing_capture import verify
from .media_processing_contract import plan
from .media_processing_files import write


def directory(config: Config, job: str) -> Path:
    try:
        if str(UUID(job)) != job:
            raise ValueError()
    except ValueError as e:
        raise InvalidInput("media_processing_task_id") from e
    return contained(config.state_root, "media-processing/" + job)


def command(spec: dict[str, Any], inputs: Path, outputs: Path, job: str) -> list[str]:
    p = plan(spec)["policy"]
    return [
        "docker",
        "create",
        "--pull",
        "never",
        "--name",
        "fiber-processing-" + job,
        "--label",
        "fiber-bundle.processing=" + job,
        "--network",
        "none",
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--log-driver",
        "none",
        "--memory",
        str(p["memory_mib"]) + "m",
        "--memory-swap",
        str(p["memory_mib"]) + "m",
        "--cpus",
        str(p["cpus"]),
        "--pids-limit",
        "64",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--mount",
        "type=bind,src=" + str(inputs) + ",dst=/media/input,readonly",
        "--mount",
        "type=bind,src=" + str(outputs) + ",dst=/media/output",
        "--entrypoint",
        "python",
        p["image"],
        "-m",
        "fiber_bundle.media_processing_entry",
    ]


def stop_owned(config: Config, spec: dict[str, Any], job: str) -> None:
    name = "fiber-processing-" + job
    root = directory(config, job)
    p = plan(spec)["policy"]
    result = subprocess.run(
        ["docker", "inspect", name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15
    )
    if result.returncode:
        from .media_container_remove import wait_for_absence

        wait_for_absence("name=^/" + name + "$")
        return
    try:
        value = json.loads(result.stdout)
        if len(value) != 1:
            raise ValueError()
        info = value[0]
        identity = info["Id"]
        if (
            info["Config"]["Image"] != p["image"]
            or info["Config"]["Labels"].get("fiber-bundle.processing") != job
            or (
                {m["Destination"]: (m["Source"], m["RW"]) for m in info["Mounts"]}
                != {
                    "/media/input": (str(contained(root, "input")), False),
                    "/media/output": (str(contained(root, "output")), True),
                }
                or len(info["Mounts"]) != 2
            )
        ):
            raise ValueError()
    except (ValueError, KeyError, TypeError) as e:
        raise InvalidInput("media_processing_stop_scope") from e
    from .media_container_remove import remove

    remove(identity)


def execute(
    config: Config,
    job: str,
    source: bytes,
    subtitle: bytes | None,
    spec: dict[str, Any],
    *,
    guard: Callable[[], None],
) -> dict[str, Any]:
    plan(spec)
    guard()
    root = directory(config, job)
    if root.exists():
        raise InvalidInput("media_processing_attempt_exists")
    make_directory(root.parent)
    root.mkdir(mode=0o700)
    inputs = contained(root, "input")
    outputs = contained(root, "output")
    make_directory(inputs)
    make_directory(outputs)
    write(inputs, "source", source)
    write(inputs, "request.json", canonical(spec))
    if subtitle is not None:
        write(inputs, "subtitle", subtitle)
    process = None
    attempted = False
    deadline = time.monotonic() + spec["policy"]["timeout_seconds"]

    def check() -> None:
        guard()
        if time.monotonic() >= deadline:
            raise InvalidInput("media_processing_timeout")

    try:
        check()
        attempted = True
        subprocess.run(
            command(spec, inputs, outputs, job),
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        check()
        process = subprocess.Popen(
            ["docker", "start", "--attach", "fiber-processing-" + job],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while process.poll() is None:
            check()
            time.sleep(0.1)
        if process.returncode:
            raise InvalidInput("media_processing_worker_rejected")
        value = verify(inputs, outputs, source, subtitle, spec)
        check()
        verify(inputs, outputs, source, subtitle, spec)
        return value
    finally:
        try:
            if attempted:
                stop_owned(config, spec, job)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
