"""Pure M10 preparation contracts, deliberately not wired to a sender or worker."""

import json
from typing import Any

from .cloud_answer_edit import PROMPT
from .config import InvalidInput
from .query_routing_config import QueryRoutingConfig


def cloud_answer_request(
    config: QueryRoutingConfig, value: object, *, backup_index: int | None = None
) -> dict[str, Any]:
    """Accept only the future local answer envelope, never a retrieval/result envelope.

    This checks shape, not provenance or semantic privacy. Future execution must first
    authenticate the local result and recheck permissions; answer excerpts may leave host.
    """
    policy = config.answer_policy(backup_index)
    if not isinstance(value, dict) or set(value) != {"schema", "answer"}:
        raise InvalidInput("cloud_answer_fields_invalid")
    answer = value["answer"]
    if value["schema"] != "local-answer/v1" or not isinstance(answer, str) or not answer.strip():
        raise InvalidInput("cloud_answer_invalid")
    try:
        if len(answer.encode()) > 16384:
            raise InvalidInput("cloud_answer_invalid")
    except UnicodeError as exc:
        raise InvalidInput("cloud_answer_invalid") from exc
    result = {
        "messages": [
            {
                "role": "system",
                "content": PROMPT,
            },
            {"role": "user", "content": json.dumps({"answer": answer}, ensure_ascii=False)},
        ]
    }
    # The existing provider preparation adds its bounded output settings and model.
    from .model_gateway import prepare

    return prepare(policy, result)


def local_failure(config: QueryRoutingConfig, category: str) -> dict[str, Any]:
    if category not in {"unavailable", "timeout", "cancelled", "rejected"}:
        raise InvalidInput("local_failure_category_invalid")
    return {
        "state": "stopped",
        "reason": "local_" + category,
        "configuration_sha256": config.digest,
        "cloud_requests": [],
        "automatic_switch": False,
    }
