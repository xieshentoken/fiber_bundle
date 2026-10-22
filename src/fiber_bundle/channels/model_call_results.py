"""Durable paid responses bound to the exact policy and prepared request; no network retries."""

import hashlib
import json
from typing import Any

from ..config import Config, InvalidInput, contained, make_directory
from ..inputs import canonical
from ..query_policy_store import write_private
from ..query_routing_files import read

SCHEMA = "channel-query-result/v1"


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def usage(result: Any) -> tuple[int, int]:
    value = result.get("usage", {}) if isinstance(result, dict) else {}
    if not isinstance(value, dict) or any(
        type(value.get(k)) is not int or value[k] < 0
        for k in ("prompt_tokens", "completion_tokens")
    ):
        raise InvalidInput("channel_model_usage_unknown")
    return value["prompt_tokens"], value["completion_tokens"]


def exists(config: Config, call: str) -> bool:
    path = contained(config.state_root, "channel-query-results/" + call + ".json")
    try:
        path.lstat()
        return True
    except FileNotFoundError:
        return False


def save(config: Config, call: str, policy_digest: str, request_digest: str, result: Any) -> None:
    home = contained(config.state_root, "channel-query-results")
    make_directory(home)
    value = {
        "schema": SCHEMA,
        "call_id": call,
        "policy_sha256": policy_digest,
        "request_sha256": request_digest,
        "result_sha256": digest(result),
        "result": result,
    }
    write_private(home / (call + ".json"), canonical(value).encode())


def load(config: Config, call: str, policy_digest: str, request_digest: str) -> Any:
    path = contained(config.state_root, "channel-query-results/" + call + ".json")
    try:
        value = json.loads(read(path, 4 * 1024 * 1024, private=True))
        if (
            not isinstance(value, dict)
            or value.get("schema") != SCHEMA
            or value.get("call_id") != call
            or value.get("policy_sha256") != policy_digest
            or value.get("request_sha256") != request_digest
            or value.get("result_sha256") != digest(value.get("result"))
        ):
            raise ValueError("unverified response")
        usage(value["result"])
        return value["result"]
    except (OSError, ValueError, InvalidInput, KeyError) as error:
        raise InvalidInput("channel_model_result_unknown") from error
