"""Offline pinned compiler inputs and workpack validation; source material stays private."""

import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import processing_container
from .config import Config, InvalidInput, contained, make_directory
from .document_source import DocumentFile
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private
from .query_routing_files import read


def code_digest() -> str:
    here = Path(__file__).parent
    return digest(
        canonical(
            {
                n: digest((here / n).read_bytes())
                for n in (
                    "expert_authoring_entry.py",
                    "expert_authoring_tools.py",
                    "expert_worker_entry.py",
                    "processing_container.py",
                    "expert_authoring_units.py",
                    "expert_proposals.py",
                )
            }
        ).encode()
    )


def seed(
    config: Config,
    file: DocumentFile,
    plan: dict[str, Any],
    profile: str,
    guard: Any,
    packed: DocumentFile | None = None,
) -> tuple[Path, dict[str, Any]]:
    attempt = str(uuid4())
    root = contained(config.state_root, "expert-authoring/" + attempt)
    make_directory(root / "input")
    make_directory(root / "output")
    file.copy_new(root / "input/source.pdf")
    if packed is not None:
        from .expert_workpack import unpack

        unpack(packed, root / "output/workpack")
    spec = {
        "action": "seed",
        "profile": profile,
        "source_sha256": file.sha256,
        "skill_sha256": plan["policy"]["expert_runtime"]["skill_sha256"],
        "limits": plan["limits"],
    }
    write_private(root / "input/request.json", canonical(spec).encode())
    processing_container.run(
        root,
        attempt,
        plan["policy"]["expert_runtime"]["image"],
        "expert_authoring_entry",
        plan["policy"]["timeout_seconds"],
        guard,
        skill_mounts=_skill_mounts(config, plan),
    )
    value = json.loads(read(root / "output/result.json", 4 * 1024 * 1024))
    if (
        value.get("schema") != "fiber-expert-authoring-seed/v1"
        or value.get("source_sha256") != file.sha256
        or value.get("skill_sha256") != spec["skill_sha256"]
    ):
        raise InvalidInput("expert_authoring_source_changed")
    if not isinstance(value.get("units"), list) or not 1 <= len(value["units"]) <= 2000:
        raise InvalidInput("expert_authoring_unit_invalid")
    return root, value


def validate(config: Config, root: Path, plan: dict[str, Any], guard: Any) -> dict[str, Any]:
    previous = json.loads(read(root / "input/request.json", 32768))
    write_private(
        root / "input/request.json", canonical({**previous, "action": "validate"}).encode()
    )
    attempt = str(uuid4())
    processing_container.run(
        root,
        attempt,
        plan["policy"]["expert_runtime"]["image"],
        "expert_authoring_entry",
        plan["policy"]["timeout_seconds"],
        guard,
        skill_mounts=_skill_mounts(config, plan),
    )
    result = json.loads(read(root / "output/validation.json", 4 * 1024 * 1024))
    if (
        not isinstance(result, dict)
        or result.get("schema") != "fiber-expert-authoring-validation/v1"
        or result.get("knowledge_verified") is not False
    ):
        raise InvalidInput("expert_authoring_proposal_invalid")
    return result


def _skill_mounts(config: Config, plan: dict[str, Any]) -> list[str] | None:
    from .project_skill_runtime import compiler_mounts

    return compiler_mounts(config, plan["project_skill"]) if plan.get("project_skill") else None
