"""Offline answer-envelope preflight: never emit or send the prepared cloud payload."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .query_routing_boundary import cloud_answer_request
from .query_routing_config import QueryRoutingConfig
from .query_routing_files import read

MAX_INPUT_BYTES = 131072


def preview(
    config: QueryRoutingConfig, path: Path, *, backup_index: int | None = None
) -> dict[str, Any]:
    """Check a caller-selected file and bind only redacted digests to the current policy."""
    policy = config.answer_policy(backup_index)
    raw = read(path, MAX_INPUT_BYTES)
    try:
        value = json.loads(
            raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
        )
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise InvalidInput("answer_preview_json_invalid") from exc
    request = cloud_answer_request(config, value, backup_index=backup_index)
    # Match complete()'s current wire serialization, including escaped Unicode bytes.
    wire = json.dumps(request, allow_nan=False).encode("utf-8")
    if len(wire) > policy.max_request_bytes:
        raise InvalidInput("answer_preview_request_too_large")
    result = config.inspect()
    result["answer_preview"] = {
        "state": "prepared",
        "input_sha256": hashlib.sha256(raw).hexdigest(),
        "request_sha256": hashlib.sha256(wire).hexdigest(),
        "input_bytes": len(raw),
        "answer_utf8_bytes": len(value["answer"].encode("utf-8")),
        "request_bytes": len(wire),
        "boundary_checked": True,
        "provenance_verified": False,
        "semantic_privacy_verified": False,
        "requests_sent": 0,
    }
    if backup_index is not None:
        result["answer_preview"]["target"] = {
            "role": "declared_backup",
            "backup_index": backup_index,
            "configuration_sha256": policy.digest,
            "declared_reserve_nusd": policy.reserve_nusd,
            "declared_task_limit_nusd": policy.task_limit_nusd,
            "declared_daily_limit_nusd": policy.daily_limit_nusd,
            "approval_verified": False,
            "budget_reserved": False,
            "automatic_switch": False,
        }
    return result
