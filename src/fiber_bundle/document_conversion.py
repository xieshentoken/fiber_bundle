"""Closed receipts for an explicitly qualified offline DOC-to-DOCX runtime."""

import re
from typing import Any

from .document_contract import DocumentError
from .inputs import canonical
from .material_contract import sha


def verify_runtime(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "image",
        "runtime_files_sha256",
        "qualification",
        "qualification_sha256",
        "accuracy_verified",
    }:
        raise DocumentError("conversion_runtime_receipt")
    q = value["qualification"]
    files = value["runtime_files_sha256"]
    if (
        value["schema"] != "fiber-doc-conversion/v1"
        or not isinstance(value["image"], str)
        or re.fullmatch(r"sha256:[0-9a-f]{64}", value["image"]) is None
        or value["accuracy_verified"] is not False
        or not isinstance(files, dict)
        or set(files) != {"soffice", "converter", "decoder_packages"}
        or any(
            not isinstance(v, str) or re.fullmatch(r"[0-9a-f]{64}", v) is None
            for v in files.values()
        )
        or not isinstance(q, dict)
        or set(q)
        != {
            "schema",
            "image",
            "runtime_files_sha256",
            "fixture_sha256",
            "attempts",
            "scope",
            "accuracy_verified",
        }
        or q["schema"] != "fiber-doc-availability/v1"
        or q["image"] != value["image"]
        or q["runtime_files_sha256"] != files
        or q["scope"] != "synthetic_availability"
        or q["accuracy_verified"] is not False
        or not isinstance(q["fixture_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", q["fixture_sha256"]) is None
        or sha(canonical(q).encode()) != value["qualification_sha256"]
    ):
        raise DocumentError("conversion_runtime_binding")
    attempts = q["attempts"]
    if not isinstance(attempts, list) or len(attempts) != 3:
        raise DocumentError("conversion_qualification_attempts")
    for ordinal, item in enumerate(attempts, 1):
        if (
            not isinstance(item, dict)
            or set(item) != {"ordinal", "state", "text_sha256"}
            or type(item["ordinal"]) is not int
            or item["ordinal"] != ordinal
            or item["state"] != "passed"
            or not isinstance(item["text_sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["text_sha256"]) is None
            or item["text_sha256"] != attempts[0]["text_sha256"]
        ):
            raise DocumentError("conversion_qualification_attempts")


def verify_mapping(result: dict[str, Any], runtime: dict[str, Any]) -> None:
    actual = result.get("runtime", {})
    mapping = actual.get("conversion")
    if {key: value for key, value in actual.items() if key != "conversion"} != runtime or (
        not isinstance(mapping, dict)
        or set(mapping) != {"source_sha256", "derived_sha256", "derived_bytes", "derived_format"}
        or mapping["source_sha256"] != result["source_sha256"]
        or mapping["derived_format"] != "docx"
        or not isinstance(mapping["derived_sha256"], str)
        or re.fullmatch(r"[0-9a-f]{64}", mapping["derived_sha256"]) is None
        or type(mapping["derived_bytes"]) is not int
        or not 0 < mapping["derived_bytes"] <= 8 * 1024 * 1024
    ):
        raise DocumentError("conversion_result_binding")
