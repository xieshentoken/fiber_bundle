"""Separate content completeness from the user's permission to save an unchecked draft."""

from typing import Any


def checks(settings: dict[str, Any]) -> bool:
    return settings.get("policy_version", 1) == 1 or bool(settings.get("completeness_check", True))


def unchecked(
    source: str, result: str, *, task: str, gaps: list[str] | None = None
) -> dict[str, Any]:
    return {
        "task_id": task,
        "source_sha256": source,
        "result_sha256": result,
        "decision": "save_unchecked",
        "completeness_status": "not_checked",
        "save_basis": "explicit_user_policy",
        "known_gaps": gaps or [],
        "reasons": ["content_completeness_not_checked"],
        "units": [],
        "unverified_dimensions": ["content_completeness", "semantic_correctness", "human_review"],
    }


def status(assessment: dict[str, Any]) -> dict[str, Any]:
    if "completeness_status" not in assessment:
        assessment["completeness_status"] = (
            "passed" if assessment["decision"] == "accept" else "partial"
        )
        assessment["known_gaps"] = (
            [] if assessment["decision"] == "accept" else assessment.get("reasons", [])
        )
    return assessment


def saveable(assessment: dict[str, Any]) -> bool:
    return assessment["decision"] in {"accept", "save_unchecked"}
