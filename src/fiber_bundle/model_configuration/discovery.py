"""Bounded read-only model discovery; no inference and no persistent draft credentials."""

import json
import socket
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlencode, urlsplit

from ..config import Config
from . import providers
from .contracts import Connection
from .errors import ModelError


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None


def guard_address(base: str, local: bool = False) -> None:
    import ipaddress

    parsed = urlsplit(base)
    try:
        addresses = socket.getaddrinfo(parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ModelError("model_transport_failure") from None
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if (
            ip.is_link_local
            or ip.is_multicast
            or ip.is_unspecified
            or (ip.is_loopback and not local)
        ):
            raise ModelError("model_endpoint_forbidden")


def headers(provider: str, key: str | None) -> dict[str, str]:
    result = {"Accept": "application/json", "User-Agent": "FiberBundle-models/1"}
    if provider == "anthropic":
        result["anthropic-version"] = "2023-06-01"
        if key:
            result["x-api-key"] = key
    elif key:
        result["Authorization"] = "Bearer " + key
    return result


def http_error(error: urllib.error.HTTPError, *, discovery: bool = False) -> ModelError:
    codes = {
        401: "model_authentication_failed",
        403: "model_permission_denied",
        429: "model_rate_limited",
    }
    code = codes.get(error.code, "model_response_invalid")
    if discovery and error.code in (404, 405):
        code = "model_discovery_unsupported"
    if error.code in (402, 429):
        try:
            data = json.loads(error.read(8192))
            reason = data.get("error", {}).get("code") or data.get("error", {}).get("type")
            if reason in {"insufficient_quota", "insufficient_balance", "credit_balance_too_low"}:
                code = "model_provider_quota"
        except (ValueError, AttributeError, OSError):
            pass
    return ModelError(code)


def fetch(url: str, provider: str, key: str | None, *, local: bool) -> dict[str, Any]:
    guard_address(url, local)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers=headers(provider, key))
    try:
        with opener.open(request, timeout=8) as reply:
            raw = reply.read(1048577)
        if len(raw) > 1048576:
            raise ModelError("model_response_invalid")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ModelError("model_response_invalid")
        return data
    except urllib.error.HTTPError as exc:
        raise http_error(exc, discovery=True) from None
    except TimeoutError:
        raise ModelError("model_timeout") from None
    except urllib.error.URLError as exc:
        raise ModelError(
            "model_timeout" if isinstance(exc.reason, TimeoutError) else "model_transport_failure"
        ) from None
    except OSError:
        raise ModelError("model_transport_failure") from None
    except ValueError:
        raise ModelError("model_response_invalid") from None


def discover(config: Config, value: Connection) -> dict[str, Any]:
    from .. import database
    from ..execution_config import read_secret

    connection = providers.connection(value)
    key = value.api_key
    if value.credential_id:
        with database.connect(config.state_root) as db:
            row = db.execute(
                "SELECT * FROM model_credentials WHERE id=?", (value.credential_id,)
            ).fetchone()
        if not row or not row["active"] or row["revision"] != value.credential_revision:
            raise ModelError("model_revision_conflict")
        saved = json.loads(row["settings_json"])
        if {k: saved[k] for k in connection} != connection:
            raise ModelError("model_revision_conflict")
        if key is None:
            from pathlib import Path

            key = read_secret(Path(row["secret_file"]))
    if value.provider != "local" and not key:
        raise ModelError("model_key_required")
    items = []
    after = None
    for _ in range(10):
        url = connection["base_url"] + "/models"
        if after:
            url += "?" + urlencode({"after_id": after, "limit": 100})
        data = fetch(
            url,
            "anthropic"
            if connection["protocol"] == "anthropic-messages"
            else connection["provider"],
            key,
            local=value.provider == "local",
        )
        models = data.get("data")
        if not isinstance(models, list):
            raise ModelError("model_response_invalid")
        for item in models:
            identity = item.get("id") if isinstance(item, dict) else None
            if (
                not isinstance(identity, str)
                or not 1 <= len(identity) <= 200
                or any(ord(c) < 33 for c in identity)
            ):
                raise ModelError("model_response_invalid")
            capability = providers.capabilities(value.provider, identity, connection["protocol"])
            if capability["compatible"]:
                items.append(
                    {"id": identity, "name": item.get("display_name") or identity, **capability}
                )
        if len(items) > 1000:
            raise ModelError("model_response_invalid")
        if not data.get("has_more"):
            break
        cursor = data.get("last_id")
        if not isinstance(cursor, str) or cursor == after:
            raise ModelError("model_response_invalid")
        after = cursor
    else:
        raise ModelError("model_response_invalid")
    return {
        "models": list({m["id"]: m for m in items}.values()),
        "state": "discovered",
        "inference_verified": False,
        "provider_balance": None,
    }
