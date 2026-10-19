"""Explicit local inference transport; the host dispatcher owns scope and call reservations."""

import json
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import asdict
from http.client import HTTPException
from pathlib import Path
from typing import Any

from .answer_model_output import reject_constant, unique_object
from .config import InvalidInput
from .local_query_contract import accept, prepare
from .local_query_process import run as run_worker
from .model_gateway import ModelFailure, NoRedirect
from .query_routing_config import LocalQueryConfig
from .query_routing_credentials import load as load_credential

MAX_RESPONSE_BYTES = 1048576
MAX_WORKER_INPUT = 266240
REASONS = frozenset(
    {
        "local_authentication_required",
        "local_redirect_refused",
        "local_http_rejected",
        "local_response_invalid",
        "local_response_too_large",
        "local_unavailable",
        "local_timeout",
        "local_credential_format_unsupported",
        "local_credential_file_rejected",
        "local_credential_invalid",
    }
)


def _json(raw: bytes) -> Any:
    return json.loads(
        raw.decode("utf-8"), object_pairs_hook=unique_object, parse_constant=reject_constant
    )


def _request(local: LocalQueryConfig, payload: dict[str, Any], key: str | None) -> dict[str, Any]:
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(
        local.endpoint,
        data=json.dumps(payload, allow_nan=False).encode(),
        headers=headers,
        method="POST",
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    try:
        with opener.open(request, timeout=local.timeout_seconds) as response:
            if response.status != 200:
                raise ModelFailure("local_http_rejected")
            if (
                response.headers.get_content_type() != "application/json"
                or response.headers.get("Content-Encoding", "identity").lower() != "identity"
                or any(
                    len(response.headers.get_all(name, [])) > 1
                    for name in (
                        "Content-Length",
                        "Content-Type",
                        "Content-Encoding",
                        "Transfer-Encoding",
                    )
                )
                or (
                    response.headers.get("Content-Length") is not None
                    and response.headers.get("Transfer-Encoding") is not None
                )
            ):
                raise ModelFailure("local_response_invalid")
            size = response.headers.get("Content-Length")
            if size is not None:
                if not re.fullmatch(r"[0-9]{1,10}", size):
                    raise ModelFailure("local_response_invalid")
                if int(size) > MAX_RESPONSE_BYTES:
                    raise ModelFailure("local_response_too_large")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise ModelFailure("local_response_too_large")
            if size is not None and len(raw) != int(size):
                raise ModelFailure("local_response_invalid")
            return accept(local, _json(raw))
    except urllib.error.HTTPError as exc:
        exc.close()
        reason = (
            "local_authentication_required"
            if exc.code in {401, 403}
            else ("local_redirect_refused" if 300 <= exc.code < 400 else "local_http_rejected")
        )
        raise ModelFailure(reason) from None
    except TimeoutError:
        raise ModelFailure("local_timeout") from None
    except (urllib.error.URLError, OSError, HTTPException):
        raise ModelFailure("local_unavailable") from None
    except (ValueError, UnicodeError, InvalidInput, RecursionError) as exc:
        if isinstance(exc, ModelFailure):
            raise
        raise ModelFailure("local_response_invalid") from None


def complete(
    local: LocalQueryConfig,
    value: object,
    *,
    allow_inference: bool = False,
    should_stop: Callable[[], bool] | None = None,
    deadline: float | None = None,
) -> dict[str, Any]:
    """One bounded POST, isolated key read. Not an authorization or budget decision.

    Host-owned local dispatch opts in after reserving a durable attempt and rechecking
    scope. A stop callback can halt the credential child, not the provider computation.
    """
    if allow_inference is not True:
        raise InvalidInput("local_inference_not_enabled")
    if should_stop is not None and not callable(should_stop):
        raise InvalidInput("local_stop_check_invalid")
    if deadline is not None and (type(deadline) not in (int, float) or not math.isfinite(deadline)):
        raise InvalidInput("local_deadline_invalid")
    payload = prepare(local, value)
    wire = json.dumps({"local": asdict(local), "payload": payload}, allow_nan=False).encode()
    if len(wire) > MAX_WORKER_INPUT:
        raise InvalidInput("local_request_too_large")
    timeout = (
        local.timeout_seconds
        if deadline is None
        else min(local.timeout_seconds, deadline - time.monotonic())
    )
    if timeout <= 0:
        raise ModelFailure("local_timeout")
    try:
        result = run_worker(wire, timeout, should_stop)
    except subprocess.TimeoutExpired:
        # subprocess.run terminates and reaps the worker; the service may still be computing.
        raise ModelFailure("local_timeout") from None
    except OSError:
        raise ModelFailure("local_unavailable") from None
    if result.returncode or len(result.stdout) > MAX_RESPONSE_BYTES + 4096:
        raise ModelFailure("local_response_invalid")
    try:
        reply = _json(result.stdout)
        if not isinstance(reply, dict) or type(reply.get("ok")) is not bool:
            raise ValueError("worker_reply_invalid")
        if reply["ok"] is False:
            if set(reply) != {"ok", "reason"} or reply["reason"] not in REASONS:
                raise ValueError("worker_reply_invalid")
            raise ModelFailure(reply["reason"])
        if set(reply) != {"ok", "body"}:
            raise ValueError("worker_reply_invalid")
        return accept(local, reply["body"])
    except ModelFailure:
        raise
    except (ValueError, TypeError, UnicodeError, InvalidInput, RecursionError):
        raise ModelFailure("local_response_invalid") from None


def _main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_WORKER_INPUT + 1)
        if len(raw) > MAX_WORKER_INPUT:
            raise InvalidInput("worker_input_invalid")
        value = _json(raw)
        if not isinstance(value, dict) or set(value) != {"local", "payload"}:
            raise InvalidInput("worker_input_invalid")
        local = LocalQueryConfig(**value["local"])
        # Revalidate the endpoint and exact host envelope before reading credentials.
        local.validate()
        payload = value["payload"]
        if (
            not isinstance(payload, dict)
            or set(payload) != {"model", "messages", "max_tokens", "stream"}
            or (
                type(payload.get("max_tokens")) is not int
                or type(payload.get("stream")) is not bool
                or payload != prepare(local, {"messages": payload.get("messages")})
            )
        ):
            raise InvalidInput("worker_input_invalid")
        key = None
        if local.secret_file is not None:
            key, _, error = load_credential(Path(local.secret_file))
            if error is not None:
                raise ModelFailure("local_" + error)
        reply = {"ok": True, "body": _request(local, payload, key)}
    except ModelFailure as exc:
        reply = {"ok": False, "reason": exc.code}
    except (ValueError, TypeError, InvalidInput, UnicodeError, RecursionError):
        reply = {"ok": False, "reason": "local_response_invalid"}
    print(json.dumps(reply, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
