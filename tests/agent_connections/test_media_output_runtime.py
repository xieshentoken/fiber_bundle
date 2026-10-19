"""Actual native Chinese PDF package through HTTP/MCP and a guarded synthetic platform."""

import asyncio
import base64
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path
from uuid import uuid4

import pytest
from test_expert_authoring import explicit_progression_only as explicit_progression_only
from test_expert_media_query import media_package as media_package

from fiber_bundle import auth, database, evidence_answers, published_expert
from fiber_bundle.agent_connections import files, mcp_client
from fiber_bundle.agent_connections.contracts import ConnectionSpec
from fiber_bundle.channels import configuration, dispatch, store
from fiber_bundle.config import InvalidInput
from fiber_bundle.expert_presentation import bindings
from fiber_bundle.objects import digest

from .test_expert_operation_runtime import (
    operation_connection,
    submit_operation,
)
from .test_expert_operation_runtime import (
    operation_provider as operation_provider,
)

pytest_plugins = ["test_source_imports", "test_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("FIBER_PROJECT_SKILL_SOURCE"), reason="fixed Docker required"
)


def test_actual_package_http_mcp_export_mixed_and_late_revocation(
    config, media_package, operation_provider, monkeypatch, request
):
    network = request.getfixturevalue("network")
    cid, vid, _, module = media_package
    # Start HTTP workers only after the compiler fixture has finished its manual workflow.
    auth.set_password(config, "synthetic-password")
    login = network.post("/admin/login", json={"password": "synthetic-password"})
    assert login.status_code == 200, login.text
    network.headers["X-CSRF-Token"] = login.json()["csrf"]
    spec = ConnectionSpec(
        schema_version="fiber-agent-connection/v2",
        name="图文出口验证",
        agent_type="mcp",
        vault_ids=[vid],
        quality_scope="all",
        allow_originals=True,
    )
    response = network.post(
        "/admin/agent-connections",
        json={"request_id": str(uuid4()), "configuration": spec.model_dump()},
    )
    assert response.status_code == 201, response.text
    connection = response.json()
    prefix = "/admin/agent-connections/" + connection["id"]
    applied = network.post(
        prefix + "/operations",
        json={"request_id": str(uuid4()), "expected_revision": 1, "kind": "apply_config"},
    )
    assert applied.status_code == 202, applied.text
    enabled = network.post(
        prefix + "/enable", json={"request_id": str(uuid4()), "expected_revision": 1}
    )
    assert enabled.status_code == 200, enabled.text
    connection = enabled.json()
    transport = files.folder(config, connection["id"]) / "transport.json"
    reader = connection["client_id"]
    counts = []
    original = published_expert.read

    def count(root, name):
        if Path(name).suffix == ".png":
            counts.append(name)
        return original(root, name)

    monkeypatch.setattr(published_expert, "read", count)
    body = {
        "module_id": module["module_id"],
        "expected_version": module["version"],
        "question": "Figure 1",
        "limit": 5,
    }
    plain = network.post(prefix + "/knowledge-expert", json=body)
    assert plain.status_code == 200, plain.text
    assert plain.json()["expert_blocks"] and counts == []
    rich = network.post(prefix + "/knowledge-expert", json={**body, "output_capability": "full"})
    assert rich.status_code == 200, rich.text
    packet = rich.json()
    assert len(counts) == 1 and packet["expert_blocks"][0]["media"][0]["status"] == "available"
    counts.clear()
    exported = network.post(
        prefix + "/knowledge-expert",
        json={**body, "output_capability": "full", "output_format": "markdown"},
    )
    assert exported.status_code == 200, exported.text
    assert len(counts) == 1
    with zipfile.ZipFile(
        io.BytesIO(base64.b64decode(exported.json()["markdown_archive_base64"]))
    ) as archive:
        assert any(name.startswith("assets/") for name in archive.namelist())
        assert "知识未核实" in archive.read("answer.md").decode()

    async def mcp():
        async with mcp_client.session(transport) as session:
            tools = await session.list_tools()
            assert len(tools.tools) == 6
            counts.clear()
            text = await session.call_tool("fiber_query_expert", body)
            assert not text.isError and all(c.type == "text" for c in text.content)
            image = await session.call_tool(
                "fiber_query_expert", {**body, "output_capability": "full"}
            )
            assert not image.isError, image
            assert any(c.type == "image" for c in image.content)
            assert image.structuredContent["mcp_presentation"]["user_visible"] == "unconfirmed"
            assert len(counts) == 1

    asyncio.run(mcp())
    root = config.project_root / "test vault/temp_vault1"
    (root / "ordinary-output.md").write_text(
        "# Ordinary\nFigure 1 plain needleonly ![[secret.png]] ![x](private.png)\n"
    )
    counts.clear()
    with database.connect(config.state_root) as db:
        mixed = evidence_answers.answer(config, db, cid, "Figure 1", "Figure", [vid])
    assert len(mixed["sources"]) >= 2 and len(mixed["expert_blocks"]) == 1 and counts == []
    operation_prefix, operation_reader = operation_connection(network, vid)
    actual_operation = submit_operation(network, operation_prefix, operation_reader)
    assert actual_operation["answer"]["expert_blocks"]
    assert len(actual_operation["answer"]["sources"]) >= 2 and counts == []
    # Actual channel dispatch, with full capability explicitly configured only in this fixture.
    value = configuration.Settings(
        policy_version=2,
        name="隔离飞书输出",
        platform="feishu",
        app_id="cli_synthetic",
        client_id=cid,
        vault_id=vid,
        runtime_manifest="/synthetic/unused.json",
        expert_media="full",
        original=True,
    ).model_dump()
    with database.connect(config.state_root) as db:
        item = store.save(config, db, value, None, None)
    with store.opened(config) as side:
        side.execute(
            "UPDATE channels SET owner='ou_synthetic',state='running' WHERE id=?", (item["id"],)
        )
    dispatch.capture(config, item["id"], 1, "actual-mixed", "ou_synthetic", "查询本库：Figure 1")
    dispatch.advance(config, item["id"], 1)
    counts.clear()
    sent = []

    async def unknown(owner, body, context, guard):
        assert isinstance(body, dict) and "Ordinary" in json.dumps(body)
        guard()
        sent.append(body)
        return False

    assert asyncio.run(dispatch.deliver(config, item["id"], 1, unknown)) == "unknown"
    assert len(counts) == 1 and len(sent) == 1
    with pytest.raises(InvalidInput, match="reconciliation"):
        asyncio.run(dispatch.deliver(config, item["id"], 1, unknown))
    # Cached bytes cannot survive revocation or move/rebind without a fresh metadata check.
    with database.connect(config.state_root) as db:
        bound = bindings(config, db, reader, packet)
        assert bound
        db.execute("UPDATE client_vault_grants SET can_original=0 WHERE client_id=?", (reader,))
        with pytest.raises(auth.Denied):
            bindings(config, db, reader, packet)
    assert len(counts) == 1
    # Mutation occurs after hydration but before a platform side effect.
    for mutation in ("permission", "rebind", "version"):
        with database.connect(config.state_root) as db:
            auth.set_grant(db, cid, vid, query=True, original=True, add=False)
            late = store.save(config, db, value, None, None)
        with store.opened(config) as side:
            side.execute(
                "UPDATE channels SET owner='ou_synthetic',state='running' WHERE id=?", (late["id"],)
            )
        dispatch.capture(config, late["id"], 1, mutation, "ou_synthetic", "查询本库：Figure 1")
        dispatch.advance(config, late["id"], 1)
        attempts = []

        async def withdrawn(owner, body, context, guard, mutation=mutation, attempts=attempts):
            with database.connect(config.state_root) as db:
                if mutation == "permission":
                    db.execute(
                        "UPDATE client_vault_grants SET can_original=0 WHERE client_id=?", (cid,)
                    )
                elif mutation == "rebind":
                    db.execute(
                        "UPDATE publication_locations SET revision=revision+1 "
                        "WHERE kind='expert' AND publication_id=?",
                        (module["module_id"][7:],),
                    )
                else:
                    db.execute(
                        "UPDATE source_expert_publications SET artifact_sha256=? WHERE task_id=?",
                        ("f" * 64, module["module_id"][7:]),
                    )
            guard()
            attempts.append(True)
            return True

        assert asyncio.run(dispatch.deliver(config, late["id"], 1, withdrawn)) == "cancelled"
        assert attempts == []
    # Restore only this synthetic fixture's publication for the browser proof below.
    with database.connect(config.state_root) as db:
        db.execute(
            "UPDATE source_expert_publications SET artifact_sha256=? WHERE task_id=?",
            (module["version"], module["module_id"][7:]),
        )
    if output := os.getenv("FIBER_MEDIA_OUTPUT_EVIDENCE"):
        target = Path(output)
        target.mkdir(parents=True, exist_ok=True)
        from fiber_bundle import expert_presentation

        (target / "identity.json").write_text(
            json.dumps(
                {
                    "fixture_root": str(config.project_root),
                    "source_root": str(Path(__file__).resolve().parents[2]),
                    "connection_id": connection["id"],
                    "module_id": module["module_id"],
                    "version": module["version"],
                    "test_url": str(network.base_url),
                    "health": network.get("/health").json(),
                    "backend_source": str(Path(expert_presentation.__file__).resolve()),
                    "backend_sha256": digest(Path(expert_presentation.__file__).read_bytes()),
                    "web_asset_sha256": digest(network.get("/static/expert_results.js").content),
                    "boundary": "synthetic PDF/model; actual compiler/HTTP/MCP/UI; no live send",
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        (target / "query.json").write_text(json.dumps(packet, ensure_ascii=False, indent=2))
        (target / "mixed.json").write_text(
            json.dumps(sent[0]["response"], ensure_ascii=False, indent=2)
        )
        (target / "operation.json").write_text(
            json.dumps(actual_operation, ensure_ascii=False, indent=2)
        )
        if os.getenv("FIBER_MEDIA_OUTPUT_BROWSER"):
            with database.connect(config.state_root) as db:
                db.execute(
                    "UPDATE client_vault_grants SET can_original=1 WHERE client_id=?", (reader,)
                )
            env = {
                **os.environ,
                "FIBER_GUI_ACCEPTANCE_URL": str(network.base_url),
                "FIBER_GUI_EVIDENCE": str(target / "browser"),
                "FIBER_MEDIA_PACKET_PATH": str(target / "query.json"),
                "FIBER_MEDIA_MIXED_PATH": str(target / "mixed.json"),
                "FIBER_MEDIA_CONNECTION_ID": connection["id"],
                "FIBER_MEDIA_OPERATION_CONNECTION_ID": operation_reader["id"],
            }
            subprocess.run(
                ["node", "tests/e2e/expert_outputs.cjs"], env=env, check=True, timeout=90
            )
            assert len(operation_provider) == 3  # API proof + mixed UI + ordinary UI, no retries.
