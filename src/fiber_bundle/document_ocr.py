"""Explicit runtime receipts for isolated Docker OCR; no host OCR execution."""

import json
import re
from pathlib import Path
from typing import Any

from .document_capture import reject_duplicate
from .document_contract import DocumentError
from .inputs import canonical
from .material_contract import sha


def verify_runtime(value: object) -> None:
    if not isinstance(value, dict) or set(value) != {
        "schema",
        "image",
        "backend",
        "languages",
        "qualification_sha256",
        "qualification",
        "runtime_files_sha256",
        "accuracy_verified",
    }:
        raise DocumentError("ocr_runtime_receipt")
    if (
        value["schema"] != "fiber-ocr-runtime/v1"
        or value["backend"] != "tesseract/v1"
        or value["languages"] != "eng+chi_sim+chi_tra"
        or value["accuracy_verified"] is not False
    ):
        raise DocumentError("ocr_runtime_receipt")
    if (
        not isinstance(value["image"], str)
        or not re.fullmatch("sha256:[0-9a-f]{64}", value["image"])
        or not isinstance(value["qualification_sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", value["qualification_sha256"])
    ):
        raise DocumentError("ocr_runtime_pin")
    files = value["runtime_files_sha256"]
    if (
        not isinstance(files, dict)
        or set(files)
        not in (
            {"pdftoppm", "tesseract", "eng", "chi_sim", "chi_tra", "worker"},
            {
                "pdftoppm",
                "tesseract",
                "eng",
                "chi_sim",
                "chi_tra",
                "worker",
                "decoder",
                "decoder_packages",
            },
        )
        or any(
            not isinstance(v, str) or not re.fullmatch("[0-9a-f]{64}", v) for v in files.values()
        )
    ):
        raise DocumentError("ocr_runtime_files")

    q = value["qualification"]
    if not isinstance(q, dict) or set(q) != {
        "schema",
        "image",
        "fixture_sha256",
        "runtime_files_sha256",
        "attempts",
        "scope",
        "accuracy_verified",
    }:
        raise DocumentError("ocr_qualification_receipt")
    if (
        q["schema"] != "fiber-ocr-availability/v1"
        or q["scope"] != "synthetic_availability"
        or q["accuracy_verified"] is not False
        or q["image"] != value["image"]
        or q["runtime_files_sha256"] != files
        or sha(canonical(q).encode()) != value["qualification_sha256"]
        or not isinstance(q["fixture_sha256"], str)
        or not re.fullmatch("[0-9a-f]{64}", q["fixture_sha256"])
    ):
        raise DocumentError("ocr_qualification_binding")
    attempts = q["attempts"]
    if not isinstance(attempts, list) or len(attempts) != 3:
        raise DocumentError("ocr_qualification_attempts")
    for i, attempt in enumerate(attempts, 1):
        if (
            not isinstance(attempt, dict)
            or set(attempt) != {"ordinal", "state", "text_sha256", "boxes_sha256", "render_sha256"}
            or type(attempt["ordinal"]) is not int
            or attempt["ordinal"] != i
            or attempt["state"] != "passed"
            or any(
                not isinstance(attempt[k], str) or not re.fullmatch("[0-9a-f]{64}", attempt[k])
                for k in ("text_sha256", "boxes_sha256", "render_sha256")
            )
            or attempt["render_sha256"] != q["fixture_sha256"]
            or any(
                attempt[k] != attempts[0][k]
                for k in ("text_sha256", "boxes_sha256", "render_sha256")
            )
        ):
            raise DocumentError("ocr_qualification_attempts")


def runtime(path: Path) -> dict[str, Any]:
    if path.is_symlink() or path.resolve() != path or path.stat().st_size > 16384:
        raise DocumentError("ocr_runtime_receipt")
    value = json.loads(path.read_text(), object_pairs_hook=reject_duplicate)
    verify_runtime(value)
    assert isinstance(value, dict)
    return value
