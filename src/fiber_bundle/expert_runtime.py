"""Pinned full fake-expert compiler receipts, distinct from the ordinary PDF extractor."""

import re
from typing import Any

from .config import InvalidInput
from .inputs import canonical
from .objects import digest

PROFILES: dict[str, set[str | None]] = {
    "native-text": {None, "pypdf-native"},
    "scan-text": {"paddleocr-ppocrv6", "pymupdf-tesseract", "docling"},
    "scan-structure": {"paddleocr-ppstructure-v3"},
    "visual": {"pdftoppm", "paddleocr-ppstructure-v3", "docling"},
}


def selection(profile: str, backend: str | None) -> None:
    if profile not in PROFILES or backend not in PROFILES[profile]:
        raise InvalidInput("expert_explicit_profile_backend_required")


def verify(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "image",
        "release",
        "protocol",
        "skill_sha256",
        "qualification",
        "qualification_sha256",
        "knowledge_verified",
    }:
        raise InvalidInput("expert_runtime_receipt")
    q = value["qualification"]
    if (
        value["schema"] != "fiber-full-fake-expert/v1"
        or value["release"] != "1.2.0"
        or value["protocol"] != "fake-expert-ux-cli-v0.2"
        or value["knowledge_verified"] is not False
        or not isinstance(value["image"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value["image"]) is None
        or not isinstance(value["skill_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", value["skill_sha256"]) is None
        or not isinstance(q, dict)
        or set(q)
        != {
            "schema",
            "image",
            "skill_sha256",
            "doctor_sha256",
            "passed",
            "scope",
            "knowledge_verified",
        }
        or q["schema"] != "fiber-full-expert-availability/v1"
        or q["image"] != value["image"]
        or q["skill_sha256"] != value["skill_sha256"]
        or q["passed"] is not True
        or q["scope"] != "compiler_availability"
        or q["knowledge_verified"] is not False
        or not isinstance(q["doctor_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", q["doctor_sha256"]) is None
        or value["qualification_sha256"] != digest(canonical(q).encode())
    ):
        raise InvalidInput("expert_runtime_binding")


def receipt(image: str, skill_sha: str, doctor: dict[str, Any]) -> dict[str, Any]:
    if doctor.get("passed") is not True:
        raise InvalidInput("expert_doctor_failed")
    qualification = {
        "schema": "fiber-full-expert-availability/v1",
        "image": image,
        "skill_sha256": skill_sha,
        "doctor_sha256": digest(canonical(doctor).encode()),
        "passed": True,
        "scope": "compiler_availability",
        "knowledge_verified": False,
    }
    value = {
        "schema": "fiber-full-fake-expert/v1",
        "image": image,
        "release": "1.2.0",
        "protocol": "fake-expert-ux-cli-v0.2",
        "skill_sha256": skill_sha,
        "qualification": qualification,
        "qualification_sha256": digest(canonical(qualification).encode()),
        "knowledge_verified": False,
    }
    verify(value)
    return value
