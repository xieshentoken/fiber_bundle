"""Read-only MCP knowledge tools over current connection-authorized HTTP APIs."""

import importlib.metadata
from pathlib import Path
from typing import Any, Literal

from .contracts import TurnRequest
from .http_client import Client


def server(connection_file: Path) -> Any:
    if importlib.metadata.version("mcp") != "1.26.0":
        raise ValueError("agent_mcp_incompatible")
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

    app = FastMCP("Fiber bundle", log_level="ERROR")
    client = Client(connection_file)
    annotations = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False)

    @app.tool(annotations=annotations)
    def fiber_list_vaults() -> list[dict[str, Any]]:
        """List only this connection's currently authorized query Vaults."""
        client.context()
        value = client.vaults()
        if not isinstance(value, list):
            raise ValueError("agent_response_invalid")
        return value

    @app.tool(annotations=annotations)
    def fiber_search(
        query: str, vault_ids: list[str] | None = None, limit: int = 5
    ) -> dict[str, Any]:
        """Search current notes with explicit scope; errors never mean no evidence."""
        ctx = client.context()
        request = TurnRequest(
            request_id="mcp", question=query, retrieval_queries=[query], vault_ids=vault_ids
        )
        selected = tuple(request.vault_ids or ctx["configuration"]["vault_ids"])
        if (
            not ctx["enabled"]
            or not set(selected) <= set(ctx["configuration"]["vault_ids"])
            or type(limit) is not int
            or not 1 <= limit <= 5
        ):
            raise ValueError("agent_scope_or_limit_invalid")
        result = client.search(query, selected, limit)
        if not isinstance(result, dict):
            raise ValueError("agent_response_invalid")
        return result

    @app.tool(annotations=annotations)
    def fiber_index_status(vault_id: str) -> dict[str, Any]:
        """Read index status; never build an index or call a model."""
        ctx = client.context()
        if not ctx["enabled"] or vault_id not in ctx["configuration"]["vault_ids"]:
            raise ValueError("agent_scope_or_limit_invalid")
        result = client.index(vault_id)
        if not isinstance(result, dict):
            raise ValueError("agent_response_invalid")
        return result

    @app.tool(annotations=annotations)
    def fiber_knowledge_catalog(
        vault_ids: list[str] | None = None, offset: int = 0, limit: int = 20
    ) -> dict[str, Any]:
        """List authorized published modules and notes with versions and truthful quality."""
        client.context()
        return dict(client.catalog(vault_ids, offset, limit))

    @app.tool(annotations=annotations)
    def fiber_moc_members(
        vault_id: str, moc_id: str | None = None, offset: int = 0
    ) -> dict[str, Any]:
        """List MOCs or authorized note/native-Skill members, then query an expert by ID/version."""
        client.context()
        return dict(client.mocs(vault_id, moc_id, offset))

    @app.tool(annotations=annotations)
    def fiber_query_expert(
        module_id: str,
        expected_version: str,
        question: str,
        limit: int = 5,
        output_capability: Literal["unknown", "text", "images", "full"] = "unknown",
        output_format: Literal["json", "markdown"] = "json",
    ) -> Any:
        """Query a published canonical expert Draft; original assurance and IDs remain intact."""
        client.context()
        from ..expert_mcp import result

        packet = dict(
            client.expert(
                module_id, expected_version, question, limit, output_capability, output_format
            )
        )
        return result(packet, output_capability)

    @app.tool(annotations=annotations)
    def fiber_read_evidence(
        module_id: str,
        expected_version: str,
        kind: str = "note",
        identity: str | None = None,
        start_line: int = 1,
        end_line: int = 80,
        time_start_ms: int | None = None,
        time_end_ms: int | None = None,
        physical_page: int | None = None,
        offset: int = 0,
        limit: int = 10,
        output_capability: Literal["unknown", "text", "images", "full"] = "unknown",
    ) -> Any:
        """Read bounded evidence by stable ID/version, with separate original-file authorization."""
        client.context()
        from .knowledge import Read

        body = Read.model_validate(
            {
                "module_id": module_id,
                "expected_version": expected_version,
                "kind": kind,
                "identity": identity,
                "start_line": start_line,
                "end_line": end_line,
                "time_start_ms": time_start_ms,
                "time_end_ms": time_end_ms,
                "physical_page": physical_page,
                "offset": offset,
                "limit": limit,
            }
        )
        if kind == "expert_media" and output_capability in {"unknown", "text"}:
            # Recheck current module/version authorization without fetching binary resources.
            client.evidence(
                {"module_id": module_id, "expected_version": expected_version, "kind": "module"}
            )
            return {
                "module_id": module_id,
                "version": expected_version,
                "resource_id": identity,
                "status": "unsupported",
                "reason": "output_capability_unknown"
                if output_capability == "unknown"
                else "output_text_only",
            }
        from ..expert_mcp import result

        return result(dict(client.evidence(body.model_dump())), output_capability)

    return app
