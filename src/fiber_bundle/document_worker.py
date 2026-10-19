"""One offline candidate extraction in a pinned container; caller owns task authorization."""

import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from . import document_capture
from .config import Config, IntegrityError, contained, make_directory
from .document_contract import DocumentError
from .document_source import DocumentFile, binding
from .inputs import canonical


@dataclass(frozen=True)
class DocumentPolicy:
    image: str
    timeout_seconds: int = 60

    def validate(self) -> None:
        if not isinstance(self.image, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image):
            raise DocumentError("document_fixed_image_required")
        if type(self.timeout_seconds) is not int or not 1 <= self.timeout_seconds <= 300:
            raise DocumentError("document_timeout_invalid")


@dataclass(frozen=True)
class DocumentCapture:
    task_id: str
    directory: Path
    result: dict[str, Any]


def command(policy: DocumentPolicy, inputs: Path, outputs: Path, name: str) -> list[str]:
    policy.validate()
    for path in (inputs, outputs):
        if path.is_symlink() or path.resolve() != path or not path.is_dir() or "," in str(path):
            raise DocumentError("document_mount_scope")
    if (
        inputs == outputs
        or inputs in outputs.parents
        or outputs in inputs.parents
        or not re.fullmatch(r"fiber-document-[0-9a-f-]{36}", name)
    ):
        raise DocumentError("document_mount_scope")
    return [
        "docker",
        "create",
        "--rm",
        "--interactive",
        "--name",
        name,
        "--label",
        "fiber-bundle.document=" + name.removeprefix("fiber-document-"),
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
        "type=bind,source=" + str(inputs) + ",target=/document/input,readonly",
        "--mount",
        "type=bind,source=" + str(outputs) + ",target=/document/output",
        "--network",
        "none",
        policy.image,
    ]


def remove(name: str) -> None:
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
            raise DocumentError("document_cleanup_failed")


def execute_document(
    config: Config,
    policy: DocumentPolicy,
    source: bytes | DocumentFile,
    *,
    guard: Callable[[], None],
    task_id: str | None = None,
    format: str = "pdf",
    profile: str = "native",
    runtime: dict[str, Any] | None = None,
    limits: dict[str, int] | None = None,
) -> DocumentCapture:
    config.validate()
    policy.validate()
    from .document_capacity import checked

    effective = checked(limits)
    if limits is not None and (format != "pdf" or profile != "native"):
        raise DocumentError("document_limits_profile")
    try:
        source_sha, source_size = binding(source, effective["source_bytes"])
    except IntegrityError as exc:
        raise DocumentError("document_source_size") from exc
    if format not in {
        "pdf",
        "docx",
        "doc",
        "xlsx",
        "pptx",
        "chat",
        "image",
        "text",
        "archive",
    } or profile not in {
        "native",
        "ocr",
        "conversion",
    }:
        raise DocumentError("document_profile")
    if profile == "ocr":
        from .document_ocr import verify_runtime

        if runtime is None or format not in {"pdf", "image"}:
            raise DocumentError("document_ocr_binding")
        verify_runtime(runtime)
        if runtime["image"] != policy.image:
            raise DocumentError("document_ocr_image_binding")
    elif profile == "conversion":
        from .document_conversion import verify_runtime as verify_conversion

        if runtime is None or format != "doc":
            raise DocumentError("document_conversion_binding")
        verify_conversion(runtime)
        if runtime["image"] != policy.image:
            raise DocumentError("document_conversion_image_binding")
    elif runtime is not None or format in {"image", "doc"}:
        raise DocumentError("document_profile")
    guard()
    identifier = task_id or str(uuid4())
    try:
        if str(UUID(identifier)) != identifier:
            raise ValueError("identifier")
    except (ValueError, AttributeError, TypeError) as exc:
        raise DocumentError("document_identifier") from exc
    root = contained(config.state_root, "document-captures/" + identifier)
    if root.exists():
        raise DocumentError("document_attempt_exists")
    inputs, outputs = contained(root, "input"), contained(root, "output")
    make_directory(inputs)
    make_directory(outputs)
    document_capture.write_new(
        root,
        "request.json",
        canonical(
            {
                "schema": 1,
                "format": format,
                "profile": profile,
                "image": policy.image,
                "source_sha256": source_sha,
                "source_bytes": source_size,
            }
        ).encode(),
    )
    if isinstance(source, DocumentFile):
        source.copy_new(inputs / "source.pdf")
    else:
        document_capture.write_new(inputs, "source.pdf", source)
    name = "fiber-document-" + identifier
    process = None
    created = False
    try:
        subprocess.run(
            command(policy, inputs, outputs, name),
            timeout=20,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        created = True
        guard()
        process = subprocess.Popen(
            ["docker", "start", "--attach", "--interactive", name],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        request: dict[str, Any] = (
            {"schema": 1}
            if format == "pdf" and profile == "native"
            else {"schema": 2, "format": format}
        )
        if limits is not None:
            request = {"schema": 3, "format": "pdf", "limits": effective}
        if profile != "native":
            request.update(profile=profile, runtime=runtime)
        process.stdin.write(canonical(request).encode())
        process.stdin.close()
        deadline = time.monotonic() + policy.timeout_seconds
        while process.poll() is None:
            guard()
            if time.monotonic() >= deadline:
                raise DocumentError("document_timeout")
            time.sleep(0.1)
        if process.returncode != 0:
            raise DocumentError("document_worker_failed")
        # The writer has stopped; validate bytes and scope before exposing candidate text.
        DocumentFile(inputs / "source.pdf", source_sha, source_size).verify()
        result = document_capture.verify(outputs, source)
        guard()
        return DocumentCapture(identifier, root, result)
    finally:
        try:
            if created:
                remove(name)
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


def stop_owned(config: Config, policy: DocumentPolicy, task_id: str) -> None:
    """Recover only the bound worker with matching label, image and private mounts."""
    import json

    policy.validate()
    if str(UUID(task_id)) != task_id:
        raise DocumentError("document_identifier")
    name = "fiber-document-" + task_id
    probe = subprocess.run(
        ["docker", "inspect", name],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=15,
        check=False,
    )
    if probe.returncode != 0:
        remaining = subprocess.run(
            ["docker", "ps", "-aq", "--filter", "name=^/" + name + "$"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=True,
        )
        if remaining.stdout.strip():
            raise DocumentError("document_cleanup_scope")
        return
    try:
        info = json.loads(probe.stdout)
        if not isinstance(info, list) or len(info) != 1:
            raise DocumentError("document_cleanup_scope")
        container = info[0]
        settings = container["Config"]
        root = contained(config.state_root, "document-captures/" + task_id)
        mounts = container["Mounts"]
        expected = {
            "/document/input": (str(contained(root, "input")), False),
            "/document/output": (str(contained(root, "output")), True),
        }
        if (
            settings["Labels"].get("fiber-bundle.document") != task_id
            or settings["Image"] != policy.image
            or len(mounts) != 2
            or {m["Destination"]: (m["Source"], m["RW"]) for m in mounts} != expected
        ):
            raise DocumentError("document_cleanup_scope")
    except (ValueError, KeyError, TypeError) as exc:
        raise DocumentError("document_cleanup_scope") from exc
    remove(name)
