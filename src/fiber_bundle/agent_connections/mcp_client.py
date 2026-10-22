"""One owned stdio session; deadlines/termination are additionally enforced by its host process."""

import contextlib
import importlib.metadata
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

from .http_client import Client, ClientError


@contextlib.asynccontextmanager
async def session(connection_file: Path) -> AsyncIterator[Any]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from ..runtime_environment import child_environment

    if importlib.metadata.version("mcp") != "1.26.0":
        raise ClientError("agent_mcp_incompatible")
    params = StdioServerParameters(
        command=sys.executable,
        args=[
            "-m",
            "fiber_bundle.agent_connections.cli",
            "mcp",
            "--connection-file",
            str(connection_file),
        ],
        env=child_environment(),
    )
    with open("/dev/null", "w") as errors:
        async with stdio_client(params, errlog=errors) as (reader, writer):
            async with ClientSession(reader, writer) as client:
                await client.initialize()
                listing = await client.list_tools()
                names = {t.name for t in listing.tools}
                required = {
                    "fiber_list_vaults",
                    "fiber_search",
                    "fiber_index_status",
                }
                optional = {
                    "fiber_supplier_query",
                    "fiber_compare_quotes",
                    "fiber_supplier_evidence",
                    "fiber_knowledge_catalog",
                    "fiber_query_expert",
                    "fiber_read_evidence",
                    "fiber_moc_members",
                }
                if not required <= names or not names <= required | optional:
                    raise ClientError("agent_mcp_tools_invalid")
                yield client


async def call(client: Any, name: str, arguments: dict[str, Any]) -> Any:
    result = await client.call_tool(name, arguments)
    if result.isError:
        raise ClientError("agent_mcp_call_failed")
    if result.structuredContent is not None:
        value = result.structuredContent
        return value.get("result", value)
    texts = [item.text for item in result.content if item.type == "text"]
    return json.loads(texts[0]) if len(texts) == 1 else [json.loads(t) for t in texts]


async def probe(connection_file: Path) -> dict[str, Any]:
    http = Client(connection_file)
    ctx = http.context()
    if not ctx["enabled"]:
        raise ClientError("agent_paused")
    async with session(connection_file) as client:
        discovery = await client.list_tools()
        vaults = await call(client, "fiber_list_vaults", {})
        if {v["id"] for v in vaults} != set(ctx["configuration"]["vault_ids"]):
            raise ClientError("agent_scope_changed")
        indices = []
        for vid in ctx["configuration"]["vault_ids"]:
            indices.append(await call(client, "fiber_index_status", {"vault_id": vid}))
        if http.context() != ctx:
            raise ClientError("agent_context_changed")
        return {
            "state": "passed",
            "context": ctx,
            "tools": sorted(t.name for t in discovery.tools),
            "indices": indices,
            "mcp_version": importlib.metadata.version("mcp"),
            "model_called": False,
            "capability": "tools_available",
        }
