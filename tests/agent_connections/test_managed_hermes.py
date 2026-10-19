"""Actual installed Hermes through production HTTP/MCP/worker and a synthetic provider."""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import uuid4

import pytest

from fiber_bundle import query_policy_store
from fiber_bundle.agent_connections.contracts import ConnectionSpec

pytestmark = pytest.mark.skipif(
    not os.environ.get("FIBER_HERMES_CHECKOUT"), reason="explicit installed Hermes test"
)


@pytest.fixture
def provider(config):
    calls = []
    entered, release = threading.Event(), threading.Event()
    flags = {"delay": False, "failure": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            self.send_response(404)
            self.end_headers()

        def do_POST(self):
            data = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            if self.path == "/api/show":
                self.send_response(404)
                self.end_headers()
                return
            assert self.path == "/v1/chat/completions" and not data.get("stream")
            assert data["model"] == "synthetic-model" and not data.get("tools")
            calls.append(data)
            entered.set()
            if flags["delay"]:
                release.wait(15)
            if flags["failure"]:
                self.send_response(500)
                self.end_headers()
                return
            answer = {
                "schema_version": "fiber-agent-answer/v1",
                "claims": [{"text": "Synthetic cooler target: 37 C", "citations": ["[1]"]}],
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
            try:
                self.wfile.write(json.dumps(result).encode())
            except BrokenPipeError:
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    value = {
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
    }
    query_policy_store.save(config, value, None)
    note = config.project_root / "test vault/temp_vault1/cooler.md"
    note.write_text("# Synthetic cooler\nSynthetic cooler target: 37 C\n")
    yield calls, flags, entered, release, note
    release.set()
    server.shutdown()
    server.server_close()
    thread.join(2)


def ready(network, timeout=30):
    vid = network.get("/admin/vaults").json()[0]["id"]
    spec = ConnectionSpec(
        name="Synthetic Hermes",
        agent_type="hermes",
        vault_ids=[vid],
        model_configuration_id="local-primary",
        limits={"timeout_seconds": timeout},
    )
    r = network.post(
        "/admin/agent-connections",
        json={"request_id": str(uuid4()), "configuration": spec.model_dump()},
    ).json()
    base = f"/admin/agent-connections/{r['id']}"
    applied = network.post(
        base + "/operations",
        json={"kind": "apply_config", "request_id": str(uuid4()), "expected_revision": 1},
    )
    assert applied.json()["state"] == "succeeded", applied.text
    r = network.post(
        base + "/enable", json={"request_id": str(uuid4()), "expected_revision": 1}
    ).json()
    return base, r


def start(network, base, row):
    response = network.post(
        base + "/operations",
        json={
            "kind": "test_query",
            "request_id": str(uuid4()),
            "expected_revision": row["revision"],
            "request": {
                "request_id": str(uuid4()),
                "question": "What is the synthetic cooler target?",
                "retrieval_queries": ["cooler"],
            },
        },
    )
    assert response.status_code == 202, response.text
    return response.json()


def finish(network, base, operation):
    deadline = time.monotonic() + 35
    while operation["state"] in ("queued", "running") and time.monotonic() < deadline:
        time.sleep(0.05)
        operation = network.get(base + "/operations/" + operation["id"]).json()
    return operation


def test_production_hermes_single_call_and_changed_source_hides_previous_result(network, provider):
    calls, _, _, _, note = provider
    base, row = ready(network)
    op = finish(network, base, start(network, base, row))
    assert op["state"] == "succeeded", op
    assert op["answer"]["claims"][0]["text"] == "Synthetic cooler target: 37 C"
    assert len(calls) == 1 and "37 C" in json.dumps(calls[0]["messages"])
    assert [e["event"] for e in op["result"]["trace"]] == [
        "mcp_retrieval_completed",
        "http_source_verified",
        "hermes_initialized",
        "provider_request_admitted",
        "provider_response_received",
        "http_source_verified",
        "final_delivery_verified",
    ]
    assert op["result"]["provider_calls"] == 1 and op["result"]["usage_state"] == "reported"
    note.write_text("# Changed\nSynthetic cooler target: 41 C\n")
    stale = network.get(base + "/operations/" + op["id"]).json()
    assert "answer" not in stale and stale["result"]["state"] == "outdated"
    assert network.get(base).json()["test"]["state"] == "outdated"


@pytest.mark.parametrize("change", ["pause", "cancel", "source"])
def test_generation_in_flight_cannot_deliver_after_pause_cancel_or_source_change(
    network, provider, change
):
    calls, flags, entered, release, note = provider
    flags["delay"] = True
    base, row = ready(network)
    op = start(network, base, row)
    assert entered.wait(10)
    if change == "pause":
        network.post(
            base + "/pause", json={"expected_revision": row["revision"], "request_id": str(uuid4())}
        ).raise_for_status()
    elif change == "cancel":
        network.post(
            base + "/operations/" + op["id"] + "/cancel",
            json={"expected_revision": row["revision"], "request_id": str(uuid4())},
        ).raise_for_status()
    else:
        note.write_text("# Changed\nSynthetic cooler target: 41 C\n")
        release.set()
    op = finish(network, base, op)
    assert op["state"] in {"cancelled", "failed"} and "answer" not in op, op
    assert len(calls) == 1


def test_failed_provider_is_one_call_and_no_retry(network, provider):
    calls, flags, _, _, _ = provider
    flags["failure"] = True
    base, row = ready(network)
    op = finish(network, base, start(network, base, row))
    assert op["state"] == "failed" and "answer" not in op
    assert len(calls) == 1


def test_model_daily_budget_is_shared_between_connections_and_failed_attempts(
    network, config, provider
):
    from dataclasses import asdict

    route = query_policy_store.current(config)
    value = {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(route)}
    value["approved_backups"] = []
    value["local_query"]["daily_calls"] = 1
    query_policy_store.save(config, value, route.digest)
    provider[1]["failure"] = True
    base, row = ready(network)
    other_base, other = ready(network)
    request = {
        "kind": "test_query",
        "request_id": "same-turn",
        "expected_revision": row["revision"],
        "request": {
            "request_id": "same-input",
            "question": "What is the cooler target?",
            "retrieval_queries": ["cooler"],
        },
    }
    first = network.post(base + "/operations", json=request).json()
    repeated = network.post(base + "/operations", json=request).json()
    assert first["id"] == repeated["id"]
    assert finish(network, base, first)["state"] == "failed"
    rejected = network.post(
        other_base + "/operations", json={**request, "expected_revision": other["revision"]}
    )
    assert (
        rejected.status_code == 409 and rejected.json()["error"] == "agent_daily_budget_exhausted"
    )
    assert len(provider[0]) == 1


def test_packaged_host_dependencies_do_not_shadow_hermes_environment(
    network, config, provider, tmp_path
):
    """A bundle may carry incompatible binary dependencies beside the host helper."""
    import shutil
    import subprocess
    from pathlib import Path

    from fiber_bundle.agent_connections import files, hermes, installation, models

    _, row = ready(network)
    source = Path(hermes.__file__).parents[1]
    site = tmp_path.resolve() / "synthetic-site-packages"
    package = site / "fiber_bundle"
    entry = package / "agent_connections/hermes_entry.py"
    entry.parent.mkdir(parents=True)
    for name in (
        "__init__.py",
        "config.py",
        "query_routing_credentials.py",
        "query_routing_files.py",
    ):
        shutil.copyfile(source / name, package / name)
    shutil.copyfile(source / "agent_connections/hermes_entry.py", entry)
    incompatible = site / "pydantic"
    incompatible.mkdir()
    (incompatible / "__init__.py").write_text(
        'raise RuntimeError("synthetic incompatible host dependency")\n'
    )
    runtime = installation.current()
    home = files.folder(config, row["id"]) / "hermes-home"
    output = tmp_path.resolve() / "generation.json"
    payload = {
        "root": runtime["root"],
        "home": str(home),
        "output": str(output),
        "model": models.local(config, "local-primary"),
        "timeout_seconds": 30,
        "system": "Return the synthetic fixture JSON.",
        "user": "Synthetic cooler target?",
        "max_tokens": 1024,
    }
    process = subprocess.run(
        [runtime["python"], "-I", "-B", str(entry)],
        input=json.dumps(payload).encode(),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={"PATH": "/usr/bin:/bin", "LANG": "en_US.UTF-8", "HERMES_HOME": str(home)},
        cwd=home,
        timeout=35,
        check=False,
    )
    assert process.returncode == 0
    generated = json.loads(output.read_text())
    assert generated["provider_calls"] == 1
    assert "37 C" in generated["answer"]
    assert len(provider[0]) == 1
