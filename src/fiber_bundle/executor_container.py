"""Shared container command and process ownership primitives, without task business logic."""

import fcntl
import os
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from .config import Config, InvalidInput, contained, make_directory, sync_directory


def identifier(value: str) -> str:
    if str(UUID(value)) != value:
        raise InvalidInput("invalid execution identifier")
    return value


def container_name(run_id: str, *, local: bool = False) -> str:
    return ("fiber-local-" if local else "fiber-run-") + identifier(run_id)


def command(
    image: str, task: Path, run_id: str, *, network: Literal["none", "bridge"], local: bool = False
) -> list[str]:
    if not isinstance(image, str) or not re.fullmatch(r"sha256:[a-f0-9]{64}", image):
        raise InvalidInput("invalid executor image")
    if task.is_symlink() or task.resolve() != task or not task.is_dir():
        raise InvalidInput("invalid task directory")
    return [
        "docker",
        "run",
        "--rm",
        "--interactive",
        "--name",
        container_name(run_id, local=local),
        "--label",
        "fiber-bundle.execution=" + run_id,
        "--read-only",
        "--user",
        "1000:1000",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--pids-limit",
        "64",
        "--memory",
        "1g",
        "--cpus",
        "1",
        "--ulimit",
        "nofile=256:256",
        "--log-driver",
        "none",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,size=128m,mode=1777",
        "--mount",
        "type=bind,source=" + str(task) + ",target=/task",
        "--network",
        network,
        image,
    ]


def stop(run_id: str, *, local: bool = False) -> None:
    subprocess.run(
        ["docker", "rm", "--force", container_name(run_id, local=local)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )


@contextmanager
def run_lock(config: Config, run_id: str) -> Any:
    root = contained(config.state_root, "execution-locks")
    try:
        make_directory(root)
    except FileExistsError:
        # Another independent task may create the common lock directory first.
        if root.is_symlink() or not root.is_dir() or root.resolve() != root:
            raise InvalidInput("invalid execution lock directory") from None
        sync_directory(root.parent)
    fd = os.open(contained(root, identifier(run_id)), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise InvalidInput("execution already owned") from exc
        yield
    finally:
        os.close(fd)
