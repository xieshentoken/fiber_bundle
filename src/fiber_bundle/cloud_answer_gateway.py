"""Single cloud editing call in a bounded credential child; no source access."""

import json
import subprocess
import sys
from collections.abc import Callable
from dataclasses import asdict
from typing import Any
from uuid import UUID

from . import model_gateway
from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput, StorageError
from .execution_config import ExecutionConfig, parse_execution_config
from .local_query_process import run
from .model_gateway import ModelFailure
from .query_routing_boundary import cloud_answer_request
from .query_routing_config import QueryRoutingConfig

MAX_INPUT = 266240
MAX_REPLY = 131072


def _json(raw: bytes) -> Any:
    return json.loads(raw.decode(), object_pairs_hook=unique_object, parse_constant=reject_constant)


def clean(body: dict[str, Any]) -> dict[str, Any]:
    """Keep usable accounting even when the assistant content is rejected."""
    usage = body.get("usage")
    result: dict[str, Any] = {
        "usage": {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens")}
        if isinstance(usage, dict)
        else None,
        "error_code": "cloud_response_rejected",
    }
    choices = body.get("choices")
    if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
        return result
    choice = choices[0]
    msg = choice.get("message")
    if (
        choice.get("finish_reason") != "stop"
        or not isinstance(msg, dict)
        or msg.get("role") != "assistant"
        or msg.get("tool_calls")
        or msg.get("function_call")
    ):
        return result
    value = msg.get("content")
    try:
        if not isinstance(value, str) or not value.strip() or len(value.encode()) > 16384:
            return result
    except UnicodeError:
        return result
    return {"usage": result["usage"], "answer": value}


def complete(
    policy: ExecutionConfig,
    run_id: str,
    answer: dict[str, str],
    *,
    allow_inference: bool = False,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    if allow_inference is not True:
        raise InvalidInput("cloud_inference_not_enabled")
    policy.validate()
    UUID(run_id)
    route = QueryRoutingConfig(None, policy, ())
    route.validate()
    payload = cloud_answer_request(route, answer)
    if len(json.dumps(payload).encode()) > policy.max_request_bytes:
        raise InvalidInput("cloud_request_too_large")
    wire = json.dumps({"policy": asdict(policy), "run_id": run_id, "answer": answer}).encode()
    if len(wire) > MAX_INPUT:
        raise InvalidInput("cloud_request_too_large")
    try:
        result = run(wire, min(policy.timeout_seconds, 120), should_stop, module=__name__)
    except subprocess.TimeoutExpired:
        raise ModelFailure("cloud_timeout") from None
    except OSError:
        raise ModelFailure("cloud_transport_failed") from None
    if result.returncode or len(result.stdout) > MAX_REPLY:
        raise ModelFailure("cloud_response_rejected")
    try:
        output = _json(result.stdout)
        if not isinstance(output, dict):
            raise ValueError()
        if set(output) == {"reason"} and output["reason"] == "cloud_transport_failed":
            raise ModelFailure(output["reason"])
        if set(output) not in ({"usage", "answer"}, {"usage", "error_code"}):
            raise ValueError()
        if "error_code" in output and output["error_code"] != "cloud_response_rejected":
            raise ValueError()
        return output
    except ModelFailure:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise ModelFailure("cloud_response_rejected") from None


def _main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise InvalidInput("worker_input_invalid")
        value = _json(raw)
        if not isinstance(value, dict) or set(value) != {"policy", "run_id", "answer"}:
            raise InvalidInput("worker_input_invalid")
        policy = parse_execution_config(value["policy"])
        policy.validate()
        run_id = str(UUID(value["run_id"]))
        route = QueryRoutingConfig(None, policy, ())
        route.validate()
        payload = cloud_answer_request(route, value["answer"])
        if len(json.dumps(payload).encode()) > policy.max_request_bytes:
            raise InvalidInput("cloud_request_too_large")
        reply = clean(model_gateway.complete(policy, run_id, payload, require_message=False))
    except (StorageError, ValueError, TypeError, OSError, RecursionError):
        reply = {"reason": "cloud_transport_failed"}
    print(json.dumps(reply, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
