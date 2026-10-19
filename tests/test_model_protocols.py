"""Provider wire contracts, immutable default snapshots and actual loopback completions."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fiber_bundle import database, model_gateway, query_policy_store
from fiber_bundle.execution_config import parse_execution_config
from fiber_bundle.model_configuration import policy, protocols
from fiber_bundle.model_configuration import store as models
from fiber_bundle.model_configuration.contracts import Change, Save

pytest_plugins = ["test_model_configuration"]


def save(config, provider, model, effort="default", **kw):
    with database.connect(config.state_root) as db:
        item = models.save(
            config,
            db,
            Save(
                provider=provider,
                name="Synthetic",
                model=model,
                effort=effort,
                api_key="synthetic-provider-secret",
                **kw,
            ),
        )
    return policy.resolve(config, item["id"])


@pytest.mark.parametrize(
    "provider,model,protocol,field",
    [
        ("openai", "gpt-5", "openai-responses", "reasoning"),
        ("anthropic", "claude-opus-4-6", "anthropic-messages", "output_config"),
        ("deepseek", "deepseek-reasoner", "openai-completions", "reasoning_effort"),
    ],
)
def test_effort_reaches_final_payload(
    config, store, vault_keychain, provider, model, protocol, field
):
    selected = save(config, provider, model, "high")
    payload = model_gateway.prepare(
        selected, {"messages": [{"role": "user", "content": "Synthetic"}]}
    )
    assert selected.protocol == protocol
    assert payload["model"] == model
    assert (
        payload[field] == "high"
        if field == "reasoning_effort"
        else payload[field]["effort"] == "high"
    )
    assert (
        parse_execution_config(json.loads(json.dumps(selected.__dict__))).digest == selected.digest
    )


def test_default_switch_preserves_frozen_route_and_fixed_model(config, store, vault_keychain):
    a = save(config, "openai", "gpt-5")
    b = save(config, "anthropic", "claude-opus-4-6")
    with database.connect(config.state_root) as db:
        models.change(db, a.configuration_id, Change(action="default", expected_revision=1))
    first = query_policy_store.current(config)
    query_policy_store.freeze(config, first)
    with database.connect(config.state_root) as db:
        models.change(db, b.configuration_id, Change(action="default", expected_revision=1))
    assert query_policy_store.current(config).cloud_answer.model == "claude-opus-4-6"
    assert query_policy_store.version(config, first.digest).cloud_answer.model == "gpt-5"
    assert policy.resolve(config, a.configuration_id).digest == a.digest


@pytest.mark.parametrize(
    "protocol", ["openai-completions", "openai-responses", "anthropic-messages"]
)
def test_real_http_provider_reply_preserves_native_evidence(
    config, store, vault_keychain, protocol
):
    calls = []
    replies = {
        "openai-completions": {
            "model": "synthetic",
            "choices": [
                {"message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 5, "completion_tokens": 3},
        },
        "openai-responses": {
            "id": "resp-1",
            "model": "synthetic",
            "status": "completed",
            "output": [
                {
                    "type": "reasoning",
                    "id": "reason-1",
                    "encrypted_content": "synthetic-signature",
                    "summary": [],
                },
                {
                    "type": "message",
                    "id": "msg-1",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "ok"}],
                },
            ],
            "usage": {"input_tokens": 5, "output_tokens": 3},
        },
        "anthropic-messages": {
            "id": "msg-1",
            "model": "synthetic",
            "content": [
                {"type": "thinking", "thinking": "synthetic", "signature": "synthetic-signature"},
                {"type": "text", "text": "ok"},
            ],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 2, "cache_read_input_tokens": 3, "output_tokens": 3},
        },
    }

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(
                (self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
            )
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(replies[protocol]).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        selected = save(
            config,
            "local",
            "synthetic",
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            protocol=protocol,
        )
        body = protocols.prepare(selected, {"messages": [{"role": "user", "content": "synthetic"}]})
        reply = model_gateway.complete(selected, "synthetic-run", body)
        assert reply["choices"][0]["message"]["content"] == "ok"
        assert reply["usage"]["prompt_tokens"] == 5
        assert reply["usage"]["completion_tokens"] == 3
        if protocol != "openai-completions":
            assert "synthetic-signature" in json.dumps(reply["_native_response"])
        assert calls[0][1]["model"] == "synthetic"
        assert "synthetic-provider-secret" not in json.dumps(calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_native_tool_result_and_thinking_context_not_dropped(config, store, vault_keychain):
    selected = save(config, "anthropic", "claude-opus-4-6", "high")
    messages = [
        {
            "role": "assistant",
            "content": [
                {"type": "thinking", "thinking": "synthetic", "signature": "signature"},
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "read",
                    "input": {"path": "input.json"},
                },
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": "synthetic result"}
            ],
        },
    ]
    payload = protocols.prepare(
        selected,
        {
            "_fiber_native": "anthropic-messages",
            "messages": messages,
            "api_key": "must-not-forward",
            "endpoint": "must-not-forward",
        },
    )
    assert payload["messages"] == messages
    assert "api_key" not in payload and "endpoint" not in payload


def test_go_inference_preserves_stable_session_header(config, store, vault_keychain, monkeypatch):
    import urllib.request

    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            calls.append(self.headers.get("x-opencode-session"))
            body = self.rfile.read(int(self.headers["Content-Length"]))
            assert b"synthetic-provider-secret" not in body
            self.send_response(200 if calls[-1] else 400)
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "choices": [{"message": {"role": "assistant", "content": "OK"}}],
                        "usage": {"prompt_tokens": 2, "completion_tokens": 1},
                    }
                ).encode()
            )

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    original = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    class LocalTransport:
        def open(self, request, **kwargs):
            target = urllib.request.Request(
                f"http://127.0.0.1:{server.server_port}/chat/completions",
                data=request.data,
                headers=dict(request.header_items()),
            )
            return original.open(target, **kwargs)

    try:
        selected = save(config, "opencodego", "glm-5.3-flash")
        monkeypatch.setattr(protocols, "guard_address", lambda *a: None)
        monkeypatch.setattr(protocols.urllib.request, "build_opener", lambda *a: LocalTransport())
        body = protocols.prepare(selected, {"messages": [{"role": "user", "content": "Synthetic"}]})
        for session in ["stable-session", "stable-session", "other-session"]:
            assert model_gateway.complete(selected, session, body)["choices"]
        assert calls == ["stable-session", "stable-session", "other-session"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
