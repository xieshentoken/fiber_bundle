"""Actual HTTP and official MCP stdio transport, with synthetic notes and no inference."""

import asyncio
import json
import time
from uuid import uuid4

import pytest

from fiber_bundle.agent_connections import files, mcp_client
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.agent_connections.evidence import merge, project


def ready(network, config, name="personal"):
    vid = next(v["id"] for v in network.get("/admin/vaults").json() if v["name"] == name)
    value = ConnectionSpec(name="Synthetic MCP", agent_type="mcp", vault_ids=[vid])
    response = network.post(
        "/admin/agent-connections",
        json={"request_id": str(uuid4()), "configuration": value.model_dump()},
    )
    assert response.status_code == 201, response.text
    row = response.json()
    applied = network.post(
        f"/admin/agent-connections/{row['id']}/operations",
        json={"request_id": str(uuid4()), "expected_revision": 1, "kind": "apply_config"},
    )
    assert applied.status_code == 202 and applied.json()["state"] == "succeeded", applied.text
    enabled = network.post(
        f"/admin/agent-connections/{row['id']}/enable",
        json={"request_id": str(uuid4()), "expected_revision": 1},
    )
    assert enabled.status_code == 200, enabled.text
    return enabled.json(), files.folder(config, row["id"]) / "transport.json"


def test_actual_stdio_readonly_scope_source_verification_and_rotation(network, config):
    row, transport = ready(network, config)
    note = config.project_root / "test vault/temp_vault1/synthetic.md"
    note.write_text("# Synthetic cooler\nSynthetic cooler target: 37 C\n")
    (config.project_root / "test vault/temp_vault2/private.md").write_text(
        "# Private\nSynthetic cooler target: 999 C\n"
    )
    from fiber_bundle.agent_connections.http_client import Client

    client = Client(transport)

    async def exercise():
        async with mcp_client.session(transport) as session:
            listing = await session.list_tools()
            assert {t.name for t in listing.tools} == {
                "fiber_list_vaults",
                "fiber_search",
                "fiber_index_status",
                "fiber_knowledge_catalog",
                "fiber_moc_members",
                "fiber_query_expert",
                "fiber_read_evidence",
            } and all(t.annotations.readOnlyHint for t in listing.tools)
            vaults = await mcp_client.call(session, "fiber_list_vaults", {})
            assert [v["id"] for v in vaults] == row["configuration"]["vault_ids"]
            value = await mcp_client.call(session, "fiber_search", {"query": "cooler"})
            assert "37 C" in json.dumps(value) and "999 C" not in json.dumps(value)
            moc_catalog = await mcp_client.call(
                session, "fiber_moc_members", {"vault_id": row["configuration"]["vault_ids"][0]}
            )
            assert moc_catalog["mocs"] == [] and moc_catalog["model_called"] is False
            catalog = await mcp_client.call(session, "fiber_knowledge_catalog", {})
            module = next(i for i in catalog["items"] if i["title"] == "Synthetic cooler")
            evidence = await mcp_client.call(
                session,
                "fiber_read_evidence",
                {
                    "module_id": module["module_id"],
                    "expected_version": module["version"],
                },
            )
            assert "37 C" in evidence["text"] and evidence["completeness_status"] == "unknown"
            assert evidence["model_called"] is False
            assert (
                await session.call_tool(
                    "fiber_read_evidence",
                    {
                        "module_id": module["module_id"],
                        "expected_version": "f" * 64,
                    },
                )
            ).isError
            none = await mcp_client.call(session, "fiber_search", {"query": "NONEXISTENT-9937"})
            assert none["status"] == "no_evidence"
            denied = await session.call_tool(
                "fiber_search", {"query": "cooler", "vault_ids": ["private"]}
            )
            assert denied.isError
            unavailable = await session.call_tool("fiber_write", {})
            assert unavailable.isError
            return value

    value = asyncio.run(exercise())
    sources = merge([project(value, tuple(row["configuration"]["vault_ids"]), 5)], 8000)
    ctx = client.context()
    assert client.request("/v1/agent-evidence/verify", {"context": ctx, "sources": sources}) == {
        "verified": True
    }
    note.write_text("# Changed\nSynthetic cooler target: 41 C\n")
    from fiber_bundle.agent_connections.http_client import ClientError

    with pytest.raises(ClientError):
        client.request("/v1/agent-evidence/verify", {"context": ctx, "sources": sources})
    export = network.get(f"/admin/agent-connections/{row['id']}/configuration-export").json()
    token = files.credential(config, row["id"])
    assert token not in json.dumps(export)
    rotated = network.post(
        f"/admin/agent-connections/{row['id']}/operations",
        json={
            "kind": "rotate_credential",
            "expected_revision": row["revision"],
            "request_id": "rotate-one",
        },
    )
    assert rotated.status_code == 202 and rotated.json()["state"] == "succeeded"
    assert (
        network.post(
            f"/admin/agent-connections/{row['id']}/operations",
            json={
                "kind": "rotate_credential",
                "expected_revision": row["revision"],
                "request_id": "rotate-one",
            },
        ).json()["id"]
        == rotated.json()["id"]
    )
    assert (
        network.get("/v1/vaults", headers={"Authorization": "Bearer " + token}).status_code == 401
    )
    assert client.context()["enabled"] is False


def test_actual_finite_probe_and_pause_blocks_mcp(network, config):
    row, transport = ready(network, config)
    op = network.post(
        f"/admin/agent-connections/{row['id']}/operations",
        json={"kind": "probe", "expected_revision": row["revision"], "request_id": "probe-one"},
    ).json()
    deadline = time.monotonic() + 12
    while op["state"] in {"queued", "running"} and time.monotonic() < deadline:
        time.sleep(0.05)
        op = network.get(f"/admin/agent-connections/{row['id']}/operations/{op['id']}").json()
    assert op["state"] == "succeeded", op
    assert op["result"]["model_called"] is False
    network.post(
        f"/admin/agent-connections/{row['id']}/pause",
        json={"expected_revision": row["revision"], "request_id": "pause-one"},
    ).raise_for_status()
    outdated = network.get(f"/admin/agent-connections/{row['id']}/operations/{op['id']}").json()
    assert outdated["result"] == {"state": "outdated"}

    async def paused():
        async with mcp_client.session(transport) as session:
            assert (await session.call_tool("fiber_search", {"query": "cooler"})).isError
            assert await mcp_client.call(session, "fiber_list_vaults", {}) == []

    asyncio.run(paused())
