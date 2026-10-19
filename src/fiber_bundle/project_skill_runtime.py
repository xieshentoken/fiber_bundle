"""Offline qualification and read-only mounts of a frozen complete compiler Skill."""

import json
import subprocess
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import executor_container, expert_runtime, project_skill_contract, project_skill_files
from .config import Config, InvalidInput, contained, make_directory
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private


def compiler_mounts(config: Config, binding: dict[str, Any]) -> list[str]:
    value = project_skill_contract.validate(binding)
    snapshot = project_skill_files.verify(config, value["snapshot_sha256"])
    if any(snapshot[k] != value[k] for k in ("compiler_sha256", "pi_sha256")):
        raise InvalidInput("project_skill_binding_invalid")
    if value.get("features") is not None and value["features"] != snapshot.get("features"):
        raise InvalidInput("project_skill_binding_invalid")
    mounts = _compiler_mounts(config, snapshot)
    if value.get("host_tools_sha256"):
        from .expert_tool_versions import verify

        root = verify(config, value["host_tools_sha256"])
        mounts += _mount(
            root / "expert_worker_entry.py", "/opt/fiber/fiber_bundle/expert_worker_entry.py"
        )
    return mounts


def _compiler_mounts(config: Config, snapshot: dict[str, Any]) -> list[str]:
    root = project_skill_files.snapshot_path(config, snapshot["snapshot_sha256"])
    return _mount(root / "skill", "/opt/skill") + _mount(
        root / "skill-files.json", "/opt/skill-files.json"
    )


def _mount(source: Path, target: str) -> list[str]:
    if source.resolve() != source or source.is_symlink() or "," in str(source):
        raise InvalidInput("project_skill_directory_invalid")
    return ["--mount", "type=bind,source=" + str(source) + ",target=" + target + ",readonly"]


def pi_mounts(config: Config, binding: dict[str, Any]) -> list[str]:
    compiler_mounts(config, binding)  # Same complete tree and instruction hash as the compiler.
    root = project_skill_files.snapshot_path(config, binding["snapshot_sha256"])
    return _mount(root / "pi.md", "/opt/fiber/expert-processing.md") + _mount(
        root / "skill", "/task/skills/fake-expert"
    )


def qualification_path(config: Config, snapshot: str, image: str) -> Path:
    project_skill_files.snapshot_path(config, snapshot)
    return contained(
        config.state_root,
        "project-skills/qualifications/" + snapshot + "-" + digest(image.encode()) + ".json",
    )


def cached(config: Config, snapshot: dict[str, Any], image: str) -> dict[str, Any] | None:
    path = qualification_path(config, snapshot["snapshot_sha256"], image)
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_bytes())
        expert_runtime.verify(value)
        if value["image"] != image or value["skill_sha256"] != snapshot["compiler_sha256"]:
            raise ValueError()
        return dict(value)
    except (ValueError, KeyError, TypeError):
        raise InvalidInput("project_skill_qualification_changed") from None


def qualify(config: Config, snapshot: dict[str, Any], image: str) -> dict[str, Any]:
    old = cached(config, snapshot, image)
    if old:
        return old
    from .source_processing_contract import image as validate_image

    validate_image(image)
    project_skill_files.verify(config, snapshot["snapshot_sha256"])
    task = contained(config.state_root, "project-skills/checks/" + str(uuid4()))
    make_directory(task)
    identity = task.name
    command = executor_container.command(image, task, identity, network="none")
    pos = command.index(image)
    command[pos:pos] = ["--entrypoint", "python", *_compiler_mounts(config, snapshot)]
    command += [
        "-B",
        "/opt/skill/scripts/fake_expert.py",
        "doctor",
        "--profile",
        "native-text",
        "--json",
    ]
    try:
        result = subprocess.run(command, capture_output=True, timeout=30, check=False)
        if not 0 < len(result.stdout) <= 1024 * 1024:
            raise InvalidInput("project_skill_doctor_failed")
        doctor = json.loads(result.stdout)
        if not isinstance(doctor, dict):
            raise InvalidInput("project_skill_doctor_failed")
        write_private(task / "doctor.json", canonical(doctor).encode())
        if result.returncode or doctor.get("passed") is not True:
            for check in doctor.get("checks", []):
                if (
                    isinstance(check, dict)
                    and check.get("passed") is False
                    and check.get("name") in {"python", "pypdf", "pdftoppm"}
                ):
                    raise InvalidInput("project_skill_dependency_" + check["name"])
            raise InvalidInput("project_skill_doctor_failed")
        receipt = expert_runtime.receipt(image, snapshot["compiler_sha256"], doctor)
        project_skill_files.verify(config, snapshot["snapshot_sha256"])
        path = qualification_path(config, snapshot["snapshot_sha256"], image)
        make_directory(path.parent)
        write_private(path, canonical(receipt).encode())
        return receipt
    except (OSError, subprocess.TimeoutExpired, ValueError, AttributeError):
        raise InvalidInput("project_skill_runtime_unavailable") from None
    finally:
        try:
            executor_container.stop(identity)
        except (OSError, subprocess.TimeoutExpired):
            raise InvalidInput("project_skill_runtime_unavailable") from None
