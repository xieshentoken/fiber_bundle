import json
import os
import socket
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.query_routing_config import LocalQueryConfig, QueryRoutingConfig
from fiber_bundle.query_routing_probe import MAX_RESPONSE_BYTES, probe


def policy(port):
    local = LocalQueryConfig(
        image="sha256:" + "b" * 64,
        endpoint=f"http://127.0.0.1:{port}/v1/chat/completions",
        model="synthetic-local-model",
        secret_file=None,
        max_input_tokens=1024,
        max_output_tokens=512,
        max_request_bytes=65536,
        max_calls=1,
        daily_calls=2,
        timeout_seconds=2,
    )
    return QueryRoutingConfig(local, None, ())


def listing(ids=("synthetic-local-model",)):
    return json.dumps(
        {"object": "list", "data": [{"object": "model", "id": i, "owned_by": "omlx"} for i in ids]}
    ).encode()


@contextmanager
def server(body=None, status=200, headers=None, mode=None):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append((self.command, self.path, dict(self.headers)))
            try:
                if mode == "slow_headers":
                    for byte in b"HTTP/1.1 200 OK\r\n":
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.1)
                    return
                self.send_response(status)
                if "Content-Type" not in (headers or {}):
                    self.send_header("Content-Type", "application/json")
                for name, value in (headers or {}).items():
                    for entry in value if isinstance(value, list) else [value]:
                        self.send_header(name, entry)
                self.end_headers()
                response = listing() if body is None else body
                if mode == "slow_body":
                    for byte in response:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.1)
                else:
                    self.wfile.write(response)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    try:
        yield http.server_port, requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def check_report(report, config, reason):
    assert report["configuration_sha256"] == config.digest
    assert report["runtime_verified"] is report["activation_allowed"] is False
    assert report["credentials_read"] is False and report["model_calls"] == 0
    assert report["local_probe"]["reason"] == reason
    output = json.dumps(report)
    assert "127.0.0.1" not in output and "synthetic-local-model" not in output


def test_one_get_only_no_inference_or_authentication(monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with server() as (port, requests):
        config = policy(port)
        with (
            patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("inference")),
            patch(
                "fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")
            ),
            patch("sqlite3.connect", side_effect=AssertionError("database")),
        ):
            report = probe(config)
        check_report(report, config, "model_listed")
        assert report["local_probe"]["state"] == "listed"
        assert report["local_probe"]["metadata_verified"] is True
        assert len(requests) == 1 and requests[0][:2] == ("GET", "/v1/models")
        assert "Authorization" not in requests[0][2] and "Cookie" not in requests[0][2]


@pytest.mark.parametrize("ids", [[], ["different"], ["different:profile"]])
def test_absent_model_is_not_runtime_failure_or_fallback(ids):
    with server(listing(ids)) as (port, requests):
        config = policy(port)
        report = probe(config)
        check_report(report, config, "model_not_listed")
        assert report["local_probe"]["state"] == "failed"
        assert report["local_probe"]["metadata_verified"] is True
        assert len(requests) == 1
        assert report["local_failure_action"] == "stop"


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "authentication_required"),
        (403, "authentication_required"),
        (500, "http_rejected"),
        (204, "http_rejected"),
    ],
)
def test_status_safe_no_response_body_in_output(status, reason):
    with server(b"SECRET PRIVATE BODY", status=status) as (port, requests):
        config = policy(port)
        report = probe(config)
        check_report(report, config, reason)
        assert "SECRET" not in json.dumps(report)
        assert len(requests) == 1


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_redirect_is_never_followed(status):
    with server() as (target, target_requests):
        with server(
            status=status, headers={"Location": f"http://127.0.0.1:{target}/v1/models"}
        ) as (port, requests):
            config = policy(port)
            check_report(probe(config), config, "redirect_refused")
            assert len(requests) == 1 and not target_requests


@pytest.mark.parametrize(
    "body",
    [
        b"not-json",
        b"\xff",
        b"{}",
        b'{"object":"list","data":null}',
        b'{"object":"list","data":[],"data":[]}',
        b'{"object":"list","data":[{"id":"synthetic-local-model"}]}',
        b'{"object":"list","data":[{"object":"model","id":2}]}',
        b'{"object":"list","data":[{"object":"model","id":"\\ud800"}]}',
        listing(["synthetic-local-model", "synthetic-local-model"]),
        listing([""]),
        listing(["bad\nmodel"]),
        listing(["x" * 257]),
        listing([str(i) for i in range(257)]),
        b"[" * 2000,
        b'{"object":"list","data":[],"extra":NaN}',
    ],
)
def test_invalid_model_list_is_rejected(body):
    with server(body) as (port, requests):
        config = policy(port)
        check_report(probe(config), config, "response_invalid")
        assert len(requests) == 1


