"""Versioned processing limits and immutable task plans, without model or filesystem I/O.

Legacy defaults remain unchanged. Native PDF file inputs use separately bounded
capacities; other extractors retain their existing source ceiling.
"""

import copy
import re
from typing import Any

from .config import InvalidInput
from .inputs import canonical
from .objects import digest

DEFAULT_LIMITS = {
    "source_bytes": 8 * 1024 * 1024,
    "pdf_pages": 100,
    "markdown_bytes": 1024 * 1024,
    "markdown_total_bytes": 1024 * 1024,
    "text_bytes": 1024 * 1024,
    "result_bytes": 4 * 1024 * 1024,
    "image_pixels": 16_000_000,
    "image_dimension": 4096,
    "batch_bytes": 32 * 1024 * 1024,
}
CAPACITY_LIMITS = {
    **DEFAULT_LIMITS,
    "source_bytes": 16 * 1024 * 1024,
    "pdf_pages": 300,
    "markdown_total_bytes": 4 * 1024 * 1024,
}
LEGACY_FIELDS = {"document_image", "article_image", "timeout_seconds"}
FIELDS = LEGACY_FIELDS | {"schema", "revision", "limits", "oversize_action"}
RUNTIME_FIELDS = {"ocr_runtime", "conversion_runtime"}
EXPERT_FIELDS = RUNTIME_FIELDS | {"expert_runtime"}
FORMATS = {
    "video",
    "audio",
    "markdown",
    "text",
    "pdf",
    "docx",
    "xlsx",
    "pptx",
    "image",
    "archive",
    "chat",
    "doc",
    "unknown",
}


