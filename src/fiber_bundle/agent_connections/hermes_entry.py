"""Owned subprocess only: constrained model proxy and real Hermes public API.

Python audit hooks are defence in depth, not an OS sandbox claim. The parent owns
hard deadline/cancellation and the durable admission. Stdout/stderr are discarded.
"""

import importlib
import importlib.metadata
import json
import os
import secrets
import socket
import ssl
import sys
import threading
import traceback
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

diagnostic_output: Path | None = None
phase = "input"
diagnostics: dict[str, Any] = {}


def main() -> None:
    global diagnostic_output, phase
    raw = sys.stdin.buffer.read(131073)
    if len(raw) > 131072:
        raise ValueError("agent_input_limit")
    value = json.loads(raw)
    root = Path(value["root"]).resolve()
    home = Path(value["home"]).resolve()
    output = Path(value["output"]).resolve()
    diagnostic_output = output
    model = value["model"]
    target = urlsplit(model["endpoint"])
    cloud = model.get("processing_location") == "cloud"
    registered_policy = None
    registered_complete: Any = None
    if cloud:
        if (
            not model.get("configuration_id")
            and model["endpoint"] != "https://opencode.ai/zen/go/v1/chat/completions"
        ):
            raise ValueError("agent_model_endpoint_invalid")
    elif target.scheme != "http" or target.hostname not in {"127.0.0.1", "::1"}:
        raise ValueError("agent_model_endpoint_invalid")
    # Load only an explicitly configured credential, before importing third-party code.
    host_source = str(Path(__file__).resolve().parents[2])
    sys.path.insert(0, host_source)
    if model.get("configuration_id"):
        from fiber_bundle.model_configuration.policy import RegisteredPolicy
        from fiber_bundle.model_configuration.protocols import complete as registered_complete

        registered_policy = RegisteredPolicy(
            **{k: model[k] for k in RegisteredPolicy.__dataclass_fields__}
        )
        registered_policy.validate()
    key = None
    try:
        if cloud:
            from fiber_bundle.execution_config import read_secret

            key = read_secret(Path(model["secret_file"]))
        else:
            from fiber_bundle.query_routing_credentials import load

            if model["secret_file"]:
                key, _, error = load(Path(model["secret_file"]))
                if error:
                    raise ValueError("agent_model_credential_invalid")
    finally:
        # A packaged host contains CPython 3.12 dependencies; Hermes owns its 3.11
        # environment. Retain the already-loaded credential helper only.
        sys.path.remove(host_source)
    proxy_token = secrets.token_urlsafe(32)
    calls = 0
    metadata = 0
    usage = None
    trace = []
    proxy_lock = threading.Lock()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    handlers: list[Any] = [urllib.request.ProxyHandler({}), NoRedirect()]
    remote_addresses = {(target.hostname, target.port)}
    if cloud:
        import certifi

        handlers.append(
            urllib.request.HTTPSHandler(context=ssl.create_default_context(cafile=certifi.where()))
        )
        remote_addresses = {
            (item[4][0], item[4][1])
            for item in socket.getaddrinfo(target.hostname, 443, type=socket.SOCK_STREAM)
        }
    opener = urllib.request.build_opener(*handlers)

    class Proxy(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def do_GET(self) -> None:
            self.handle_request(False)

        def do_POST(self) -> None:
            self.handle_request(True)

        def handle_request(self, post: bool) -> None:
            nonlocal calls, metadata, usage
            inference = self.path == "/v1/chat/completions" and post
            if inference and self.headers.get("Authorization") != "Bearer " + proxy_token:
                self.send_error(401)
                return
            with proxy_lock:
                if inference:
                    if calls >= 1:
                        self.send_error(429, "agent_call_limit")
                        return
                    calls += 1
                    diagnostics["provider_calls"] = calls
                    trace.append({"event": "provider_request_admitted"})
                else:
                    metadata += 1
                    diagnostics["metadata_requests"] = metadata
                    if metadata > 32 or self.path not in {"/v1/models", "/models", "/api/show"}:
                        self.send_error(400, "agent_metadata_limit")
                        return
            body = None
            streaming = False
            if cloud and not inference:
                # Hermes metadata discovery stays local; no auxiliary cloud requests or bodies.
                wire = json.dumps(
                    {"object": "list", "data": [{"id": model["model"], "object": "model"}]}
                ).encode()
                self.send_response(200 if not post else 404)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)
                return
            if post:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= model["max_request_bytes"]:
                    self.send_error(413)
                    return
                body = self.rfile.read(size)
                if inference:
                    request = json.loads(body)
                    if request.get("model") != model["model"] or request.get("tools"):
                        self.send_error(400)
                        return
                    streaming = request.get("stream") is True
                    request["max_tokens"] = value["max_tokens"]
                    request["stream"] = False
                    if cloud:
                        request["response_format"] = {"type": "json_object"}
                    request.pop("stream_options", None)
                    messages = request.get("messages", [])
                    diagnostics["input_bytes"] = sum(
                        len(str(m.get("content", "")).encode()) for m in messages
                    )
                    if (
                        not isinstance(messages, list)
                        or sum(len(str(m.get("content", "")).encode()) for m in messages) + 256
                        > model["max_input_tokens"]
                    ):
                        diagnostics["provider_error"] = "agent_input_budget"
                        self.send_error(413, "agent_input_budget")
                        return
                    body = json.dumps(request).encode()
                    if len(body) > model["max_request_bytes"]:
                        self.send_error(413)
                        return
            headers = {"Content-Type": "application/json", "Accept": "application/json"}
            if key:
                headers["Authorization"] = "Bearer " + key
            if cloud:
                headers["User-Agent"] = "fiber-bundle-hermes/0.5.0"
                headers["x-opencode-session"] = value["session_id"]
            endpoint = (
                model["endpoint"]
                if inference
                else target.scheme + "://" + target.netloc + self.path
            )
            request = urllib.request.Request(endpoint, data=body, headers=headers)
            try:
                if registered_policy is not None and inference:
                    if body is None:
                        raise ValueError("agent_model_body_missing")
                    from dataclasses import replace

                    from fiber_bundle.model_configuration.protocols import prepare

                    selected = replace(registered_policy, max_output_tokens=value["max_tokens"])
                    result = registered_complete(
                        selected,
                        value["session_id"],
                        prepare(selected, json.loads(body)),
                        loaded_key=key,
                    )
                    wire = json.dumps(result).encode()
                    content_type = "application/json"
                else:
                    with opener.open(
                        request, timeout=min(value["timeout_seconds"], model["timeout_seconds"])
                    ) as response:
                        wire = response.read(1048577)
                        content_type = response.headers.get("Content-Type", "application/json")
                if len(wire) > 1048576:
                    raise ValueError("agent_model_response_limit")
                if inference:
                    trace.append({"event": "provider_response_received"})
                if inference:
                    result = json.loads(wire)
                    if result.get("model") != model["model"]:
                        raise ValueError("agent_provider_model_mismatch")
                    reported = result.get("usage")
                    if isinstance(reported, dict):
                        usage = {
                            k: reported.get(k)
                            for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                        }
                        if (
                            any(type(v) is not int or v < 0 for v in usage.values())
                            or usage["prompt_tokens"] > model["max_input_tokens"]
                            or usage["completion_tokens"] > value["max_tokens"]
                        ):
                            raise ValueError("agent_provider_usage_invalid")
                    choice = result["choices"][0]
                    if choice["finish_reason"] != "stop":
                        raise ValueError("agent_provider_incomplete")
                    if streaming:
                        chunk = {
                            "id": "managed-completion",
                            "object": "chat.completion.chunk",
                            "created": 0,
                            "model": model["model"],
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {
                                        "role": "assistant",
                                        "content": choice["message"]["content"],
                                    },
                                    "finish_reason": "stop",
                                }
                            ],
                        }
                        wire = ("data: " + json.dumps(chunk) + "\n\ndata: [DONE]\n\n").encode()
                        content_type = "text/event-stream"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(wire)))
                self.end_headers()
                self.wfile.write(wire)
            except urllib.error.HTTPError as exc:
                diagnostics["provider_error"] = "agent_provider_http_" + str(exc.code)
                self.send_response(exc.code)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"error":{"message":"agent_provider_rejected"}}')
            except Exception as exc:
                diagnostics["provider_error"] = type(exc).__name__
                self.send_error(502, "agent_provider_unavailable")

    server = ThreadingHTTPServer(("127.0.0.1", 0), Proxy)
    port = server.server_port
    allowed = {("127.0.0.1", port), *remote_addresses}

    def guard(event: str, args: tuple[Any, ...]) -> None:
        if event == "socket.connect" and args[0].family in {socket.AF_INET, socket.AF_INET6}:
            if tuple(args[1][:2]) not in allowed:
                raise PermissionError("agent_network_scope")
        if event == "socket.getaddrinfo" and args[0] not in {
            "127.0.0.1",
            "::1",
            "localhost",
            target.hostname,
        }:
            raise PermissionError("agent_network_scope")
        if event in {"subprocess.Popen", "os.system", "os.exec", "os.posix_spawn"}:
            raise PermissionError("agent_auxiliary_process_denied")
        if event == "open" and isinstance(args[0], (str, bytes)):
            path = Path(os.fsdecode(args[0])).resolve()
            writing = args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND)
            private = (
                path.is_relative_to(root.parent) and not path.is_relative_to(root)
            ) or path.name in {".env", ".op.env", "auth.json", "credentials.json"}
            if (
                (writing or private)
                and not path.is_relative_to(home)
                and path != output
                and path != Path("/dev/null")
            ):
                raise PermissionError("agent_profile_scope")

    phase = "runtime_import"
    sys.addaudithook(guard)
    sys.path.insert(0, str(root))
    sys.dont_write_bytecode = True
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    for name, expected in (("mcp", "1.26.0"), ("openai", "2.24.0"), ("httpx", "0.28.1")):
        if importlib.metadata.version(name) != expected:
            raise ValueError("agent_hermes_dependency_changed")
    AIAgent = importlib.import_module("run_agent").AIAgent

    phase = "runtime_initialization"
    agent = AIAgent(
        base_url=f"http://127.0.0.1:{port}/v1",
        api_key=proxy_token,
        model=model["model"],
        provider="openai",
        api_mode="chat_completions",
        max_iterations=1,
        max_tokens=value["max_tokens"],
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
    trace.append({"event": "hermes_initialized"})
    try:
        if agent.tools:
            raise ValueError("agent_unexpected_tools")
        phase = "model_generation"
        result = agent.run_conversation(
            value["user"], system_message=value["system"], conversation_history=[]
        )
        diagnostics["completed"] = bool(result.get("completed"))
        diagnostics["failed"] = bool(result.get("failed"))
        if result.get("failed") or not result.get("completed") or calls != 1:
            raise ValueError("agent_generation_failed")
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            json.dump(
                {
                    "answer": result["final_response"],
                    "trace": trace,
                    "provider_calls": calls,
                    "usage": usage,
                    "usage_state": "reported" if usage else "unknown",
                    "metadata_requests": metadata,
                },
                stream,
            )
    finally:
        agent.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        if diagnostic_output is not None and not diagnostic_output.exists():
            fd = os.open(
                diagnostic_output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            with os.fdopen(fd, "w") as stream:
                json.dump(
                    {
                        "error_code": "agent_hermes_failed",
                        "phase": phase,
                        "exception_type": type(error).__name__,
                        "diagnostics": diagnostics,
                        "locations": [
                            f.name for f in traceback.extract_tb(error.__traceback__)[-5:]
                        ],
                    },
                    stream,
                )
        raise SystemExit(1) from None
