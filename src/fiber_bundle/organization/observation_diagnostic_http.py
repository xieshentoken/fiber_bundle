"""Bounded read-failure evidence for a rejected maintenance action, without ledger writes."""

import re
from typing import Any

from starlette.responses import JSONResponse

from ..cloud_observation import ObservationError


def bounded_detail(error: ObservationError) -> dict[str, Any]:
    """Expose diagnostic metadata only; never source text or retained proof blocks."""
    source = error.detail
    detail: dict[str, Any] = {}
    for key in ("reason", "phase"):
        value = source.get(key)
        if isinstance(value, str) and re.fullmatch(r"[a-z_]{1,100}", value):
            detail[key] = value
    path = source.get("path")
    if (
        isinstance(path, str)
        and len(path) <= 2048
        and not path.startswith("/")
        and ".." not in path.split("/")
    ):
        detail["path"] = path
    detail["changed_fields"] = [
        field
        for field in source.get("changed_fields", [])[:9]
        if isinstance(field, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,39}", field)
    ]
    detail["retryable_read"] = source.get("retryable_read") is True
    if (
        source.get("reason") == "retained_evidence_limit"
        and source.get("phase") == "moc_retained_members"
    ):
        mid = source.get("moc_id")
        if isinstance(mid, str) and re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", mid
        ):
            detail["moc_id"] = mid
        for key in ("actual_bytes", "limit_bytes"):
            value = source.get(key)
            if type(value) is int and 0 <= value <= 2**53 - 1:
                detail[key] = value
        missing = source.get("missing_members")
        if isinstance(missing, list):
            detail["missing_members"] = [
                member
                for member in missing[:20]
                if isinstance(member, str)
                and 0 < len(member) <= 2048
                and not member.startswith("/")
                and ".." not in member.split("/")
            ]
    return detail


def response(error: ObservationError) -> JSONResponse:
    from .read_errors import response as read_error_response

    code = str(error)
    # Match the existing global handler's placement-conflict/storage status.
    specific = read_error_response(error)
    status = (
        specific[0]
        if specific
        else (409 if re.fullmatch(r"(?:placement|layout)_[a-z_]{1,80}", code) else 503)
    )
    if code == "note directory changed":
        code = "organization_directory_observation_changed"
    elif not re.fullmatch(r"[a-z][a-z0-9_]{0,95}", code):
        code = "storage_unavailable"
    return JSONResponse({"error": code, "error_detail": bounded_detail(error)}, status_code=status)
