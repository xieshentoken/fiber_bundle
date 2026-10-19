"""Opt-in subprocess probe of the installed Hermes API against a synthetic local provider.

No real model or personal Hermes profile is used. This is A0 compatibility evidence,
not the production A4 launcher or a substitute for its durable budgets and cancellation.
"""

import importlib.metadata
import json
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.dont_write_bytecode = True
checkout, project, isolated = (Path(value).resolve() for value in sys.argv[1:4])
sys.path[:0] = [str(project / "src"), str(project / "tests"), str(checkout)]

from agent_connections.support import Harness, proposal  # noqa: E402
from fiber_bundle.agent_connections.hermes_compatibility import inspect  # noqa: E402
from fiber_bundle.agent_connections.turn import TurnBlocked  # noqa: E402

events = []
requests = []
request_paths = []
metadata_paths = []
denied = []
provider_failure = False


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        # Hermes probes local model-server metadata; no inference or evidence is involved.
        metadata_paths.append(self.path)
        events.append("metadata")
        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/show":
            assert payload in ({"name": "synthetic-local"}, {"model": "synthetic-local"})
            metadata_paths.append(self.path)
            events.append("metadata")
            self.send_response(404)
            self.end_headers()
            return
        events.append("provider")
        requests.append(payload)
        request_paths.append(self.path)
        assert self.path == "/v1/chat/completions", self.path
        assert payload["model"] == "synthetic-local"
        assert not payload.get("tools")
        assert payload.get("max_tokens") == 1024
        assert "Synthetic coolant target: 37 C" in json.dumps(payload["messages"])
        if provider_failure:
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"synthetic failure","type":"server_error"}}')
            return
        self.send_response(200)
        if payload.get("stream"):
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for delta, finish in [
                ({"role": "assistant", "content": proposal()}, None),
                ({}, "stop"),
            ]:
                chunk = {
                    "id": "synthetic",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "synthetic-local",
                    "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
                }
                self.wfile.write(("data: " + json.dumps(chunk) + "\n\n").encode())
            self.wfile.write(b"data: [DONE]\n\n")
        else:
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(
                json.dumps(
                    {
                        "id": "synthetic",
                        "object": "chat.completion",
                        "created": 1,
                        "model": "synthetic-local",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": proposal(),
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 200,
                            "completion_tokens": 30,
                            "total_tokens": 230,
                        },
                    }
                ).encode()
            )


server = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
port = server.server_address[1]
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
private_home = checkout.parent


def audit(event, args):
    # Defence for this test process, not a claimed Python security sandbox.
    if event == "socket.connect" and args[0].family in {socket.AF_INET, socket.AF_INET6}:
        if args[1] != ("127.0.0.1", port):
            denied.append("network")
            raise PermissionError("probe_loopback_only")
    if event == "socket.getaddrinfo" and args[0] not in {"127.0.0.1", "localhost"}:
        denied.append("dns")
        raise PermissionError("probe_loopback_only")
    if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
        denied.append("subprocess")
        raise PermissionError("probe_no_child_processes")
    if event == "open" and isinstance(args[0], (str, bytes)):
        path = Path(os.fsdecode(args[0])).resolve()
        writing = args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
        secret = (
            path.is_relative_to(private_home) and not path.is_relative_to(checkout)
        ) or path.name in {".env", ".op.env", "auth.json", "credentials.json"}
        if (writing or secret) and not path.is_relative_to(isolated) and path != Path("/dev/null"):
            denied.append("write" if writing else "private_read")
            raise PermissionError("probe_profile_isolation")


sys.addaudithook(audit)
compatibility = inspect(checkout)
assert compatibility["state"] == "candidate"


def generate(prepared):
    # Import and construction happen only after the host has retrieved and verified evidence.
    events.append("construct")
    from run_agent import AIAgent

    agent = AIAgent(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key="synthetic-not-a-real-key",
        model="synthetic-local",
        provider="openai",
        api_mode="chat_completions",
        max_iterations=1,
        max_tokens=1024,
        enabled_toolsets=[],
        disabled_toolsets=[],
        skip_memory=True,
        skip_context_files=True,
        load_soul_identity=False,
        session_db=None,
        fallback_model=None,
        save_trajectories=False,
        quiet_mode=True,
        verbose_logging=False,
        checkpoints_enabled=False,
    )
    try:
        assert agent.tools == []
        result = agent.run_conversation(
            prepared.user_message,
            system_message=prepared.system_message,
            conversation_history=[],
        )
        if result.get("failed") or not result.get("completed"):
            raise RuntimeError("probe_agent_failed")
        assert result["api_calls"] == 1
        return result["final_response"]
    finally:
        agent.close()


checks = {}
try:
    for scenario, expected in [
        ("retrieval_failure", "agent_retrieval_failed"),
        ("cancel", "agent_cancelled"),
        ("revoke", "agent_context_changed"),
    ]:
        h = Harness()

        def retrieve(*args, h=h, scenario=scenario):
            result = h.retrieve(*args)
            if scenario == "retrieval_failure":
                raise RuntimeError("synthetic retrieval error")
            if scenario == "cancel":
                h.stopped = True
            if scenario == "revoke":
                h.context["authorization_sha256"] = "b" * 64
            return result

        try:
            h.run(retrieve=retrieve, generate=generate)
            raise AssertionError("gate should have blocked")
        except TurnBlocked as exc:
            assert exc.code == expected
        assert events == [] and requests == []
        checks[scenario] = {"constructor_calls": 0, "provider_calls": 0, "delivered": False}

    h = Harness()

    def retrieve(*args):
        events.append("retrieve")
        return h.retrieve(*args)

    def verify(prepared):
        events.append("verify")
        h.verify(prepared)

    result = h.run(retrieve=retrieve, verify_evidence=verify, generate=generate)
    assert [e for e in events if e != "metadata"] == [
        "retrieve",
        "verify",
        "construct",
        "provider",
        "verify",
    ], (events, request_paths)
    assert all(
        events.index("construct") < i < events.index("provider")
        for i, event in enumerate(events)
        if event == "metadata"
    )
    assert result["claims"][0]["citations"] == ["[1]"]
    checks["success"] = {"trace": list(events), "provider_calls": len(requests), "draft": True}

    provider_failure = True
    previous = len(requests)
    try:
        Harness().run(generate=generate)
        raise AssertionError("provider failure must stop")
    except TurnBlocked as exc:
        assert exc.code == "agent_generation_failed"
    assert len(requests) - previous == 1
    checks["provider_failure"] = {"provider_calls": 1, "delivered": False, "retried": False}
    report = {
        "state": "passed",
        "scope": "A0_actual_Hermes_synthetic_provider",
        "compatibility": compatibility,
        "dependencies": {
            name: importlib.metadata.version(name) for name in ("mcp", "openai", "httpx")
        },
        "checks": checks,
        "metadata_paths": metadata_paths,
        "guard_denials": denied,
        "real_model_called": False,
        "production_ready": False,
    }
    assert not {"network", "dns", "write", "private_read"}.intersection(denied)
    (isolated / "probe-result.json").write_text(json.dumps(report, indent=2))
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)
