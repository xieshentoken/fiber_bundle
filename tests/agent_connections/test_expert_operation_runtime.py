"""Actual operation HTTP/worker/MCP/Hermes path with a local synthetic model only."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from uuid import uuid4

import pytest
from test_expert_authoring import explicit_progression_only as explicit_progression_only
from test_expert_media_query import media_package as media_package

from fiber_bundle import database, published_expert, query_policy_store
from fiber_bundle.agent_connections import files
from fiber_bundle.agent_connections.contracts import ConnectionSpec

pytest_plugins = ["test_source_imports", "test_execution"]
pytestmark = pytest.mark.skipif(
    not os.getenv("FIBER_PROJECT_SKILL_SOURCE"), reason="fixed runtime required"
)


@pytest.fixture
def operation_provider(config):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path != "/v1/chat/completions":
                self.send_response(404)
                self.end_headers()
                return
            assert data["model"] == "synthetic-model" and not data.get("tools")
            calls.append(data)
            answer = {
                "schema_version": "fiber-agent-answer/v1",
                "claims": [{"text": "检索到了授权笔记中的相关文字。", "citations": ["[1]"]}],
                "model_supplement": None,
            }
            result = {
                "id": "synthetic",
                "object": "chat.completion",
                "created": 0,
                "model": "synthetic-model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": json.dumps(answer)},
                    }
                ],
                "usage": {"prompt_tokens": 1000, "completion_tokens": 100, "total_tokens": 1100},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    query_policy_store.save(
        config,
        {
            "schema": "query-routing-preparation/v1",
            "enabled": False,
            "local_query": {
                "image": "sha256:" + "a" * 64,
                "endpoint": f"http://127.0.0.1:{server.server_port}/v1/chat/completions",
                "model": "synthetic-model",
                "secret_file": None,
                "max_input_tokens": 8192,
                "max_output_tokens": 1024,
                "max_request_bytes": 65536,
                "max_calls": 1,
                "daily_calls": 10,
                "timeout_seconds": 30,
            },
            "cloud_answer": None,
            "approved_backups": [],
        },
        None,
    )
    try:
        yield calls
    finally:
        server.shutdown()
        server.server_close()
        thread.join(2)


def operation_connection(network, vid):
    spec = ConnectionSpec(
        schema_version="fiber-agent-connection/v2",
        name="实际图文操作",
        agent_type="hermes",
        vault_ids=[vid],
        quality_scope="all",
        allow_originals=True,
        model_configuration_id="local-primary",
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
    assert applied.status_code == 202 and applied.json()["state"] == "succeeded", applied.text
    enabled = network.post(
        prefix + "/enable", json={"request_id": str(uuid4()), "expected_revision": 1}
    )
    assert enabled.status_code == 200, enabled.text
    return prefix, enabled.json()


def submit_operation(network, prefix, connection, question="Figure 1", query="Figure"):
    response = network.post(
        prefix + "/operations",
        json={
            "request_id": str(uuid4()),
            "expected_revision": connection["revision"],
            "kind": "test_query",
            "request": {
                "request_id": str(uuid4()),
                "question": question,
                "retrieval_queries": [query],
            },
        },
    )
    assert response.status_code == 202, response.text
    identity = response.json()["id"]
    deadline = time.monotonic() + 35
    while time.monotonic() < deadline:
        result = network.get(prefix + "/operations/" + identity)
        assert result.status_code == 200, result.text
        value = result.json()
        if value["state"] not in {"queued", "running"}:
            assert value["state"] == "succeeded", value
            return value
        time.sleep(0.1)
    pytest.fail("actual operation did not settle")


def test_real_operation_preserves_registry_mixed_sources_and_legacy_history(
    config, media_package, operation_provider, monkeypatch, request
):
    network = request.getfixturevalue("network")
    from fiber_bundle import auth

    _, vid, _, module = media_package
    auth.set_password(config, "synthetic-password")
    login = network.post("/admin/login", json={"password": "synthetic-password"})
    network.headers["X-CSRF-Token"] = login.json()["csrf"]
    root = config.project_root / "test vault/temp_vault1"
    (root / "ordinary-operation.md").write_text(
        "# Ordinary Figure\nFigure text and needleonly ![private](missing.png).\n"
    )
    prefix, connection = operation_connection(network, vid)
    reads = []
    original = published_expert.read
    monkeypatch.setattr(
        published_expert, "read", lambda root, name: (reads.append(name), original(root, name))[1]
    )
    value = submit_operation(network, prefix, connection)
    assert len(value["answer"]["sources"]) >= 2
    assert value["answer"]["claims"][0]["citations"] == ["[1]"]
    assert value["answer"]["expert_blocks"][0]["module_id"] == module["module_id"]
    assert value["answer"]["expert_blocks"][0]["media"]
    assert not any(name.endswith(".png") for name in reads)
    if output := os.getenv("FIBER_MEDIA_OPERATION_EVIDENCE"):
        path = Path(output)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    reads.clear()
    ordinary = submit_operation(network, prefix, connection, "needleonly", "needleonly")
    assert "expert_blocks" not in ordinary["answer"] and reads == []
    assert len(operation_provider) == 2
    # Reopening older successful output stays text-only; it never reruns a model.
    path = files.folder(config, connection["id"]) / (value["id"] + ".answer.json")
    document = json.loads(path.read_bytes())
    document["answer"].pop("question")
    for source in document["answer"]["sources"]:
        for key in ("expert_module_id", "expert_module_version", "expert_projection"):
            source.pop(key, None)
    path.write_text(json.dumps(document))
    legacy = network.get(prefix + "/operations/" + value["id"]).json()
    assert legacy["result"]["state"] != "outdated" and "expert_blocks" not in legacy["answer"]
    assert len(operation_provider) == 2
    with database.connect(config.state_root) as db:
        auth.set_grant(db, connection["client_id"], vid, query=False, original=True, add=False)
    withdrawn = network.get(prefix + "/operations/" + value["id"]).json()
    assert "answer" not in withdrawn
