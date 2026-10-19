"""Opt-in loopback model listing; bounded, explicit credentials only, never inference."""

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from dataclasses import asdict
from http.client import HTTPException
from pathlib import Path
from typing import Any, NoReturn

from .config import InvalidInput
from .model_gateway import NoRedirect
from .query_routing_config import LocalQueryConfig, QueryRoutingConfig
from .query_routing_credentials import load as load_credential

MAX_RESPONSE_BYTES = 131072
MAX_MODELS = 256
MAX_PROBE_SECONDS = 10
REASONS = frozenset(
    {
        "model_listed",
        "model_not_listed",
        "authentication_required",
        "redirect_refused",
        "http_rejected",
        "response_invalid",
        "response_too_large",
        "unavailable",
        "timeout",
        "credential_format_unsupported",
        "credential_file_rejected",
        "credential_invalid",
        "credentials_not_configured",
    }
)


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate_field")
        result[key] = value
    return result


def _constant(value: str) -> NoReturn:
    raise ValueError("non_finite_json")


def _listed(body: bytes, model: str) -> bool:
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_object, parse_constant=_constant)
    if not isinstance(value, dict) or value.get("object") != "list":
        raise ValueError("model_list_invalid")
    data = value.get("data")
    if not isinstance(data, list) or len(data) > MAX_MODELS:
        raise ValueError("model_list_invalid")
    ids: set[str] = set()
    for item in data:
        if not isinstance(item, dict) or item.get("object") != "model":
            raise ValueError("model_record_invalid")
        identifier = item.get("id")
        if (
            not isinstance(identifier, str)
            or not 0 < len(identifier) <= 256
            or not identifier.isprintable()
            or identifier in ids
        ):
            raise ValueError("model_record_invalid")
        identifier.encode("utf-8")
        ids.add(identifier)
    return model in ids


def _request(local: LocalQueryConfig, key: str | None = None) -> str:
    local.validate()
    if local.secret_file is not None and key is None:
        raise InvalidInput("credential_probe_not_allowed")
    url = local.endpoint.removesuffix("/chat/completions") + "/models"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    headers = {"Accept": "application/json"}
    if key is not None:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with opener.open(
            request, timeout=min(local.timeout_seconds, MAX_PROBE_SECONDS)
        ) as response:
            if response.status != 200:
                return "http_rejected"
            if response.headers.get_content_type() != "application/json" or (
                response.headers.get("Content-Encoding", "identity").lower() != "identity"
            ):
                return "response_invalid"
            if any(
                len(response.headers.get_all(name, [])) > 1
                for name in (
                    "Content-Length",
                    "Content-Type",
                    "Content-Encoding",
                    "Transfer-Encoding",
                )
            ) or (
                response.headers.get("Content-Length") is not None
                and response.headers.get("Transfer-Encoding") is not None
            ):
                return "response_invalid"
            size = response.headers.get("Content-Length")
            if size is not None:
                if not re.fullmatch(r"[0-9]{1,10}", size):
                    return "response_invalid"
                if int(size) > MAX_RESPONSE_BYTES:
                    return "response_too_large"
            body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(body) > MAX_RESPONSE_BYTES:
                return "response_too_large"
            if size is not None and len(body) != int(size):
                return "response_invalid"
            return "model_listed" if _listed(body, local.model) else "model_not_listed"
    except urllib.error.HTTPError as exc:
        exc.close()
        if exc.code in {401, 403}:
            return "authentication_required"
        return "redirect_refused" if 300 <= exc.code < 400 else "http_rejected"
    except TimeoutError:
        return "timeout"
    except urllib.error.URLError as exc:
        return "timeout" if isinstance(exc.reason, TimeoutError) else "unavailable"
    except (ValueError, UnicodeError, RecursionError, HTTPException):
        return "response_invalid"
    except OSError:
        return "unavailable"


def probe(config: QueryRoutingConfig, *, read_credentials: bool = False) -> dict[str, Any]:
    """Validate again, then run one metadata attempt with a hard wall deadline."""
    if type(read_credentials) is not bool:
        raise InvalidInput("credential_probe_option_invalid")
    result = config.inspect()
    reason = "local_not_configured"
    attempts = 0
    credentials_read: bool | None = False
    local = config.local_query
    if local is not None:
        local.validate()
        reason = "credentials_not_configured" if read_credentials else "credentials_not_read"
        if (read_credentials and local.secret_file is not None) or (
            not read_credentials and local.secret_file is None
        ):
            attempts = 1
            credentials_read = None if read_credentials else False
            try:
                # A subprocess bounds slow headers and trickled bodies, not only socket inactivity.
                child = subprocess.run(
                    [sys.executable, "-m", "fiber_bundle.query_routing_probe"],
                    input=json.dumps(
                        {"local": asdict(local), "read_credentials": read_credentials}
                    ),
                    capture_output=True,
                    text=True,
                    timeout=min(local.timeout_seconds, MAX_PROBE_SECONDS),
                    check=False,
                )
                if child.returncode != 0:
                    raise ValueError("worker_failed")
                reply = json.loads(
                    child.stdout, object_pairs_hook=_object, parse_constant=_constant
                )
                if (
                    not isinstance(reply, dict)
                    or set(reply) != {"reason", "credentials_read"}
                    or not isinstance(reply["reason"], str)
                    or reply["reason"] not in REASONS
                    or (
                        reply["credentials_read"] is not None
                        and type(reply["credentials_read"]) is not bool
                    )
                    or (not read_credentials and reply["credentials_read"] is not False)
                ):
                    raise ValueError("worker_response_invalid")
                reason = reply["reason"]
                credentials_read = reply["credentials_read"]
            except subprocess.TimeoutExpired:
                reason = "timeout"
            except OSError:
                reason = "unavailable"
                credentials_read = False
            except (ValueError, RecursionError):
                reason = "unavailable"
    result["local_probe"] = {
        "state": "listed" if reason == "model_listed" else "failed" if attempts else "not_run",
        "reason": reason,
        "probe_attempts": attempts,
        "model_listed": (
            reason == "model_listed" if reason in {"model_listed", "model_not_listed"} else None
        ),
        "metadata_verified": reason in {"model_listed", "model_not_listed"},
    }
    if read_credentials:
        result["credentials_read"] = credentials_read
        result["local_probe"]["credential_read_requested"] = True
        result["local_probe"]["authentication_verified"] = False
    return result


def _main() -> int:
    """Private bounded worker protocol; only fixed reason codes leave the process."""
    credentials_read: bool | None = False
    try:
        payload = sys.stdin.read(8193)
        if len(payload) > 8192:
            raise ValueError("worker_input_too_large")
        value = json.loads(payload, object_pairs_hook=_object, parse_constant=_constant)
        if (
            not isinstance(value, dict)
            or set(value) != {"local", "read_credentials"}
            or type(value["read_credentials"]) is not bool
        ):
            raise ValueError("worker_input_invalid")
        local = LocalQueryConfig(**value["local"])
        local.validate()
        key = None
        if value["read_credentials"]:
            if local.secret_file is None:
                reason = "credentials_not_configured"
            else:
                key, credentials_read, error = load_credential(Path(local.secret_file))
                reason = error if error is not None else _request(local, key)
        else:
            reason = _request(local)
    except (TypeError, ValueError, InvalidInput, UnicodeError, RecursionError):
        reason = "response_invalid"
    print(json.dumps({"reason": reason, "credentials_read": credentials_read}))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
