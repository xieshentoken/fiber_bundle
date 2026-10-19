"""Read-only discovery against real loopback HTTP, including failures and redirects."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fiber_bundle.model_configuration import discovery
from fiber_bundle.model_configuration.contracts import Connection
from fiber_bundle.model_configuration.errors import ModelError


@pytest.fixture
def model_server():
    state = {
        "status": 200,
        "calls": [],
        "reply": {"data": [{"id": "local/model:1"}, {"id": "text-embedding-1"}]},
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["calls"].append((self.path, self.headers.get("Authorization")))
            self.send_response(state["status"])
            if state["status"] == 302:
                self.send_header("Location", state["location"])
            self.end_headers()
            self.wfile.write(json.dumps(state["reply"]).encode())

        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            state["calls"].append((self.path, self.headers.get("Authorization")))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "model": "local/model:1",
                        "choices": [
                            {
                                "message": {"role": "assistant", "content": "OK"},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {"prompt_tokens": 3, "completion_tokens": 1},
                    }
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    state["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    yield state
    server.shutdown()
    server.server_close()
    thread.join()


def test_read_only_discovery_filters_and_does_not_claim_inference(config, store, model_server):
    result = discovery.discover(
        config, Connection(provider="local", name="Local", base_url=model_server["base_url"])
    )
    assert [m["id"] for m in result["models"]] == ["local/model:1"]
    assert result["inference_verified"] is False and result["provider_balance"] is None
    assert model_server["calls"] == [("/v1/models", None)]


@pytest.mark.parametrize(
    "status,code",
    [
        (401, "authentication_failed"),
        (403, "permission_denied"),
        (404, "discovery_unsupported"),
        (429, "rate_limited"),
    ],
)
def test_discovery_reports_real_reason(config, store, model_server, status, code):
    model_server["status"] = status
    with pytest.raises(ModelError, match=code):
        discovery.discover(
            config, Connection(provider="local", name="Local", base_url=model_server["base_url"])
        )


def test_redirect_never_forwards_credentials(config, store, model_server):
    model_server.update(status=302, location=model_server["base_url"] + "/other")
    with pytest.raises(ModelError):
        discovery.discover(
            config,
            Connection(
                provider="local",
                name="Local",
                base_url=model_server["base_url"],
                api_key="synthetic-redirect-key",
            ),
        )
    assert len(model_server["calls"]) == 1


def test_repeated_pagination_cursor_is_bounded(config, store, model_server):
    model_server["reply"] = {"data": [{"id": "local-1"}], "has_more": True, "last_id": "same"}
    with pytest.raises(ModelError, match="response_invalid"):
        discovery.discover(
            config, Connection(provider="local", name="Local", base_url=model_server["base_url"])
        )
    assert len(model_server["calls"]) == 2


def test_api_requires_session_and_validation_never_echoes_key(http):
    client, headers = http, dict(http.headers)
    response = client.post(
        "/admin/models/discover",
        json={"provider": "openai", "name": "Key", "api_key": "synthetic bad secret"},
        headers=headers,
    )
    assert response.status_code == 422
    assert response.json()["field"] == "api_key"
    assert "synthetic" not in response.text
    response = client.post(
        "/admin/models",
        json={"provider": "openai", "name": "Key", "model": "gpt-5"},
        headers=headers,
    )
    assert response.json()["error"] == "model_key_required"


def test_explicit_api_test_uses_budget_and_unknown_price(http, model_server, monkeypatch):
    import time

    from fiber_bundle.model_configuration import keychain

    keys = {}
    monkeypatch.setattr(keychain, "put", lambda account, value: keys.__setitem__(account, value))
    monkeypatch.setattr(keychain, "get", lambda account: keys[account])
    saved = http.post(
        "/admin/models",
        json={
            "provider": "local",
            "name": "Local",
            "base_url": model_server["base_url"],
            "model": "local/model:1",
        },
    ).json()
    rejected = http.post(
        f"/admin/models/{saved['id']}/test", json={"expected_revision": 1, "approve_tokens": False}
    )
    assert rejected.status_code == 422 and not model_server["calls"]
    launched = http.post(
        f"/admin/models/{saved['id']}/test", json={"expected_revision": 1, "approve_tokens": True}
    )
    assert launched.status_code == 200, launched.text
    identity = launched.json()["id"]
    for _ in range(100):
        result = http.get(f"/admin/models/tests/{identity}").json()
        if result["state"] not in {"queued", "running"}:
            break
        time.sleep(0.02)
    assert result["state"] == "succeeded", result
    report = http.get("/admin/system/usage").json()
    assert report["summary"]["input_tokens"] == 3
    assert report["summary"]["unsettled_calls"] == 1
    catalog = http.get("/admin/models").json()
    assert catalog["models"][0]["verification"]["state"] == "passed"
