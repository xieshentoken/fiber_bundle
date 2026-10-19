"""Bounded local ASR resource policy and immutable source/glossary processing plan."""

import re
from typing import Any

from .config import InvalidInput
from .media_files import canonical
from .media_glossary_contract import validate as glossary


def policy(value: object) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {
        "image",
        "model_lock_sha256",
        "timeout_seconds",
        "memory_mib",
        "cpus",
        "max_duration_ms",
        "daily_maximum",
        "asr_enabled",
        "qualified",
    }:
        raise InvalidInput("media_processing_policy")
    if not isinstance(value["image"], str) or not re.fullmatch(
        r"sha256:[a-f0-9]{64}", value["image"]
    ):
        raise InvalidInput("media_processing_image")
    if not isinstance(value["model_lock_sha256"], str) or not re.fullmatch(
        r"[a-f0-9]{64}", value["model_lock_sha256"]
    ):
        raise InvalidInput("media_processing_model_lock")
    for key, low, high in [
        ("timeout_seconds", 1, 900),
        ("memory_mib", 512, 6144),
        ("cpus", 1, 4),
        ("max_duration_ms", 1, 60000),
        ("daily_maximum", 1, 100),
    ]:
        if type(value[key]) is not int or not low <= value[key] <= high:
            raise InvalidInput("media_processing_resource_limit")
    for key in ("asr_enabled", "qualified"):
        if type(value[key]) is not bool:
            raise InvalidInput("media_processing_policy_flag")
    return value


def plan(value: object) -> dict[str, Any]:
    if (
        not isinstance(value, dict)
        or set(value)
        != {
            "schema",
            "parent_result_sha256",
            "source_sha256",
            "source_bytes",
            "request",
            "duration_ms",
            "glossary_revision",
            "glossary_sha256",
            "glossary",
            "policy",
            "asr",
        }
        or value["schema"] != "fiber-media-processing-plan/v1"
    ):
        raise InvalidInput("media_processing_plan")
    for key in ("parent_result_sha256", "source_sha256", "glossary_sha256"):
        if not isinstance(value[key], str) or not re.fullmatch("[a-f0-9]{64}", value[key]):
            raise InvalidInput("media_processing_plan_sha")
    p = policy(value["policy"])
    g = glossary(value["glossary"])
    import hashlib

    if hashlib.sha256(canonical(g)).hexdigest() != value["glossary_sha256"]:
        raise InvalidInput("media_processing_glossary_sha")
    if type(value["asr"]) is not bool or (
        value["asr"] and (not p["asr_enabled"] or not p["qualified"])
    ):
        raise InvalidInput("media_processing_asr_unqualified")
    if (
        type(value["duration_ms"]) is not int
        or not 1 <= value["duration_ms"] <= p["max_duration_ms"]
    ):
        raise InvalidInput("media_processing_duration_budget")
    if type(value["glossary_revision"]) is not int or not 1 <= value["glossary_revision"] <= 100:
        raise InvalidInput("media_processing_revision")
    if type(value["source_bytes"]) is not int or not 1 <= value["source_bytes"] <= 8 * 1024 * 1024:
        raise InvalidInput("media_processing_source_size")
    from .media_candidate import request

    request(value["request"])
    if value["request"]["declared_duration_ms"] != value["duration_ms"] or (
        value["asr"] != (value["request"]["subtitle_kind"] is None)
    ):
        raise InvalidInput("media_processing_parent_mismatch")
    return value
