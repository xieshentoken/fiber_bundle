"""Host-side isolated article capture. No publication or client-facing API.

The caller supplies a current-authorization/cancellation guard, invoked before
launch, while running, and after host verification. The capture mount is distinct
from the Pi task workspace; article bytes never appear in model tool responses.
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from .article_capture import VerifiedCapture, verify_capture, write_new
from .articles import ArticleError, public_url
from .config import Config, contained, make_directory


@dataclass(frozen=True)
class CapturePolicy:
    image: str
    timeout_seconds: int = 240

    def validate(self) -> None:
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise ArticleError("capture_requires_fixed_image")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 600:
            raise ArticleError("invalid_capture_timeout")


@dataclass(frozen=True)
class CaptureResult:
    task_id: str
    directory: Path
    verified: VerifiedCapture


def command(policy: CapturePolicy, output: Path, name: str) -> list[str]:
    policy.validate()
    if (
        output.is_symlink()
        or output.resolve() != output
        or not output.is_dir()
        or "," in str(output)
        or not re.fullmatch(r"fiber-capture-[0-9a-f-]{36}", name)
    ):
        raise ArticleError("invalid_capture_scope")
    return [
        "docker",
        "create",
        "--rm",
        "--interactive",
        "--name",
        name,
        "--label",
        "fiber-bundle.capture=" + name.removeprefix("fiber-capture-"),
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "32",
        "--memory",
        "256m",
        "--cpus",
        "1",
        "--ulimit",
        "nofile=128:128",
        "--log-driver",
        "none",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=32m,mode=1777",
        "--mount",
        "type=bind,source=" + str(output) + ",target=/capture",
        "--network",
        "bridge",
        policy.image,
    ]


def create_container(policy: CapturePolicy, output: Path, name: str) -> None:
    subprocess.run(
        command(policy, output, name),
        timeout=20,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def start_command(name: str) -> list[str]:
    return ["docker", "start", "--attach", "--interactive", name]


def remove_container(name: str) -> None:
    removed = subprocess.run(
        ["docker", "rm", "--force", name],
        timeout=15,
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if removed.returncode != 0:
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"],
            timeout=15,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        if remaining.stdout.strip():
            raise ArticleError("capture_cleanup_failed")


def execute_capture(
    config: Config,
    policy: CapturePolicy,
    source_url: str,
    *,
    guard: Callable[[], None],
    task_id: str | None = None,
) -> CaptureResult:
    """Capture once. Failures preserve raw/partial files; caller chooses any retry."""
    config.validate()
    policy.validate()
    source_url = public_url(source_url)
    guard()
    task_id = task_id or str(uuid4())
    if str(UUID(task_id)) != task_id:
        raise ArticleError("invalid_capture_identifier")
    name = "fiber-capture-" + task_id
    root = contained(config.state_root, "article-captures/" + task_id)
    output = contained(root, "output")
    make_directory(output)
    write_new(root, "request.json", json.dumps({"url": source_url, "image": policy.image}).encode())
    process = None
    try:
        # Recheck before any worker code/network runs; cancellation has a known target.
        create_container(policy, output, name)
        guard()
        process = subprocess.Popen(
            start_command(name),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        process.stdin.write(json.dumps({"url": source_url}).encode())
        process.stdin.close()
        deadline = time.monotonic() + policy.timeout_seconds
        while process.poll() is None:
            guard()
            if time.monotonic() >= deadline:
                raise ArticleError("capture_timeout")
            time.sleep(0.1)
        if process.returncode != 0:
            raise ArticleError("capture_worker_failed")
        # --rm has stopped the writer before any of its files are trusted.
        verified = verify_capture(output, source_url)
        guard()
        return CaptureResult(task_id, output, verified)
    finally:
        try:
            remove_container(name)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                if process.stdin is not None:
                    process.stdin.close()
