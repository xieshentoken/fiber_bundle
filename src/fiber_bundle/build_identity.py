"""Recorded candidate provenance; absent metadata must never imply a known build."""

import hashlib
import json
import re
from pathlib import Path

from .config import InvalidInput
from .inputs import canonical
from .query_routing_files import read

SHA256 = re.compile(r"[0-9a-f]{64}")


def candidate(version: str, source_files: dict[str, str]) -> dict[str, str]:
    """Bind a public distribution snapshot and its runtime files; exclude this generated JSON."""
    runtime = {k: v for k, v in source_files.items() if k.startswith("src/fiber_bundle/")}
    source_sha = hashlib.sha256(canonical(source_files).encode()).hexdigest()
    return {
        "schema": "candidate-identity/v1",
        "version": version,
        "build_id": version + ".src." + source_sha,
        "source_sha256": source_sha,
        "runtime_sha256": hashlib.sha256(canonical(runtime).encode()).hexdigest(),
    }


def inspect() -> dict[str, str | None]:
    """Read only bounded installed metadata. This is not a live source integrity check."""
    path = Path(__file__).with_name("build_identity.json")
    if not path.exists() and not path.is_symlink():
        return {"state": "unidentified", "build_id": None}
    try:
        value = json.loads(read(path, 4096))
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "version", "build_id", "source_sha256", "runtime_sha256"}
            or value["schema"] != "candidate-identity/v1"
            or not isinstance(value["version"], str)
            or re.fullmatch(r"[0-9A-Za-z.+-]{1,80}", value["version"]) is None
            or any(
                not isinstance(value[k], str) or SHA256.fullmatch(value[k]) is None
                for k in ("source_sha256", "runtime_sha256")
            )
            or value["build_id"] != value["version"] + ".src." + value["source_sha256"]
        ):
            raise ValueError()
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise InvalidInput("build_identity_invalid") from exc
    return {"state": "recorded", **value}
