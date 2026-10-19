"""Bounded, request-bound acquisition diagnostics; never adopt remote error text."""

import hashlib
from pathlib import Path
from typing import Any

from .config import InvalidInput
from .media_files import canonical, decode
from .media_processing_files import read, write

REASONS = frozenset(
    {
        "input_invalid",
        "dependency_unavailable",
        "metadata_failed",
        "source_unsupported",
        "access_restricted",
        "format_unsupported",
        "source_id_mismatch",
        "duration_mismatch",
        "size_unverified",
        "download_failed",
        "download_size_exceeded",
        "conversion_failed",
        "output_invalid",
        "unclassified",
    }
)
PREFIX = "media_link_attempt_blocked:"


class AcquisitionBlocked(InvalidInput):
    def __init__(self, reason: str):
        if reason not in REASONS:
            raise InvalidInput("media_link_failure_reason_invalid")
        self.reason = reason
        super().__init__("media_link_public_acquisition_blocked")


def reason(error: BaseException) -> str:
    if isinstance(error, AcquisitionBlocked) and error.reason in REASONS:
        return error.reason
    return "unclassified"


def record(inputs: Path, outputs: Path, error: BaseException) -> None:
    # Preserve any partial effects; never replace a candidate result or source file.
    if any(outputs.iterdir()):
        return
    raw = read(inputs, "request.json", 4096)
    write(
        outputs,
        "result.json",
        canonical(
            {
                "schema": "fiber-media-link-failure/v1",
                "request_sha256": hashlib.sha256(raw).hexdigest(),
                "reason": reason(error),
            }
        ),
    )


def reported(root: Path, spec: dict[str, Any]) -> str:
    if read(root / "input", "request.json", 4096) != canonical(spec):
        raise InvalidInput("media_link_failure_request_changed")
    if {p.name for p in (root / "output").iterdir()} != {"result.json"}:
        raise InvalidInput("media_link_failure_output_invalid")
    value = decode(read(root / "output", "result.json", 1024))
    if (
        set(value) != {"schema", "request_sha256", "reason"}
        or value["schema"] != "fiber-media-link-failure/v1"
        or value["request_sha256"] != hashlib.sha256(canonical(spec)).hexdigest()
        or not isinstance(value["reason"], str)
        or value["reason"] not in REASONS
    ):
        raise InvalidInput("media_link_failure_binding_invalid")
    return value["reason"]


def stored(error: BaseException) -> str:
    return PREFIX + reason(error)


def from_code(code: object) -> str | None:
    if isinstance(code, str) and code.startswith(PREFIX):
        candidate = code.removeprefix(PREFIX)
        if candidate in REASONS:
            return candidate
    return "unclassified" if code == "media_link_attempt_blocked" else None
