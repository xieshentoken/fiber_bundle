"""Explicit worker images plus an already approved execution model; no auto execution."""

import json
from typing import Any

from . import query_policy_store
from . import source_processing_contract as contract
from .config import Config, InvalidInput, contained, make_directory
from .execution_config import ExecutionConfig
from .inputs import canonical
from .objects import digest
from .query_routing_files import read


def inspect(config: Config, execution: ExecutionConfig | None) -> dict[str, Any]:
    from . import project_skills

    project_skill = project_skills.inspect(config)
    path = contained(config.state_root, "configs/source-processing.json")
    value = json.loads(read(path, 16384), object_pairs_hook=_unique) if path.exists() else None
    if value is not None:
        validate(value)
    if execution is None:
        route = query_policy_store.current(config)
        execution = route.cloud_answer if route else None
    return {
        # Retained for the existing Pi task UI, which still needs an execution model.
        "configured": value is not None
        and execution is not None
        and value["document_image"] is not None
        and value["article_image"] is not None,
        "policy_configured": value is not None,
        "value": value,
        "effective_policy": contract.normalize(value) if value else contract.defaults(),
        "capacity": dict(contract.CAPACITY_LIMITS),
        "capacity_scope": "file_native_pdf_structured_parts",
        "structured_parts_ready": True,
        "full_fake_expert_ready": bool(value and value.get("expert_runtime"))
        or bool(project_skill["settings"]["enabled"] and project_skill["state"] == "ready"),
        "project_skill": project_skill,
        "configuration_sha256": digest(canonical(value).encode()) if value else None,
        "model_configuration_sha256": execution.digest if execution else None,
        "model_configured": execution is not None,
        "automatic": False,
    }


def model(config: Config, execution: ExecutionConfig | None) -> ExecutionConfig:
    from .model_configuration import policy as registered

    if registered.has_default(config):
        return registered.resolve(config)
    if execution is None:
        route = query_policy_store.current(config)
        execution = route.cloud_answer if route else None
    if execution is None:
        raise InvalidInput("source_execution_model_not_configured")
    execution.validate()
    return execution


def validate(value: Any) -> None:
    contract.normalize(value)


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise InvalidInput("processing_policy_duplicate_field")
        result[key] = value
    return result


def save(
    config: Config, execution: ExecutionConfig | None, value: dict[str, Any], expected: str | None
) -> dict[str, Any]:
    validate(value)
    # Caller holds the existing storage lock (HTTP guard or explicit core caller).
    current = inspect(config, execution)
    if current["configuration_sha256"] != expected:
        raise InvalidInput("source processing policy changed")
    revision = 1 if current["value"] is None else current["effective_policy"]["revision"] + 1
    if set(value) == contract.LEGACY_FIELDS:
        # Old UI saves worker settings without deleting a newer limits policy.
        next_value = {
            **current["effective_policy"],
            **value,
            "schema": current["effective_policy"]["schema"],
            "revision": revision,
        }
    else:
        if value["revision"] != revision:
            raise InvalidInput("processing_policy_revision_conflict")
        next_value = contract.normalize(value)
    validate(next_value)
    root = contained(config.state_root, "configs")
    if not root.exists():
        make_directory(root)
    query_policy_store.write_private(
        root / "source-processing.json", canonical(next_value).encode()
    )
    return inspect(config, execution)
