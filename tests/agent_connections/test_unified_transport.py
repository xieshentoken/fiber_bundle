"""Actual read-only stdio MCP retrieves automatic notes after a complete scoped merge."""

import asyncio

import pytest
from test_automatic_ingestion import admitted
from test_session_search import owner as session_owner  # noqa: F401
from test_source_imports import owner as owner
from test_vault_merge_content import merge

from fiber_bundle import automatic_ingestion
from fiber_bundle.agent_connections import mcp_client

from .test_transport import ready


@pytest.fixture(autouse=True)
def explicit_ticks(monkeypatch):
    monkeypatch.setattr(automatic_ingestion.Runtime, "start", lambda self: None)


def test_actual_mcp_target_scope_preserves_system_assurance(
    config, store, owner, network, tmp_path
):
    cid, source, _, _, _ = admitted(config, owner)
    for _ in range(3):
        assert automatic_ingestion.tick(config)
    target, _, _, _ = merge(config, cid, source, tmp_path)
    connection, transport = ready(network, config, "supplier")
    assert connection["configuration"]["vault_ids"] == [target]

    async def exercise():
        async with mcp_client.session(transport) as session:
            vaults = await mcp_client.call(session, "fiber_list_vaults", {})
            assert [v["id"] for v in vaults] == [target]
            result = await mcp_client.call(
                session, "fiber_search", {"query": "verified-auto-token"}
            )
            assert result["results"]
            assert all(
                hit["vault_id"] == target and hit["review_actor"] == "system_rules"
                for hit in result["results"]
            )
            assert (
                await session.call_tool(
                    "fiber_search", {"query": "verified-auto-token", "vault_ids": [source]}
                )
            ).isError

    asyncio.run(exercise())
