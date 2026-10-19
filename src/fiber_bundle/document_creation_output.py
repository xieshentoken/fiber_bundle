"""Bounded draft output protocol and file checks; no inference, delivery or publication."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID

from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .document_creation_contract import MAX_ARTIFACT_BYTES, CreationPlan, validate
from .inputs import canonical
from .objects import digest
from .query_routing_files import read

SCHEMA = "document-creation-output/v1"
FORMAT_REVISION = "utf8-draft-v1"
DRAFT_MARKER = "未核验草稿 / UNVERIFIED DRAFT\n\n"
FORMATS = {"md": "text/markdown", "txt": "text/plain"}
MAX_RECEIPT_BYTES = 16384


def context(
    plan: CreationPlan, run_id: str, configuration_sha256: str, request_sha256: str
) -> dict[str, Any]:
    """The request SHA binds the fixed host envelope, not Pi's later actual model requests."""
    validate(plan)
    try:
        if str(UUID(run_id)) != run_id or any(
            not isinstance(value, str) or re.fullmatch(r"[a-f0-9]{64}", value) is None
            for value in (configuration_sha256, request_sha256)
        ):
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise InvalidInput("document_output_context_invalid") from None
    return {
        "run_id": run_id,
        "revision": plan.revision,
        "plan_sha256": plan.sha256,
        "input_sha256": digest(plan.input_json.encode()),
        "configuration_sha256": configuration_sha256,
        "request_sha256": request_sha256,
    }


@dataclass(frozen=True)
class CheckedOutput:
    """Immutable checked bytes. Reopening the untrusted worker pathname is unnecessary."""

    content: bytes = field(repr=False)
    filename: str
    media_type: str
    context_json: str
    upload_limit_bytes: int | None

    def report(self) -> dict[str, Any]:
        return {
            "schema": "document-creation-output-check/v1",
            "context": json.loads(self.context_json),
            "artifact": {
                "sha256": digest(self.content),
                "byte_count": len(self.content),
                "original_name": self.filename,
                "media_type": self.media_type,
            },
            "format_check_revision": FORMAT_REVISION,
            "format_verified": True,
            "file_exists_verified": True,
            "upload_size_checked": self.upload_limit_bytes is not None,
            "upload_limit_bytes": self.upload_limit_bytes,
            # A size check is not a real transport preflight or a successful send.
            "uploadable_verified": False,
            "unverified_draft": True,
            "knowledge_verified": False,
            "semantic_verified": False,
            "runtime_verified": False,
            "execution_allowed": False,
            "delivery_allowed": False,
            "save_allowed": False,
        }


def inspect(
    task: Path,
    plan: CreationPlan,
    expected_context: dict[str, Any],
    *,
    upload_limit_bytes: int | None = None,
) -> CheckedOutput:
    """Only fixed draft.md/draft.txt plus output.json; reject links, races and false formats."""
    validate(plan)
    if plan.requested_format not in FORMATS:
        raise InvalidInput("document_output_format_unqualified")
    if upload_limit_bytes is not None and (
        type(upload_limit_bytes) is not int or not 0 < upload_limit_bytes <= MAX_ARTIFACT_BYTES
    ):
        raise InvalidInput("document_upload_limit_invalid")
    try:
        expected = context(
            plan,
            expected_context["run_id"],
            expected_context["configuration_sha256"],
            expected_context["request_sha256"],
        )
        if canonical(expected_context) != canonical(expected):
            raise ValueError()
        if not task.is_absolute() or task.resolve() != task or not task.is_dir():
            raise ValueError()
        receipt_raw = read(task / "output.json", MAX_RECEIPT_BYTES)
        receipt = json.loads(
            receipt_raw,
            object_pairs_hook=unique_object,
            parse_constant=reject_constant,
        )
        filename = "draft." + plan.requested_format
        if (
            not isinstance(receipt, dict)
            or set(receipt) != {"schema", "context", "filename", "sha256", "byte_count"}
            or receipt["schema"] != SCHEMA
            or canonical(receipt["context"]) != canonical(expected)
            or receipt["filename"] != filename
            or not isinstance(receipt["sha256"], str)
            or re.fullmatch(r"[a-f0-9]{64}", receipt["sha256"]) is None
            or type(receipt["byte_count"]) is not int
            or not 0 < receipt["byte_count"] <= MAX_ARTIFACT_BYTES
        ):
            raise ValueError()
        raw = read(task / filename, MAX_ARTIFACT_BYTES)
        if len(raw) != receipt["byte_count"] or digest(raw) != receipt["sha256"]:
            raise ValueError()
        value = raw.decode("utf-8")
        if (
            not value.startswith(DRAFT_MARKER)
            or not value[len(DRAFT_MARKER) :].strip()
            or any((ord(c) < 32 and c not in "\n\t\r") or ord(c) == 127 for c in value)
        ):
            raise ValueError()
        # Check the receipt again after taking the file snapshot. No live worker is trusted.
        if read(task / "output.json", MAX_RECEIPT_BYTES) != receipt_raw:
            raise ValueError()
    except (OSError, ValueError, TypeError, KeyError, RecursionError, InvalidInput):
        raise InvalidInput("document_output_invalid") from None
    if upload_limit_bytes is not None and len(raw) > upload_limit_bytes:
        raise InvalidInput("document_output_upload_size_exceeded")
    return CheckedOutput(
        raw, filename, FORMATS[plan.requested_format], canonical(expected), upload_limit_bytes
    )
