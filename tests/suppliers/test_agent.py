"""Bearer HTTP scope enforcement using real connection identity and retained supplier facts."""

import json

import pytest
from test_ingestion import offer, scoped

from fiber_bundle import database
from fiber_bundle.agent_connections import application, configuration
from fiber_bundle.agent_connections import store as connections
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.suppliers import ingestion
from fiber_bundle.suppliers.contracts import ManualQuote


def test_agent_queries_are_current_scoped_readonly_and_bounded(config, store, http):
    cid, vid = scoped(config)
    with database.locked(config), database.connect(config.state_root) as db:
        ingestion.manual(
            config,
            db,
            ManualQuote(client_id=cid, vault_id=vid, request_id="agent-source", offer=offer()),
        )
        item = configuration.create(
            config,
            db,
            ConnectionSpec(
                schema_version="fiber-agent-connection/v2",
                name="Synthetic supplier Agent",
                agent_type="mcp",
                vault_ids=[vid],
            ),
            "create-agent",
        )
        application.apply(config, db, connections.get(db, item["id"]), "http://127.0.0.1:8765")
        item = configuration.control(config, db, item["id"], "enable", 1, "enable-agent")
        # The public installation points at its private credential; do not put it in logs.
        installation = config.state_root / "agent-connections" / item["id"] / "connection.json"
        if not installation.exists():
            candidates = list(
                (config.state_root / "agent-connections" / item["id"]).rglob("*.json")
            )
            installation = next(
                p
                for p in candidates
                if json.loads(p.read_text()).get("schema") == "fiber-agent-transport/v1"
            )
        transport = json.loads(installation.read_text())
        token = json.loads(__import__("pathlib").Path(transport["credential_file"]).read_text())[
            "token"
        ]
        another = db.execute("SELECT id FROM vaults WHERE name='personal'").fetchone()[0]
    headers = {"Authorization": "Bearer " + token}
    request = {"vault_id": vid, "limit": 1}
    result = http.post("/v1/agent-knowledge/suppliers/query", json=request, headers=headers)
    assert result.status_code == 200, result.text
    packet = result.json()
    assert packet["total"] == 1 and packet["model_called"] is False
    source = packet["items"][0]["evidence"]
    evidence = http.post(
        "/v1/agent-knowledge/suppliers/evidence",
        headers=headers,
        json={
            "vault_id": vid,
            "source_id": source["source_id"],
            "expected_sha256": source["sha256"],
            "length": 20,
        },
    )
    assert evidence.status_code == 200 and len(evidence.json()["text"]) == 20
    assert (
        http.post(
            "/v1/agent-knowledge/suppliers/query", json={"vault_id": another}, headers=headers
        ).status_code
        == 404
    )
    with database.connect(config.state_root) as db:
        configuration.control(config, db, item["id"], "pause", item["revision"], "pause-agent")
    assert (
        http.post("/v1/agent-knowledge/suppliers/query", json=request, headers=headers).status_code
        == 403
    )


def test_mcp_exports_readonly_supplier_contracts(tmp_path):
    import asyncio
    import importlib.metadata

    from fiber_bundle.agent_connections.mcp_server import server

    if importlib.metadata.version("mcp") != "1.26.0":
        pytest.skip("Pinned existing MCP runtime required")
    path = tmp_path / "connection.json"
    path.write_text(
        json.dumps(
            {
                "schema": "fiber-agent-transport/v1",
                "endpoint": "http://127.0.0.1:18849",
                "credential_file": str(tmp_path / "credential.json"),
                "connection_id": "synthetic",
            }
        )
    )
    path.chmod(0o600)
    app = server(path)
    tools = asyncio.run(app.list_tools())
    names = {"fiber_supplier_query", "fiber_compare_quotes", "fiber_supplier_evidence"}
    selected = [t for t in tools if t.name in names]
    assert {t.name for t in selected} == names
    assert all(t.annotations.readOnlyHint and not t.annotations.destructiveHint for t in selected)
    query = next(t for t in selected if t.name == "fiber_supplier_query")
    assert "conditions" in query.inputSchema["properties"]
