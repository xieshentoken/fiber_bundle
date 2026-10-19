"""Verify the exact local skill contract before starting a pinned Pi processing run."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .config import IntegrityError, InvalidInput


def selected(kind: str) -> dict[str, Any]:
    root = Path(__file__).with_name("executor")
    manifest = json.loads((root / "processing-skills.json").read_bytes())
    if manifest["pi_version"] != "0.87.1" or kind not in manifest["skills"]:
        raise InvalidInput("processing_skill_unknown")
    item = manifest["skills"][kind]
    if item["file"] != kind + "-processing.md":
        raise IntegrityError("processing_skill_path")
    raw = (root / item["file"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise IntegrityError("processing_skill_changed")
    return {"kind": kind, "pi_version": "0.87.1", **item}
