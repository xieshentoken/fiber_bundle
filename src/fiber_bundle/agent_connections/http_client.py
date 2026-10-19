"""Fixed loopback transport; no redirects, proxies, credential arguments or automatic retry."""

import json
import re
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from ..config import InvalidInput
from ..model_gateway import NoRedirect
from . import files


class ClientError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


class Client:
    def __init__(self, connection_file: Path):
        value = files.private_json(connection_file, 4096)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema", "endpoint", "credential_file", "connection_id"}
            or value["schema"] != "fiber-agent-transport/v1"
        ):
            raise InvalidInput("agent_transport_invalid")
        if not isinstance(value["endpoint"], str) or not re.fullmatch(
            r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", value["endpoint"]
        ):
            raise InvalidInput("agent_endpoint_invalid")
        if int(value["endpoint"].rsplit(":", 1)[1]) > 65535:
            raise InvalidInput("agent_endpoint_invalid")
        self.endpoint = value["endpoint"]
        self.identity = value["connection_id"]
        self.credential_path = Path(value["credential_file"])
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path: str, value: object = None) -> Any:
        token = files.private_json(self.credential_path, 1024)
        if (
            not isinstance(token, dict)
            or set(token) != {"token"}
            or not isinstance(token["token"], str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token["token"])
        ):
            raise ClientError("agent_credential_invalid")
        raw = (
            json.dumps(value, ensure_ascii=False, allow_nan=False).encode()
            if value is not None
            else None
        )
        if raw and len(raw) > 131072:
            raise ClientError("agent_request_limit")
        req = urllib.request.Request(
            self.endpoint + path,
            data=raw,
            headers={
                "Authorization": "Bearer " + token["token"],
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )
        try:
            with self.opener.open(
                req, timeout=30 if path == "/v1/agent-knowledge/expert" else 10
            ) as response:
                if response.headers.get_content_type() != "application/json":
                    raise ClientError("agent_response_invalid")
                # Binary resources are fetched only by explicit expert ID/capability requests.
                capacity = 524288
                if isinstance(value, dict) and (
                    (path == "/v1/agent-knowledge/evidence" and value.get("kind") == "expert_media")
                    or (
                        path == "/v1/agent-knowledge/expert"
                        and (
                            value.get("output_capability") in {"images", "full"}
                            or value.get("output_format") == "markdown"
                        )
                    )
                ):
                    capacity = (
                        12 * 1024 * 1024
                        if path == "/v1/agent-knowledge/evidence"
                        else 96 * 1024 * 1024
                    )
                body = response.read(capacity + 1)
                if len(body) > capacity:
                    raise ClientError("agent_response_limit")
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            codes = {
                401: "agent_unauthorized",
                403: "agent_paused",
                404: "agent_scope_denied",
                409: "agent_source_stale",
            }
            raise ClientError(codes.get(exc.code, "agent_http_failed")) from None
        except (OSError, ValueError):
            raise ClientError("agent_service_unavailable") from None

    def context(self) -> dict[str, Any]:
        from .contracts import ConnectionContext

        value = ConnectionContext.model_validate(self.request("/v1/agent-context"))
        if value.connection_id != self.identity:
            raise ClientError("agent_identity_changed")
        return value.model_dump()

    def vaults(self) -> Any:
        return self.request("/v1/vaults")

    def search(self, query: str, vault_ids: tuple[str, ...], limit: int) -> Any:
        return self.request(
            "/v1/search", {"query": query, "vault_ids": list(vault_ids), "limit": limit}
        )

    def verify(self, prepared: Any) -> None:
        value = self.request(
            "/v1/agent-evidence/verify",
            {
                "context": json.loads(prepared.context_json),
                "sources": json.loads(prepared.evidence_json),
            },
        )

        if (
            not isinstance(value, dict)
            or set(value) != {"verified"}
            or value["verified"] is not True
        ):
            raise ClientError("agent_verification_failed")

    def index(self, vault_id: str) -> Any:
        from urllib.parse import quote

        return self.request("/v1/vaults/" + quote(vault_id, safe="") + "/note-index")

    def catalog(self, vault_ids: list[str] | None = None, offset: int = 0, limit: int = 20) -> Any:
        return self.request(
            "/v1/agent-knowledge/catalog",
            {"vault_ids": vault_ids, "offset": offset, "limit": limit},
        )

    def mocs(self, vault_id: str, moc_id: str | None = None, offset: int = 0) -> Any:
        return self.request(
            "/v1/agent-knowledge/mocs", {"vault_id": vault_id, "moc_id": moc_id, "offset": offset}
        )

    def expert(
        self,
        module_id: str,
        expected_version: str,
        question: str,
        limit: int = 5,
        output_capability: str = "unknown",
        output_format: str = "json",
    ) -> Any:
        return self.request(
            "/v1/agent-knowledge/expert",
            {
                "module_id": module_id,
                "expected_version": expected_version,
                "question": question,
                "limit": limit,
                "output_capability": output_capability,
                "output_format": output_format,
            },
        )

    def evidence(self, body: dict[str, Any]) -> Any:
        return self.request("/v1/agent-knowledge/evidence", body)
