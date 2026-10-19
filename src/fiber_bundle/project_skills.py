"""Project Skill settings and task selection, isolated from existing worker/model settings."""

import json
from typing import Any

from . import project_skill_contract, project_skill_files, project_skill_runtime
from .config import Config, InvalidInput, contained, make_directory
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private


def settings(config: Config) -> tuple[dict[str, Any], str | None]:
    path = contained(config.state_root, "configs/project-skills.json")
    if not path.exists():
        return {"schema": 1, "enabled": False, "image": None}, None
    raw = path.read_bytes()
    try:
        value = json.loads(raw)
    except ValueError:
        raise InvalidInput("project_skill_settings_invalid") from None
    if (
        not isinstance(value, dict)
        or set(value) != {"schema", "enabled", "image"}
        or value["schema"] != 1
        or type(value["enabled"]) is not bool
    ):
        raise InvalidInput("project_skill_settings_invalid")
    from .source_processing_contract import image

    image(value["image"])
    return value, digest(raw)


def inspect(config: Config) -> dict[str, Any]:
    value, revision = settings(config)
    result: dict[str, Any] = {
        "settings": value,
        "settings_sha256": revision,
        "directory": str(contained(config.project_root, "skills/fake-expert")),
        "state": "missing",
        "snapshot": None,
        "reason": None,
    }
    try:
        root = project_skill_files.source(config)
        if root.exists():
            metadata, _ = project_skill_files.contents(project_skill_files.read_tree(root))
            identity = digest(canonical(metadata).encode())
            snapshot = {**metadata, "snapshot_sha256": identity}
            runtime = (
                project_skill_runtime.cached(config, snapshot, value["image"])
                if value["image"]
                else None
            )
            result.update(
                state="ready" if runtime else "detected",
                snapshot={
                    "id": "fake-expert",
                    "release": metadata["release"],
                    "snapshot_sha256": identity,
                    "compiler_sha256": metadata["compiler_sha256"],
                    "files": len(metadata["files"]),
                    "qualified": bool(runtime),
                },
                reason=None,
            )
    except (InvalidInput, OSError, ValueError) as error:
        result.update(
            state="invalid",
            reason=str(error)
            if isinstance(error, InvalidInput)
            else "project_skill_directory_unreadable",
        )
    from .processing_errors import MESSAGES

    if result["reason"]:
        result["message"] = MESSAGES.get(result["reason"], "项目 Skill 暂不可用，请核对目录。")
    return result


def resolve(config: Config) -> dict[str, Any] | None:
    value, _ = settings(config)
    if not value["enabled"]:
        return None
    if not value["image"]:
        raise InvalidInput("project_skill_runtime_unavailable")
    snapshot = project_skill_files.capture(config)
    runtime = project_skill_runtime.qualify(config, snapshot, value["image"])
    from .expert_tool_versions import capture

    binding = {
        "schema": "fiber-project-skill/v1",
        "id": "fake-expert",
        **{k: snapshot[k] for k in ("snapshot_sha256", "compiler_sha256", "pi_sha256")},
        "runtime": runtime,
        "features": snapshot["features"],
        "host_tools_sha256": capture(config),
    }
    return project_skill_contract.validate(binding)


def save(config: Config, enabled: bool, image: str | None, expected: str | None) -> dict[str, Any]:
    from .source_processing_contract import image as validate_image

    validate_image(image)
    if settings(config)[1] != expected:
        raise InvalidInput("project_skill_settings_changed")
    if enabled:
        if not image:
            raise InvalidInput("project_skill_runtime_unavailable")
        snapshot = project_skill_files.capture(config)
        project_skill_runtime.qualify(config, snapshot, image)
    path = contained(config.state_root, "configs/project-skills.json")
    make_directory(path.parent)
    write_private(path, canonical({"schema": 1, "enabled": enabled, "image": image}).encode())
    return inspect(config)


def apply_policy(policy: dict[str, Any], binding: dict[str, Any]) -> dict[str, Any]:
    from .source_processing_contract import normalize

    value = normalize(policy)
    return {
        **value,
        "schema": 4,
        "revision": max(1, value["revision"]),
        "ocr_runtime": value.get("ocr_runtime"),
        "conversion_runtime": value.get("conversion_runtime"),
        "expert_runtime": project_skill_contract.validate(binding)["runtime"],
    }
