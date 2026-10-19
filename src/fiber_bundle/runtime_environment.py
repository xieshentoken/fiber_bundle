"""Allowlisted Python worker environment; never inherit provider credentials or user sites."""

import os
from pathlib import Path


def child_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "PYTHONHOME",
        "FIBER_CLOUD_HELPER",
        "FIBER_CLOUD_HELPER_SHA256",
        "FIBER_TRASH_HELPER",
        "FIBER_TRASH_HELPER_SHA256",
    )
    value = {key: os.environ[key] for key in allowed if key in os.environ}
    value["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    value["PYTHONNOUSERSITE"] = "1"
    value["PYTHONDONTWRITEBYTECODE"] = "1"
    return value
