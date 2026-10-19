import json
import os
import subprocess
import sys
import threading
from contextlib import contextmanager
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from test_query_routing_probe import listing, policy, run_cli, server

from fiber_bundle.config import InvalidInput
from fiber_bundle.query_routing_credentials import load
from fiber_bundle.query_routing_probe import probe

KEY = "synthetic_LOCAL_only_token_123456"


def authenticated_policy(tmp_path, port, raw=None, suffix=".txt"):
    secret = tmp_path / ("private-key" + suffix)
    secret.write_bytes((KEY + "\n").encode() if raw is None else raw)
    config = policy(port)
    return replace(config, local_query=replace(config.local_query, secret_file=str(secret))), secret


@contextmanager
def authenticated_server():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            accepted = self.headers.get("Authorization") == "Bearer " + KEY
            requests.append((self.command, self.path, accepted))
            body = listing() if accepted else b'{"error":"PRIVATE SERVER RESPONSE"}'
            self.send_response(200 if accepted else 401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

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


def assert_redacted(report, config, secret):
    output = json.dumps(report)
    for value in [
        KEY,
        str(secret),
        config.local_query.model,
        config.local_query.endpoint,
        "PRIVATE SERVER RESPONSE",
    ]:
        assert value not in output
    assert report["configuration_sha256"] == config.digest
    assert report["activation_allowed"] is report["runtime_verified"] is False
    assert report["model_calls"] == 0
    assert report["local_probe"]["authentication_verified"] is False


def test_only_explicit_auth_reads_credential_and_gets_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with authenticated_server() as (port, requests):
        config, secret = authenticated_policy(tmp_path, port)
        before = secret.read_bytes()
        with patch("subprocess.run", side_effect=AssertionError("default worker")):
            normal = probe(config)
        assert normal["credentials_read"] is False
        assert normal["local_probe"]["reason"] == "credentials_not_read" and not requests
        result = probe(config, read_credentials=True)
        assert result["local_probe"]["state"] == "listed"
        assert result["local_probe"]["credential_read_requested"] is True
        assert result["credentials_read"] is True
        assert requests == [("GET", "/v1/models", True)]
        assert secret.read_bytes() == before
        assert_redacted(result, config, secret)


def test_invalid_token_is_not_retried_without_auth(tmp_path):
    with authenticated_server() as (port, requests):
        config, secret = authenticated_policy(tmp_path, port, b"synthetic_WRONG_key")
        report = probe(config, read_credentials=True)
        assert report["local_probe"]["state"] == "failed"
        assert report["local_probe"]["reason"] == "authentication_required"
        assert report["credentials_read"] is True
        assert requests == [("GET", "/v1/models", False)]
        assert_redacted(report, config, secret)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_http_failure_keeps_credential_read_evidence(tmp_path, status):
    with server(body=KEY.encode(), status=status) as (port, requests):
        config, secret = authenticated_policy(tmp_path, port)
        report = probe(config, read_credentials=True)
        assert report["credentials_read"] is True
        assert report["local_probe"]["state"] == "failed"
        assert len(requests) == 1
        assert_redacted(report, config, secret)


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_redirect_never_forwards_bearer(tmp_path, status):
    with server() as (target, target_requests):
        with server(
            status=status, headers={"Location": f"http://127.0.0.1:{target}/v1/models"}
        ) as (port, requests):
            config, secret = authenticated_policy(tmp_path, port)
            report = probe(config, read_credentials=True)
            assert report["local_probe"]["reason"] == "redirect_refused"
            assert report["credentials_read"] is True
            assert len(requests) == 1 and not target_requests
            assert_redacted(report, config, secret)


@pytest.mark.parametrize(
    "raw",
    [
        b" ",
        b"key\r\nInjected: header",
        b"one two",
        b"abc=def",
        b"\xff",
        "中文".encode(),
        b'{"key":"abc"}',
    ],
)
def test_invalid_secret_format_stops_before_http(tmp_path, raw):
    with server() as (port, requests):
        config, secret = authenticated_policy(tmp_path, port, raw)
        report = probe(config, read_credentials=True)
        assert report["local_probe"]["reason"] == "credential_invalid"
        assert report["credentials_read"] is True and not requests
        assert_redacted(report, config, secret)


@pytest.mark.parametrize(
    "kind",
    ["symlink", "parent_symlink", "hardlink", "fifo", "directory", "empty", "oversized", "missing"],
)
def test_unsafe_secret_file_stops_before_http(tmp_path, kind):
    with server() as (port, requests):
        config, original = authenticated_policy(tmp_path, port)
        secret = tmp_path / "unsafe-key"
        if kind == "symlink":
            secret.symlink_to(original)
        elif kind == "parent_symlink":
            secret.symlink_to(tmp_path, target_is_directory=True)
            secret = secret / original.name
        elif kind == "hardlink":
            os.link(original, secret)
        elif kind == "fifo":
            os.mkfifo(secret)
        elif kind == "directory":
            secret.mkdir()
        elif kind == "empty":
            secret.touch()
        elif kind == "oversized":
            secret.write_bytes(b"x" * 4099)
        config = replace(config, local_query=replace(config.local_query, secret_file=str(secret)))
        report = probe(config, read_credentials=True)
        assert report["local_probe"]["reason"] == "credential_file_rejected" and not requests
        assert report["credentials_read"] is None
        assert_redacted(report, config, secret)


def test_rtf_is_not_opened_converted_or_copied(tmp_path):
    path = tmp_path / "not-read.rtf"
    with patch("fiber_bundle.query_routing_credentials.read", side_effect=AssertionError("read")):
        assert load(path) == (None, False, "credential_format_unsupported")
    with server() as (port, requests):
        config, secret = authenticated_policy(tmp_path, port, b"{\\rtf1 PRIVATE}", suffix=".rtf")
        report = probe(config, read_credentials=True)
        assert report["local_probe"]["reason"] == "credential_format_unsupported"
        assert report["credentials_read"] is False and not requests
        assert_redacted(report, config, secret)


@pytest.mark.parametrize("configured", [False, True])
def test_missing_local_or_secret_reference_is_not_run(configured):
    config = policy(1)
    if not configured:
        config = replace(config, local_query=None)
    with patch("subprocess.run", side_effect=AssertionError("worker")):
        report = probe(config, read_credentials=True)
    assert report["local_probe"]["state"] == "not_run"
    assert report["local_probe"]["reason"] == (
        "credentials_not_configured" if configured else "local_not_configured"
    )
    assert report["credentials_read"] is False


@pytest.mark.parametrize("mode", ["slow_headers", "slow_body"])
def test_timeout_does_not_claim_secret_unread_and_reaps_worker(tmp_path, monkeypatch, mode):
    monkeypatch.setattr("fiber_bundle.query_routing_probe.MAX_PROBE_SECONDS", 0.6)
    real_popen = subprocess.Popen
    children = []

    def record(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        children.append(child)
        return child

    with server(mode=mode) as (port, requests):
        config, secret = authenticated_policy(tmp_path, port)
        with patch("subprocess.Popen", side_effect=record):
            report = probe(config, read_credentials=True)
        assert report["local_probe"]["reason"] == "timeout"
        assert report["credentials_read"] is None and len(requests) == 1
        assert len(children) == 1 and children[0].poll() is not None
        assert_redacted(report, config, secret)


def test_secret_snapshot_change_reports_unknown_read_state(tmp_path):
    path = tmp_path / "synthetic-key"
    path.write_text(KEY)
    with patch("fiber_bundle.query_routing_credentials.read", side_effect=InvalidInput("changed")):
        assert load(path) == (None, None, "credential_file_rejected")


@pytest.mark.parametrize("key", ["a", "a.b_c-~+/=", "x" * 4096])
def test_ascii_bearer_token_size_and_alphabet(tmp_path, key):
    path = tmp_path / "synthetic-key"
    path.write_bytes((key + "\r\n").encode())
    assert load(path) == (key, True, None)


@pytest.mark.parametrize("option", [1, None, "true"])
def test_auth_option_must_be_explicit_bool(option):
    with patch("subprocess.run", side_effect=AssertionError("worker")), pytest.raises(InvalidInput):
        probe(policy(1), read_credentials=option)


def test_invalid_endpoint_is_refused_before_credential_read(tmp_path):
    config, secret = authenticated_policy(tmp_path, 1)
    config = replace(
        config,
        local_query=replace(config.local_query, endpoint="http://remote/v1/chat/completions"),
    )
    with patch("subprocess.run", side_effect=AssertionError("worker")), pytest.raises(InvalidInput):
        probe(config, read_credentials=True)


def test_cli_explicit_auth_and_no_raw_key_in_ipc(tmp_path):
    with authenticated_server() as (port, requests):
        config, secret = authenticated_policy(tmp_path, port)
        path = tmp_path / "policy.json"
        path.write_text(
            json.dumps(
                {"schema": "query-routing-preparation/v1", "enabled": False, **asdict(config)}
            )
        )
        normal = run_cli(path, "--probe-local")
        assert normal.returncode == 0 and not requests
        result = run_cli(path, "--probe-local-auth")
        assert result.returncode == 0 and not result.stderr
        assert_redacted(json.loads(result.stdout), config, secret)
        assert requests == [("GET", "/v1/models", True)]
        real_run = subprocess.run
        captured = []

        def record(*args, **kwargs):
            captured.append((args, kwargs))
            return real_run(*args, **kwargs)

        with patch("subprocess.run", side_effect=record):
            report = probe(config, read_credentials=True)
        assert KEY not in json.dumps(captured)
        assert_redacted(report, config, secret)


@pytest.mark.parametrize("args", [["--probe-local"], ["--preview-answer-file", "PRIVATE_ANSWER"]])
def test_cli_auth_is_exclusive_with_other_modes(tmp_path, args):
    result = run_cli(tmp_path / "PRIVATE_POLICY", "--probe-local-auth", *args)
    assert result.returncode == 2 and not result.stdout
    assert "PRIVATE_ANSWER" not in result.stderr and "PRIVATE_POLICY" not in result.stderr


def test_private_worker_revalidates_endpoint_before_secret_access(tmp_path):
    config, secret = authenticated_policy(tmp_path, 1)
    local = asdict(config.local_query)
    local["endpoint"] = "http://remote/v1/chat/completions"
    result = subprocess.run(
        [sys.executable, "-m", "fiber_bundle.query_routing_probe"],
        input=json.dumps({"local": local, "read_credentials": True}),
        capture_output=True,
        text=True,
        timeout=3,
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {"reason": "response_invalid", "credentials_read": False}
    assert KEY not in result.stdout and str(secret) not in result.stdout
