"""An explicit owner query selects only the configured bound Vault, never recent batches."""

from typing import Any

from .config import InvalidInput

PREFIXES = ("查询本库：", "查询本库:")


def question(value: str) -> str:
    if not isinstance(value, str) or not value.startswith(PREFIXES):
        raise InvalidInput("explicit bound-Vault query required")
    result = value[len(PREFIXES[0]) :].strip()
    if not result or len(result) > 2000:
        raise InvalidInput("bound-Vault query requires a question")
    return result


def identity(batch_id: str) -> str:
    return f"weixin-task:{batch_id}:notes:answer"


def existing(plan: dict[str, Any] | None) -> bool:
    return bool(plan and plan.get("query_existing") is True)


def snapshot(request: dict[str, Any], evidence: object) -> object:
    """Old workers cannot validate a note query without its Weixin cancellation guard."""
    submission = request.get("submission_id")
    if (
        request.get("session_id") is None
        and isinstance(submission, str)
        and submission.startswith("weixin-task:")
    ):
        return {
            "schema": "weixin-note-evidence/v1",
            "submission_id": submission,
            "evidence": evidence,
        }
    return evidence