def image(value: object) -> None:
    if value is not None and (
        not isinstance(value, str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
    ):
        raise InvalidInput("processing_fixed_image_required")


def limits(value: object) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(DEFAULT_LIMITS):
        raise InvalidInput("processing_limits_invalid")
    for key, maximum in CAPACITY_LIMITS.items():
        if type(value[key]) is not int or not 1 <= value[key] <= maximum:
            raise InvalidInput("processing_limit_outside_capacity:" + key)
    if value["markdown_bytes"] > value["markdown_total_bytes"]:
        raise InvalidInput("processing_markdown_limits_invalid")
    if value["source_bytes"] > value["batch_bytes"]:
        raise InvalidInput("processing_batch_limit_invalid")
    return dict(value)


def normalize(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) not in (
        LEGACY_FIELDS,
        FIELDS,
        FIELDS | RUNTIME_FIELDS,
        FIELDS | EXPERT_FIELDS,
    ):
        raise InvalidInput("processing_policy_invalid")
    legacy = set(value) == LEGACY_FIELDS
    for key in ("document_image", "article_image"):
        image(value[key])
        if legacy and value[key] is None:
            raise InvalidInput("processing_legacy_image_required")
    if type(value["timeout_seconds"]) is not int or not 1 <= value["timeout_seconds"] <= 300:
        raise InvalidInput("processing_timeout_invalid")
    if not legacy and (
        type(value["schema"]) is not int
        or value["schema"]
        != (
            4
            if set(value) == FIELDS | EXPERT_FIELDS
            else 3
            if set(value) == FIELDS | RUNTIME_FIELDS
            else 2
        )
        or type(value["revision"]) is not int
        or not 1 <= value["revision"] <= 2**31 - 1
        or not isinstance(value["oversize_action"], str)
        or value["oversize_action"] not in {"pause", "split"}
    ):
        raise InvalidInput("processing_policy_version_invalid")
    result = {
        **{k: value[k] for k in LEGACY_FIELDS},
        "schema": 2,
        "revision": 0 if legacy else value["revision"],
        "limits": dict(DEFAULT_LIMITS) if legacy else limits(value["limits"]),
        "oversize_action": "pause" if legacy else value["oversize_action"],
    }
    if set(value) in (FIELDS | RUNTIME_FIELDS, FIELDS | EXPERT_FIELDS):
        from .document_conversion import verify_runtime as conversion
        from .document_ocr import verify_runtime as ocr

        for key, validate in (("ocr_runtime", ocr), ("conversion_runtime", conversion)):
            if value[key] is not None:
                validate(value[key])
        result.update(schema=3, **{key: copy.deepcopy(value[key]) for key in RUNTIME_FIELDS})
        if "expert_runtime" in value:
            from .expert_runtime import verify

            if value["expert_runtime"] is not None:
                verify(value["expert_runtime"])
            result.update(schema=4, expert_runtime=copy.deepcopy(value["expert_runtime"]))
    return result


def defaults() -> dict[str, Any]:
    return {
        "schema": 2,
        "revision": 1,
        "document_image": None,
        "article_image": None,
        "timeout_seconds": 60,
        "limits": dict(DEFAULT_LIMITS),
        "oversize_action": "pause",
    }


def freeze(
    value: object,
    *,
    format: str,
    source_sha256: str,
    source_bytes: int,
    action: str = "process",
    engine: str = "ordinary",
    profile: str = "native",
    overrides: dict[str, int] | None = None,
    project_skill: dict[str, Any] | None = None,
) -> dict[str, Any]:
    policy = normalize(value)
    if (
        not isinstance(format, str)
        or format not in FORMATS
        or not isinstance(action, str)
        or action not in {"archive", "process"}
        or not isinstance(engine, str)
        or engine not in {"ordinary", "fake-expert"}
        or not isinstance(profile, str)
        or profile not in {"native", "ocr", "conversion"}
        or not isinstance(source_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
        or type(source_bytes) is not int
        or source_bytes < 1
    ) or (profile == "conversion" and format != "doc"):
        raise InvalidInput("processing_task_invalid")
    if overrides is not None and (
        not isinstance(overrides, dict) or set(overrides) - set(DEFAULT_LIMITS)
    ):
        raise InvalidInput("processing_overrides_invalid")
    effective = {**policy["limits"], **(overrides or {})}
    limits(effective)
    if any(effective[k] > policy["limits"][k] for k in DEFAULT_LIMITS):
        raise InvalidInput("processing_override_exceeds_policy")
    if source_bytes > effective["source_bytes"]:
        raise InvalidInput("processing_source_limit")
    if (engine == "fake-expert" and format != "pdf") or (
        profile == "ocr" and format not in {"pdf", "image"}
    ):
        raise InvalidInput("processing_engine_profile_invalid")
    plan = {
        "schema": "source-processing-task/v1",
        "policy": policy,
        "policy_sha256": digest(canonical(policy).encode()),
        "limits": effective,
        "format": format,
        "action": action,
        "engine": engine,
        "profile": profile,
        "source_sha256": source_sha256,
        "source_bytes": source_bytes,
        "model_required": action == "process" and engine == "fake-expert",
        "publication_allowed": False,
    }
    if project_skill is not None:
        from .project_skill_contract import validate

        if engine != "fake-expert" or action != "process" or profile != "native":
            raise InvalidInput("project_skill_native_pdf_required")
        binding = validate(project_skill)
        if binding["runtime"] != policy.get("expert_runtime"):
            raise InvalidInput("project_skill_binding_invalid")
        plan["project_skill"] = copy.deepcopy(binding)
    return {**plan, "plan_sha256": digest(canonical(plan).encode())}


def validate_plan(value: object) -> dict[str, Any]:
    try:
        if not isinstance(value, dict):
            raise ValueError()
        policy = value["policy"]
        # Revision zero is reserved for a frozen legacy policy, not a new saved config.
        if isinstance(policy, dict) and policy.get("revision") == 0:
            if policy != normalize({k: policy[k] for k in LEGACY_FIELDS}):
                raise ValueError()
            policy = {k: policy[k] for k in LEGACY_FIELDS}
        expected = freeze(
            policy,
            format=value["format"],
            source_sha256=value["source_sha256"],
            source_bytes=value["source_bytes"],
            action=value["action"],
            engine=value["engine"],
            profile=value["profile"],
            overrides=value["limits"],
            project_skill=value.get("project_skill"),
        )
        if canonical(expected) != canonical(value):
            raise ValueError()
        return copy.deepcopy(expected)
    except (KeyError, TypeError, ValueError, InvalidInput):
        raise InvalidInput("processing_frozen_plan_invalid") from None


def capability(plan: object) -> dict[str, Any]:
    value = validate_plan(plan)
    if value["action"] == "archive":
        return {"binding_ready": True, "model_required": False, "reason": "archive_only"}
    if value["source_bytes"] > DEFAULT_LIMITS["source_bytes"] and (
        value["format"] != "pdf" or value["profile"] != "native"
    ):
        reason = "format_source_capacity"
    elif value["engine"] == "fake-expert":
        reason = (
            "full_expert_binding_configured"
            if value["policy"].get("expert_runtime")
            else "full_fake_expert_not_integrated"
        )
    elif value["profile"] == "ocr":
        reason = (
            "worker_binding_configured"
            if value["policy"].get("ocr_runtime")
            else "ocr_profile_required"
        )
    elif value["profile"] == "conversion":
        reason = (
            "worker_binding_configured"
            if value["policy"].get("conversion_runtime")
            else "doc_converter_not_integrated"
        )
    elif value["format"] == "image":
        reason = "ocr_profile_required"
    elif value["format"] == "doc":
        reason = (
            "conversion_profile_required"
            if value["policy"].get("conversion_runtime")
            else "doc_converter_not_integrated"
        )
    elif value["format"] == "unknown":
        reason = "unsupported_format"
    elif value["policy"]["document_image"] is None:
        reason = "document_worker_not_configured"
    else:
        reason = "worker_binding_configured"
    return {
        "binding_ready": reason in {"worker_binding_configured", "full_expert_binding_configured"},
        "model_required": value["model_required"],
        "reason": reason,
        "runtime_verified": False,
    }


def worker_binding(plan: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
    """Select the explicitly frozen profile's image; no native/OCR fallback."""
    value = validate_plan(plan)
    runtime = value["policy"].get(value["profile"] + "_runtime")
    selected = runtime["image"] if runtime is not None else value["policy"]["document_image"]
    if not isinstance(selected, str):
        raise InvalidInput("document_worker_not_configured")
    if value["profile"] != "native" and runtime is None:
        raise InvalidInput("processing_runtime_not_configured")
    return selected, runtime


def check_result(plan: object, result: dict[str, Any]) -> None:
    value = validate_plan(plan)
    bound = value["limits"]
    if (result.get("source_sha256"), result.get("source_bytes")) != (
        value["source_sha256"],
        value["source_bytes"],
    ):
        raise InvalidInput("processing_result_source_changed")
    units = result.get("pages", result.get("segments", []))
    if value["format"] == "pdf" and len(units) > bound["pdf_pages"]:
        raise InvalidInput("processing_pdf_page_limit")
    if sum(len(u["text"].encode()) for u in units) > bound["text_bytes"]:
        raise InvalidInput("processing_text_limit")
    if (
        value["format"] == "image"
        and sum(
            u.get("locator", {}).get("width", 0) * u.get("locator", {}).get("height", 0)
            for u in units
        )
        > bound["image_pixels"]
    ):
        raise InvalidInput("processing_image_limit")
    for unit in units:
        locator = unit.get("locator", {})
        if "width" in locator and "height" in locator:
            width, height = locator["width"], locator["height"]
            if (
                max(width, height) > bound["image_dimension"]
                or width * height > bound["image_pixels"]
            ):
                raise InvalidInput("processing_image_limit")
    wire = {k: v for k, v in result.items() if k != "result_sha256"}
    if len(canonical(wire).encode()) > bound["result_bytes"]:
        raise InvalidInput("processing_result_limit")


def check_markdown(plan: object, files: dict[str, bytes]) -> None:
    bound = validate_plan(plan)["limits"]
    sizes = [len(v) for k, v in files.items() if k.endswith(".md")]
    if not sizes or any(n > bound["markdown_bytes"] for n in sizes):
        raise InvalidInput("processing_markdown_file_limit")
    if sum(sizes) > bound["markdown_total_bytes"]:
        raise InvalidInput("processing_markdown_total_limit")