@pytest.mark.parametrize(
    "headers,body,reason",
    [
        ({"Content-Type": "text/html"}, listing(), "response_invalid"),
        ({"Content-Encoding": "gzip"}, listing(), "response_invalid"),
        ({"Content-Length": "oops"}, listing(), "response_invalid"),
        ({"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, b"", "response_too_large"),
        ({}, b"x" * (MAX_RESPONSE_BYTES + 1), "response_too_large"),
        ({"Content-Length": "1000"}, listing(), "response_invalid"),
        ({"Content-Length": ["0", "100"]}, listing(), "response_invalid"),
        ({"Content-Length": "0", "Transfer-Encoding": "chunked"}, b"", "response_invalid"),
    ],
)
def test_response_bounds(headers, body, reason):
    with server(body, headers=headers) as (port, requests):
        config = policy(port)
        check_report(probe(config), config, reason)
        assert len(requests) == 1


@pytest.mark.parametrize("mode", ["slow_headers", "slow_body"])
def test_wall_deadline_kills_and_reaps_worker(monkeypatch, mode):
    monkeypatch.setattr("fiber_bundle.query_routing_probe.MAX_PROBE_SECONDS", 0.6)
    popen = subprocess.Popen
    children = []

    def record(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    with server(mode=mode) as (port, requests):
        config = policy(port)
        start = time.monotonic()
        with patch("subprocess.Popen", side_effect=record):
            report = probe(config)
        assert time.monotonic() - start < 1.8
        check_report(report, config, "timeout")
        assert len(children) == 1 and children[0].poll() is not None
        assert len(requests) == 1


def test_unavailable_port_no_retry():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    config = policy(port)
    check_report(probe(config), config, "unavailable")


@pytest.mark.parametrize("configured", [False, True])
def test_unconfigured_or_secret_reference_does_not_spawn_or_network(tmp_path, configured):
    config = policy(1)
    if configured:
        secret = tmp_path / "private-key"
        secret.write_text("SECRET SENTINEL")
        config = replace(config, local_query=replace(config.local_query, secret_file=str(secret)))
    else:
        config = replace(config, local_query=None)
    with (
        patch("subprocess.run", side_effect=AssertionError("worker")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("pathlib.Path.open", side_effect=AssertionError("secret")),
    ):
        report = probe(config)
    check_report(report, config, "credentials_not_read" if configured else "local_not_configured")
    assert report["local_probe"]["state"] == "not_run"
    assert report["local_probe"]["probe_attempts"] == 0
    assert report["local_probe"]["model_listed"] is None
    assert "SECRET SENTINEL" not in json.dumps(report) and str(tmp_path) not in json.dumps(report)


def test_direct_invalid_endpoint_is_refused_before_worker():
    config = policy(1)
    config = replace(
        config,
        local_query=replace(config.local_query, endpoint="http://remote/v1/chat/completions"),
    )
    with patch("subprocess.run", side_effect=AssertionError("worker")), pytest.raises(InvalidInput):
        probe(config)


def run_cli(path, *args):
    return subprocess.run(
        [sys.executable, "-m", "fiber_bundle.query_routing_cli", "--policy-file", str(path), *args],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=15,
    )


@pytest.mark.parametrize("ids,code", [(["synthetic-local-model"], 0), ([], 1)])
def test_cli_opt_in_only_redacted_and_exit_status(tmp_path, ids, code):
    from dataclasses import asdict

    with server(listing(ids)) as (port, requests):
        config = policy(port)
        path = tmp_path / "policy.json"
        path.write_text(
            json.dumps(
                {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(config)}
            )
        )
        offline = run_cli(path)
        assert offline.returncode == 0 and not requests
        assert "local_probe" not in json.loads(offline.stdout)
        explicit = run_cli(path, "--probe-local")
        assert explicit.returncode == code and len(requests) == 1
        assert not explicit.stderr
        check_report(
            json.loads(explicit.stdout), config, "model_listed" if code == 0 else "model_not_listed"
        )


def test_valid_content_length_and_chunked_listing():
    body = listing()
    for headers, response in [
        ({"Content-Length": str(len(body))}, body),
        ({"Transfer-Encoding": "chunked"}, f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"),
    ]:
        with server(response, headers=headers) as (port, requests):
            config = policy(port)
            check_report(probe(config), config, "model_listed")
            assert len(requests) == 1


@pytest.mark.parametrize(
    "returncode,output", [(0, "PRIVATE endpoint model secret"), (1, "model_listed")]
)
def test_worker_failure_output_not_exposed(returncode, output):
    config = policy(1)
    child = subprocess.CompletedProcess([], returncode, output, "SECRET")
    with patch("subprocess.run", return_value=child) as run:
        report = probe(config)
    check_report(report, config, "unavailable")
    assert "PRIVATE" not in json.dumps(report) and "SECRET" not in json.dumps(report)
    assert run.call_count == 1 and run.call_args.kwargs["timeout"] == 2


def test_worker_spawn_error_is_safe():
    config = policy(1)
    with patch("subprocess.run", side_effect=OSError("PRIVATE")):
        check_report(probe(config), config, "unavailable")


def test_cli_default_empty_policy_opt_in_remains_not_run():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    child = run_cli(root / "configs/query-routing.example.json", "--probe-local")
    assert child.returncode == 0 and not child.stderr
    report = json.loads(child.stdout)
    assert report["state"] == "incomplete"
    assert report["local_probe"]["state"] == "not_run"
    assert report["local_probe"]["probe_attempts"] == 0
