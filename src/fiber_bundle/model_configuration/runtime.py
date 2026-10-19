"""Trusted Pi runtime qualification. Preparation is explicit, inspection never starts jobs."""

import hashlib
import json
import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from ..config import Config, contained, make_directory
from ..inputs import canonical
from ..query_policy_store import write_private
from .errors import ModelError


def manifest() -> dict[str, Any]:
    path = Path(__file__).parents[1] / "vendor/model-runtime.json"
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or value.get("schema") != "fiber-model-runtime/v1":
        raise ModelError("model_runtime_changed")
    for kind in ("image", "creation_image"):
        if not re.fullmatch(r"sha256:[a-f0-9]{64}", value.get(kind, "")):
            raise ModelError("model_runtime_changed")
    for name, digest in value["sources"].items():
        source = contained(Path(__file__).parents[1], name)
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ModelError("model_runtime_changed")
    return value


def expected_image(kind: str = "pi") -> str:
    return str(manifest()["creation_image" if kind == "creation" else "image"])


def inspect(kind: str = "pi") -> dict[str, Any]:
    value = manifest()
    image = expected_image(kind)
    try:
        run = subprocess.run(
            ["docker", "image", "inspect", image],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ModelError("model_docker_unavailable") from None
    if run.returncode:
        daemon = subprocess.run(
            ["docker", "info", "--format", "{{.Architecture}}"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        if daemon.returncode:
            raise ModelError("model_docker_unavailable")
        if daemon.stdout.strip() not in {b"arm64", b"aarch64"}:
            raise ModelError("model_runtime_architecture")
        return {
            "state": "missing",
            "message": "受限 Pi 环境尚未准备，请点击准备运行环境。",
            "expected_image": image,
        }
    try:
        info = json.loads(run.stdout)[0]
        if (
            info["Id"] != image
            or info["Architecture"] != value["architecture"]
            or info["Config"]["Labels"].get("local.fiber-bundle.model-recipe")
            != value["recipe_sha256"]
        ):
            raise ModelError("model_runtime_changed")
    except (ValueError, KeyError, IndexError):
        raise ModelError("model_runtime_changed") from None
    return {
        "state": "ready",
        "image": image,
        "pi_version": value["pi_version"],
        "architecture": value["architecture"],
    }


def require(image: str) -> None:
    kind = "creation" if image == expected_image("creation") else "pi"
    if image != expected_image(kind):
        raise ModelError("model_runtime_changed")
    if inspect(kind)["state"] != "ready":
        raise ModelError("model_runtime_missing")


def preparation(config: Config) -> dict[str, Any]:
    path = contained(config.state_root, "configs/model-runtime-preparation.json")
    if not path.exists():
        return {"state": "not_run"}
    data = json.loads(path.read_text())
    if data["state"] in {"queued", "running"} and time.time() - data["updated_at"] > 180:
        return {"state": "interrupted", "error_code": "model_runtime_prepare_interrupted"}
    return dict(data)


def mark(config: Config, state: str, error: str | None = None) -> None:
    root = contained(config.state_root, "configs")
    make_directory(root)
    write_private(
        root / "model-runtime-preparation.json",
        canonical({"state": state, "error_code": error, "updated_at": time.time()}).encode(),
    )


def prepare(config: Config, archive: Path | None = None) -> None:
    mark(config, "running")
    try:
        value = manifest()
        if inspect()["state"] == "ready" and inspect("creation")["state"] == "ready":
            mark(config, "succeeded")
            return
        path = archive or Path(__file__).parents[4] / "model-runtimes/model-runtime.tar.gz"
        if not path.is_file() or path.is_symlink() or path.resolve() != path:
            raise ModelError("model_runtime_archive_missing")
        digest = hashlib.sha256()
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, "rb") as stream:
            while data := stream.read(1024 * 1024):
                digest.update(data)
        if digest.hexdigest() != value.get("archive_sha256"):
            raise ModelError("model_runtime_changed")
        try:
            command = subprocess.run(
                ["docker", "load", "--input", str(path)],
                capture_output=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            raise ModelError("model_runtime_prepare_timeout") from None
        if (
            command.returncode
            or inspect()["state"] != "ready"
            or inspect("creation")["state"] != "ready"
        ):
            raise ModelError("model_runtime_changed")
        mark(config, "succeeded")
    except Exception as exc:
        mark(
            config,
            "failed",
            exc.code if isinstance(exc, ModelError) else "model_docker_unavailable",
        )
