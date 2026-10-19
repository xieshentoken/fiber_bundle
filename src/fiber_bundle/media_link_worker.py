"""Bounded isolated public downloader with owned-container reconciliation."""

import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID

from .config import Config, InvalidInput, contained, make_directory
from .media_files import canonical, decode
from .media_link_artifact import duration
from .media_link_contract import source_url
from .media_processing_files import read, write


def directory(config: Config, identity: str) -> Path:
    if str(UUID(identity)) != identity:
        raise InvalidInput("media_link_task_id")
    return contained(config.state_root, "media-links/" + identity)


def verify(root: Path, spec: dict[str, Any]) -> tuple[dict[str, Any], bytes]:
    if read(root / "input", "request.json", 4096) != canonical(spec):
        raise InvalidInput("media_link_request_changed")
    value = decode(read(root / "output", "result.json", 4096))
    data = read(root / "output", "source")
    duration(value, data, spec)
    if {p.name for p in (root / "output").iterdir()} != {
        "source",
        "result.json",
    }:
        raise InvalidInput("media_link_result_binding")
    return value, data


def stop_owned(config: Config, image: str, identity: str) -> None:
    import json

    name = "fiber-link-" + identity
    root = directory(config, identity)
    r = subprocess.run(
        ["docker", "inspect", name], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=15
    )
    if r.returncode:
        from .media_container_remove import wait_for_absence

        wait_for_absence("name=^/" + name + "$")
        return
    try:
        info = json.loads(r.stdout)
        if (
            len(info) != 1
            or info[0]["Config"]["Image"] != image
            or info[0]["Config"]["Labels"].get("fiber-bundle.link") != identity
            or (
                len(info[0]["Mounts"]) != 2
                or {m["Destination"]: (m["Source"], m["RW"]) for m in info[0]["Mounts"]}
                != {
                    "/media/input": (str(root / "input"), False),
                    "/media/output": (str(root / "output"), True),
                }
            )
        ):
            raise ValueError()
        owned = info[0]["Id"]
    except (KeyError, ValueError, TypeError) as e:
        raise InvalidInput("media_link_stop_scope") from e
    from .media_container_remove import remove

    remove(owned)


def execute(
    config: Config, identity: str, image: str, spec: dict[str, Any], *, guard: Callable[[], None]
) -> tuple[dict[str, Any], bytes]:
    return _execute(config, identity, image, spec, verify, guard=guard)


def execute_preflight(
    config: Config, identity: str, image: str, spec: dict[str, Any], *, guard: Callable[[], None]
) -> dict[str, Any]:
    from .media_link_metadata import verify as preview

    return _execute(config, identity, image, spec, preview, guard=guard)


def _execute[T](
    config: Config,
    identity: str,
    image: str,
    spec: dict[str, Any],
    verifier: Callable[[Path, dict[str, Any]], T],
    *,
    guard: Callable[[], None],
) -> T:
    source_url(spec["url"])
    root = directory(config, identity)
    guard()
    if root.exists():
        raise InvalidInput("media_link_no_replay")
    make_directory(root / "input")
    make_directory(root / "output")
    write(root / "input", "request.json", canonical(spec))
    name = "fiber-link-" + identity
    args = [
        "docker",
        "create",
        "--pull",
        "never",
        "--name",
        name,
        "--label",
        "fiber-bundle.link=" + identity,
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
        "512m",
        "--memory-swap",
        "512m",
        "--cpus",
        "1",
        "--pids-limit",
        "32",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--mount",
        "type=bind,src=" + str(root / "input") + ",dst=/media/input,readonly",
        "--mount",
        "type=bind,src=" + str(root / "output") + ",dst=/media/output",
        "--entrypoint",
        "python",
        image,
        "-m",
        "fiber_bundle.media_link_entry",
    ]
    process = None
    attempted = False
    deadline = time.monotonic() + 120
    try:
        attempted = True
        subprocess.run(
            args, check=True, timeout=15, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        guard()
        process = subprocess.Popen(
            ["docker", "start", "--attach", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        while process.poll() is None:
            guard()
            if time.monotonic() >= deadline:
                raise InvalidInput("media_link_timeout")
            time.sleep(0.1)
        if process.returncode:
            from .media_link_failure import AcquisitionBlocked, reported

            try:
                diagnostic = reported(root, spec)
            except (OSError, InvalidInput):
                diagnostic = "unclassified"
            raise AcquisitionBlocked(diagnostic)
        result = verifier(root, spec)
        guard()
        return result
    finally:
        try:
            if attempted:
                stop_owned(config, image, identity)
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
