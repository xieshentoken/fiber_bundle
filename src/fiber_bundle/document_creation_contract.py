"""Immutable offline document plans and candidate descriptors; no execution authority."""

import json
import re
from dataclasses import dataclass, replace
from typing import Any

from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .inputs import AssetInput, canonical, text
from .objects import digest

MAX_INPUT_BYTES = 256 * 1024
MAX_REQUIREMENT_BYTES = 16384
MAX_REVISIONS = 32
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_PLAN_BYTES = MAX_INPUT_BYTES + MAX_REQUIREMENT_BYTES + 65536


@dataclass(frozen=True)
class CreationPlan:
    client_id: str
    query_task_id: str
    input_json: str
    requirements: tuple[str, ...]
    requested_format: str
    revision: int = 1
    parent_sha256: str | None = None
    apply_at: str = "before_execution"

    @property
    def sha256(self) -> str:
        return digest(canonical(self.value()).encode())

    def value(self) -> dict[str, Any]:
        source = validate(self)
        return {
            "schema": "document-creation-plan/v1",
            "client_id": self.client_id,
            "query_task_id": self.query_task_id,
            "input": source,
            "input_sha256": digest(self.input_json.encode()),
            "requirements": list(self.requirements),
            "requested_format": self.requested_format,
            "revision": self.revision,
            "parent_sha256": self.parent_sha256,
            "apply_at": self.apply_at,
            "input_coverage": "retrieved_excerpts",
            "evidence_status": "evidence_only" if source["sources"] else "insufficient_evidence",
            "unverified_draft": True,
            "semantic_verified": False,
            "execution_allowed": False,
            "format_verified": False,
            "delivery_allowed": False,
            "save_allowed": False,
        }


def requirement(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInput("document_requirement_invalid")
    try:
        if len(value.encode()) > MAX_REQUIREMENT_BYTES or any(
            (ord(c) < 32 and c not in "\n\t") or ord(c) == 127 for c in value
        ):
            raise ValueError()
    except (ValueError, UnicodeError):
        raise InvalidInput("document_requirement_invalid") from None
    return value


def parse(raw: str) -> CreationPlan:
    """Load a canonical stored plan, including all derived fields and closed-off gates."""
    try:
        if not isinstance(raw, str) or len(raw.encode()) > MAX_PLAN_BYTES:
            raise ValueError()
        value = json.loads(raw, object_pairs_hook=unique_object, parse_constant=reject_constant)
        if not isinstance(value, dict) or not isinstance(value.get("requirements"), list):
            raise ValueError()
        plan = CreationPlan(
            value["client_id"],
            value["query_task_id"],
            canonical(value["input"]),
            tuple(value["requirements"]),
            value["requested_format"],
            value["revision"],
            value["parent_sha256"],
            value["apply_at"],
        )
        if canonical(plan.value()) != raw:
            raise ValueError()
        return plan
    except (KeyError, TypeError, ValueError, UnicodeError, RecursionError, InvalidInput):
        raise InvalidInput("document_plan_invalid") from None


def validate(plan: CreationPlan) -> dict[str, Any]:
    try:
        text(plan.client_id, limit=128)
        text(plan.query_task_id, limit=128)
        if (
            not isinstance(plan.requirements, tuple)
            or not 1 <= len(plan.requirements) <= MAX_REVISIONS
            or sum(len(requirement(r).encode()) for r in plan.requirements) > MAX_REQUIREMENT_BYTES
            or type(plan.revision) is not int
            or plan.revision != len(plan.requirements)
            or not isinstance(plan.requested_format, str)
            or re.fullmatch(r"[a-z][a-z0-9-]{0,31}", plan.requested_format) is None
            or plan.apply_at not in {"before_execution", "next_version"}
            or (plan.revision == 1 and plan.parent_sha256 is not None)
            or (
                plan.revision > 1
                and (
                    not isinstance(plan.parent_sha256, str)
                    or re.fullmatch(r"[0-9a-f]{64}", plan.parent_sha256) is None
                )
            )
            or not isinstance(plan.input_json, str)
            or len(plan.input_json.encode()) > MAX_INPUT_BYTES
        ):
            raise ValueError()
        value = json.loads(
            plan.input_json, object_pairs_hook=unique_object, parse_constant=reject_constant
        )
        if (
            not isinstance(value, dict)
            or set(value)
            != {
                "client_id",
                "query_task_id",
                "binding_sha256",
                "vault_ids",
                "source_version_id",
                "sources",
                "quotations",
            }
            or value["client_id"] != plan.client_id
            or value["query_task_id"] != plan.query_task_id
            or not isinstance(value["binding_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", value["binding_sha256"]) is None
            or not isinstance(value["vault_ids"], list)
            or not 1 <= len(value["vault_ids"]) <= 20
            or any(not isinstance(v, str) or not v for v in value["vault_ids"])
            or len(set(value["vault_ids"])) != len(value["vault_ids"])
            or (
                value["source_version_id"] is not None
                and not isinstance(value["source_version_id"], str)
            )
            or not isinstance(value["sources"], list)
            or not isinstance(value["quotations"], list)
            or len(value["sources"]) != len(value["quotations"])
            or len(value["sources"]) > 50
            or any(
                not isinstance(s, dict)
                or not isinstance(q, dict)
                or set(q) != {"citation", "quote"}
                or not isinstance(q["citation"], str)
                or not isinstance(q["quote"], str)
                or not q["citation"]
                or not q["quote"]
                or q["citation"] != s.get("citation")
                or q["quote"] != s.get("snippet")
                for s, q in zip(value["sources"], value["quotations"], strict=True)
            )
            or canonical(value) != plan.input_json
        ):
            raise ValueError()
        return value
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeError, RecursionError):
        raise InvalidInput("document_plan_invalid") from None


def amend(plan: CreationPlan, addition: str, *, expected_sha256: str, phase: str) -> CreationPlan:
    validate(plan)
    if expected_sha256 != plan.sha256:
        raise InvalidInput("document_plan_conflict")
    if phase not in {"prepared", "running", "finished"}:
        raise InvalidInput("document_phase_invalid")
    updated = replace(
        plan,
        requirements=(*plan.requirements, requirement(addition)),
        revision=plan.revision + 1,
        parent_sha256=plan.sha256,
        # Without a verified executor checkpoint, running additions never mutate input.
        apply_at="before_execution" if phase == "prepared" else "next_version",
    )
    validate(updated)
    return updated


def artifact(
    plan: CreationPlan, content: bytes, filename: str, *, expected_sha256: str
) -> dict[str, Any]:
    validate(plan)
    if expected_sha256 != plan.sha256:
        raise InvalidInput("document_plan_conflict")
    if not isinstance(content, bytes) or not 0 < len(content) <= MAX_ARTIFACT_BYTES:
        raise InvalidInput("document_artifact_invalid")
    descriptor = AssetInput(content, filename, "application/octet-stream").descriptor()
    if filename.rsplit(".", 1)[-1] != plan.requested_format:
        raise InvalidInput("document_artifact_format_mismatch")
    return {
        "schema": "document-creation-artifact/v1",
        "plan_sha256": plan.sha256,
        "input_sha256": digest(plan.input_json.encode()),
        "revision": plan.revision,
        "parent_plan_sha256": plan.parent_sha256,
        "artifact": descriptor,
        "requested_format": plan.requested_format,
        "unverified_draft": True,
        "knowledge_verified": False,
        "semantic_verified": False,
        "format_verified": False,
        "file_exists_verified": False,
        "uploadable_verified": False,
        "delivery_allowed": False,
        "save_allowed": False,
    }
