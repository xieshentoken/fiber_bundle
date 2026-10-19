"""A single retained attempt in an offline, pinned, bounded media container."""

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import Config, InvalidInput, contained, make_directory
from .media_candidate import request
from .media_capture import verify, verify_snapshot
from .media_contract import MAX_MEDIA_BYTES
from .media_files import canonical, write
from .media_subtitles import MAX_SUBTITLE_BYTES


@dataclass(frozen=True)
class MediaPolicy:
    image: str
    timeout_seconds: int = 90

    def validate(self) -> None:
        if not isinstance(self.image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise InvalidInput("media_fixed_image_required")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 120:
            raise InvalidInput("media_timeout")


def directory(config: Config, job_id: str) -> Path:
    try:
        if str(UUID(job_id)) != job_id:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise InvalidInput("media_identifier") from exc
    return contained(config.state_root, "media-captures/" + job_id)


def command(policy: MediaPolicy, inputs: Path, outputs: Path, job_id: str) -> list[str]:
    policy.validate()
    if str(UUID(job_id)) != job_id:
        raise InvalidInput("media_identifier")
    for path in (inputs, outputs):
        if path.is_symlink() or path.resolve() != path or not path.is_dir() or "," in str(path):
            raise InvalidInput("media_mount_scope")
    if inputs == outputs or inputs in outputs.parents or outputs in inputs.parents:
        raise InvalidInput("media_mount_scope")
    return [
        "docker",
        "create",
        "--name",
        "fiber-media-" + job_id,
        "--label",
        "fiber-bundle.media=" + job_id,
        "--read-only",
        "--user",
        "1000:1000",
        "--network",
        "none",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--ulimit",
        "nofile=128:128",
        "--log-driver",
        "none",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
        "--mount",
        f"type=bind,source={inputs},target=/media/input,readonly",
        "--mount",
        f"type=bind,source={outputs},target=/media/output",
        policy.image,
    ]


def remove(job_id: str) -> None:
    name = "fiber-media-" + job_id
    result = subprocess.run(
        ["docker", "rm", "--force", name],
        timeout=15,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"],
            timeout=15,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        if remaining.stdout.strip():
            raise InvalidInput("media_worker_stop_failed")


def stop_owned(config: Config, policy: MediaPolicy, job_id: str) -> None:
    root = directory(config, job_id)
    policy.validate()
    result = subprocess.run(
        ["docker", "inspect", "fiber-media-" + job_id],
        timeout=15,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode:
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/fiber-media-" + job_id + "$"],
            timeout=15,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=True,
        )
        if remaining.stdout.strip():
            raise InvalidInput("media_worker_stop_scope")
        return
    try:
        info = json.loads(result.stdout)
        if not isinstance(info, list) or len(info) != 1:
            raise InvalidInput("media_worker_stop_scope")
        settings = info[0]["Config"]
        mounts = info[0]["Mounts"]
        expected = {
            "/media/input": (str(contained(root, "input")), False),
            "/media/output": (str(contained(root, "output")), True),
        }
        if (
            settings["Image"] != policy.image
            or settings["Labels"].get("fiber-bundle.media") != job_id
            or len(mounts) != 2
            or {m["Destination"]: (m["Source"], m["RW"]) for m in mounts} != expected
        ):
            raise InvalidInput("media_worker_stop_scope")
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidInput("media_worker_stop_scope") from exc
    remove(job_id)


def execute(
    config: Config,
    policy: MediaPolicy,
    job_id: str,
    source: bytes,
    subtitle: bytes | None,
    spec: dict[str, Any],
    *,
    guard: Callable[[], None],
) -> dict[str, Any]:
    config.validate()
    policy.validate()
    request(spec)
    if (
        not isinstance(source, bytes)
        or not 0 < len(source) <= MAX_MEDIA_BYTES
        or (
            subtitle is not None
            and (not isinstance(subtitle, bytes) or not (0 < len(subtitle) <= MAX_SUBTITLE_BYTES))
        )
        or (subtitle is None) != (spec["subtitle_kind"] is None)
    ):
        raise InvalidInput("media_snapshot_input")
    guard()
    root = directory(config, job_id)
    if root.exists():
        raise InvalidInput("media_attempt_exists")
    make_directory(root.parent)
    root.mkdir(mode=0o700)
    inputs, outputs = contained(root, "input"), contained(root, "output")
    make_directory(inputs)
    make_directory(outputs)
    write(inputs, "source", source)
    write(inputs, "request.json", canonical(spec))
    if subtitle is not None:
        write(inputs, "subtitle", subtitle)
    created = False
    process = None
    deadline = time.monotonic() + policy.timeout_seconds

    def check() -> None:
        guard()
        if time.monotonic() >= deadline:
            raise InvalidInput("media_worker_timeout")

    try:
        check()
        subprocess.run(
            command(policy, inputs, outputs, job_id),
            timeout=15,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        created = True
        check()
        process = subprocess.Popen(
            ["docker", "start", "--attach", "fiber-media-" + job_id],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while process.poll() is None:
            check()
            time.sleep(0.1)
        if process.returncode != 0:
            raise InvalidInput("media_worker_rejected")
        verify_snapshot(inputs, source, subtitle, spec)
        result = verify(outputs, source, subtitle, spec)
        check()
        verify_snapshot(inputs, source, subtitle, spec)
        return result
    finally:
        try:
            if created:
                remove(job_id)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
