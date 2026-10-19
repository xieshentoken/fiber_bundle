import copy
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import contextmanager
from dataclasses import asdict, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

import pytest
from test_answer_tasks import response
from test_answer_tasks import task as task
from test_execution import policy as policy
from test_query_routing_probe import policy as routing_policy
from test_session_search import owner as owner

from fiber_bundle import answer_tasks, auth, database, execution_ledger, model_gateway
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.local_query_contract import accept, prepare
from fiber_bundle.local_query_gateway import MAX_RESPONSE_BYTES, complete

SENTINEL = "LOCAL RAW EVIDENCE SENTINEL"
KEY = "synthetic_LOCAL_token"


def request():
    return {
        "messages": [
            {"role": "system", "content": "Return only the host-bound answer JSON."},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "question": "Question?",
                        "quotations": [{"citation": "[1]", "quote": SENTINEL}],
                    }
                ),
            },
        ]
    }


def completion(payload=None):
    result = response(payload or {"messages": request()["messages"]})
    result.update(model="synthetic-local-model", debug="PRIVATE PROVIDER DEBUG")
    result["choices"][0]["message"]["reasoning_content"] = "PRIVATE REASONING"
    return result


@contextmanager
def server(*, body=None, status=200, headers=None, mode=None, require_key=False):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            payload = json.loads(raw)
            authorized = self.headers.get("Authorization") == "Bearer " + KEY
            requests.append((self.command, self.path, payload, authorized))
            output = json.dumps(completion(payload)).encode() if body is None else body
            code = status if not require_key or authorized else 401
            if mode == "slow_headers":
                time.sleep(1.8)
            self.send_response(code)
            configured = {"Content-Type": "application/json", **(headers or {})}
            if "Content-Length" not in configured and mode != "slow_body":
                configured["Content-Length"] = str(len(output))
            for name, values in configured.items():
                for value in values if isinstance(values, list) else [values]:
                    self.send_header(name, value)
            self.end_headers()
            try:
                if mode == "slow_body":
                    for byte in output:
                        self.wfile.write(bytes([byte]))
                        self.wfile.flush()
                        time.sleep(0.02)
                else:
                    self.wfile.write(output)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def log_message(self, *args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=lambda: http.serve_forever(poll_interval=0.05), daemon=True)
    thread.start()
    try:
        yield routing_policy(http.server_port).local_query, requests
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)


def fail(local, reason):
    with pytest.raises(model_gateway.ModelFailure) as caught:
        complete(local, request(), allow_inference=True)
    assert str(caught.value) == reason and SENTINEL not in str(caught.value)


def test_local_post_is_fixed_single_call_no_proxy_or_cloud(monkeypatch):
    for name in ["http_proxy", "HTTP_PROXY", "ALL_PROXY"]:
        monkeypatch.setenv(name, "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    with (
        server() as (local, requests),
        patch.object(model_gateway, "complete", side_effect=AssertionError("cloud")),
    ):
        result = complete(local, request(), allow_inference=True)
    assert len(requests) == 1
    method, path, payload, authorized = requests[0]
    assert (method, path, authorized) == ("POST", "/v1/chat/completions", False)
    assert set(payload) == {"model", "messages", "max_tokens", "stream"}
    assert payload["model"] == local.model and payload["max_tokens"] == local.max_output_tokens
    assert payload["stream"] is False and SENTINEL in json.dumps(payload)
    assert result["choices"][0]["message"]["role"] == "assistant"
    assert result["usage"]["total_tokens"] == 50
    assert "PRIVATE PROVIDER" not in json.dumps(result) and "PRIVATE REASONING" not in json.dumps(
        result
    )


@pytest.mark.parametrize("flag", [False, None, 0, 1, "true"])
def test_inference_is_dormant_without_exact_opt_in(flag):
    with patch("subprocess.run", side_effect=AssertionError("worker")):
        with pytest.raises(InvalidInput, match="local_inference_not_enabled"):
            complete(routing_policy(1).local_query, request(), allow_inference=flag)


@pytest.mark.parametrize("change", ["endpoint", "tools", "role", "extra", "surrogate", "size"])
def test_bad_configuration_or_request_is_rejected_before_worker(change):
    local = routing_policy(1).local_query
    value = request()
    if change == "endpoint":
        local = replace(local, endpoint="http://remote/v1/chat/completions")
    elif change == "tools":
        value["tools"] = [{"type": "function"}]
    elif change == "role":
        value["messages"][1]["role"] = "tool"
    elif change == "extra":
        value["messages"][1]["secret"] = "PRIVATE SECRET"
    elif change == "surrogate":
        value["messages"][1]["content"] = "\ud800"
    else:
        local = replace(local, max_request_bytes=100)
    with patch("subprocess.run", side_effect=AssertionError("worker")):
        with pytest.raises(InvalidInput):
            complete(local, value, allow_inference=True)


def test_wire_byte_limit_uses_actual_ascii_escaping():
    local = routing_policy(1).local_query
    value = request()
    value["messages"][1]["content"] = "中文" * 100
    payload = prepare(local, value)
    size = len(json.dumps(payload).encode())
    assert size > len(json.dumps(payload, ensure_ascii=False).encode())
    assert prepare(replace(local, max_request_bytes=size), value) == payload
    with pytest.raises(InvalidInput, match="local_request_too_large"):
        prepare(replace(local, max_request_bytes=size - 1), value)


def test_credential_remains_in_worker_and_has_no_plaintext_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("PRIVATE_UNRELATED_SECRET", KEY)
    key_file = tmp_path / "private-key.txt"
    key_file.write_text(KEY)
    before = set(tmp_path.iterdir())
    captured = []
    original_run = subprocess.run

    def record(*args, **kwargs):
        captured.append((args, kwargs))
        return original_run(*args, **kwargs)

    with server(require_key=True) as (local, requests):
        local = replace(local, secret_file=str(key_file))
        with patch("subprocess.run", side_effect=record):
            result = complete(local, request(), allow_inference=True)
    assert len(requests) == 1 and requests[0][3] is True
    assert all(KEY not in repr(item) for item in captured)
    assert KEY not in json.dumps(result)
    assert set(tmp_path.iterdir()) == before and key_file.read_text() == KEY


@pytest.mark.parametrize("kind", ["rtf", "invalid", "symlink", "missing"])
def test_bad_credential_does_not_send_anonymous_request(tmp_path, kind):
    secret = tmp_path / ("key.rtf" if kind == "rtf" else "key.txt")
    if kind != "missing":
        secret.write_text("{\\rtf1 synthetic}" if kind == "rtf" else "header\r\ninjection")
    if kind == "symlink":
        real = secret.with_suffix(".original")
        secret.rename(real)
        secret.symlink_to(real)
    reason = (
        "local_credential_"
        + {
            "rtf": "format_unsupported",
            "invalid": "invalid",
            "symlink": "file_rejected",
            "missing": "file_rejected",
        }[kind]
    )
    with server() as (local, requests):
        fail(replace(local, secret_file=str(secret)), reason)
        assert not requests


@pytest.mark.parametrize("status", [301, 302, 307, 308, 401, 403, 429, 500])
def test_http_failures_stop_once_without_fallback(status):
    with (
        server() as (target, target_requests),
        server(status=status, headers={"Location": target.endpoint}) as (local, requests),
    ):
        reason = (
            "local_authentication_required"
            if status in {401, 403}
            else ("local_redirect_refused" if status < 400 else "local_http_rejected")
        )
        fail(local, reason)
        assert len(requests) == 1 and not target_requests


@pytest.mark.parametrize(
    "mutation",
    [
        "model",
        "usage_missing",
        "usage_bool",
        "usage_input",
        "usage_output",
        "usage_zero",
        "unfinished",
        "role",
        "tools",
        "json",
        "duplicate_content",
        "surrogate",
    ],
)
def test_provider_output_cannot_claim_success_with_invalid_contract(mutation):
    result = completion()
    message = result["choices"][0]["message"]
    if mutation == "model":
        result["model"] = "other-model"
    elif mutation == "usage_missing":
        result.pop("usage")
    elif mutation == "usage_bool":
        result["usage"]["prompt_tokens"] = True
    elif mutation == "usage_input":
        result["usage"]["prompt_tokens"] = 200001
    elif mutation == "usage_output":
        result["usage"]["completion_tokens"] = 8193
    elif mutation == "usage_zero":
        result["usage"]["completion_tokens"] = 0
    elif mutation == "unfinished":
        result["choices"][0]["finish_reason"] = "length"
    elif mutation == "role":
        message["role"] = "tool"
    elif mutation == "tools":
        message["tool_calls"] = [{"id": "UNAPPROVED"}]
    elif mutation == "json":
        message["content"] = "not json"
    elif mutation == "duplicate_content":
        message["content"] = '{"mode":"extractive","mode":"extractive"}'
    else:
        message["content"] = '{"text":"\ud800"}'
    with server(body=json.dumps(result).encode()) as (local, requests):
        fail(local, "local_response_invalid")
        assert len(requests) == 1


@pytest.mark.parametrize(
    "body,headers,reason",
    [
        (b'{"model":"x","model":"x"}', {}, "local_response_invalid"),
        (b'{"value":NaN}', {}, "local_response_invalid"),
        (b"\xff", {}, "local_response_invalid"),
        (b"[" * 2000, {}, "local_response_invalid"),
        (b"{}", {"Content-Type": "text/plain"}, "local_response_invalid"),
        (b"{}", {"Content-Encoding": "gzip"}, "local_response_invalid"),
        (b"{}", {"Content-Length": ["2", "2"]}, "local_response_invalid"),
        (b"{}", {"Content-Length": "oops"}, "local_response_invalid"),
        (b"{}", {"Content-Length": "1000"}, "local_response_invalid"),
        (b"", {"Content-Length": "0", "Transfer-Encoding": "chunked"}, "local_response_invalid"),
        (b"", {"Content-Length": str(MAX_RESPONSE_BYTES + 1)}, "local_response_too_large"),
        (b"x" * (MAX_RESPONSE_BYTES + 1), {}, "local_response_too_large"),
    ],
    ids=lambda value: f"bytes-{len(value)}" if isinstance(value, bytes) else None,
)
def test_http_response_limits_and_strict_json(body, headers, reason):
    with server(body=body, headers=headers) as (local, requests):
        fail(local, reason)
        assert len(requests) == 1


@pytest.mark.parametrize("mode", ["slow_headers", "slow_body"])
def test_wall_timeout_kills_worker_and_does_not_retry(mode):
    popen = subprocess.Popen
    children = []

    def record(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child

    with server(mode=mode) as (local, requests):
        started = time.monotonic()
        with patch("subprocess.Popen", side_effect=record):
            fail(replace(local, timeout_seconds=1), "local_timeout")
        assert time.monotonic() - started < 1.6
        assert len(requests) == 1 and len(children) == 1 and children[0].poll() is not None
        with pytest.raises(ChildProcessError):
            os.waitpid(children[0].pid, os.WNOHANG)


def test_connection_refused_is_local_failure_not_cloud():
    with server() as (local, _):
        pass
    with patch.object(model_gateway, "complete", side_effect=AssertionError("cloud")):
        fail(local, "local_unavailable")


@pytest.mark.parametrize(
    "reply",
    [
        b"not-json",
        b'{"ok":1,"body":{}}',
        b'{"ok":false,"reason":"PRIVATE ERROR"}',
        b'{"ok":false,"reason":[]}',
        b'{"ok":true,"body":{},"extra":true}',
        b'{"ok":false,"reason":"local_timeout","reason":"local_unavailable"}',
    ],
)
def test_parent_rejects_bad_worker_protocol_without_exposing_output(reply):
    result = subprocess.CompletedProcess([], 0, reply, b"PRIVATE STDERR")
    with patch("subprocess.run", return_value=result):
        fail(routing_policy(1).local_query, "local_response_invalid")


@pytest.mark.parametrize("change", ["endpoint", "stream_bool", "max_bool", "extra"])
def test_private_worker_revalidates_before_credential_access(tmp_path, change):
    local = routing_policy(1).local_query
    secret = tmp_path / "PRIVATE_NONEXISTENT_KEY.txt"
    local = replace(local, secret_file=str(secret), max_output_tokens=1)
    payload = prepare(local, request())
    value = {"local": asdict(local), "payload": payload}
    if change == "endpoint":
        value["local"]["endpoint"] = "http://remote/v1/chat/completions"
    elif change == "stream_bool":
        payload["stream"] = 0
    elif change == "max_bool":
        payload["max_tokens"] = True
    else:
        payload["extra"] = "PRIVATE VALUE"
    result = subprocess.run(
        [sys.executable, "-m", "fiber_bundle.local_query_gateway"],
        input=json.dumps(value).encode(),
        capture_output=True,
        timeout=3,
    )
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == {"ok": False, "reason": "local_response_invalid"}
    assert b"PRIVATE" not in result.stdout


def test_bound_host_request_and_output_reuse_existing_answer_validation(config, policy, task):
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, task[0], policy)
    run = execution_ledger.get_run(db, task[0])
    value = answer_tasks.model_request(config, db, run, answer_tasks.get(db, task[0]))
    with server() as (local, requests):
        body = complete(local, value, allow_inference=True)
    answer_tasks.accept(config, db, run, body)
    assert answer_tasks.get(db, task[0])["result_json"] is not None
    assert "UNTRUSTED PI" not in json.dumps(requests[0][2])
    # This tests the adapter seam only, not a completed dispatch or a budget reservation.
    assert (
        run["state"] == "running"
        and db.execute("SELECT COUNT(*) FROM model_calls").fetchone()[0] == 0
    )
    db.close()


@pytest.mark.parametrize("mutation", ["revoke", "evidence"])
def test_existing_host_binding_rejects_stale_evidence_before_local_call(
    config, policy, task, owner, mutation
):
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, task[0], policy)
    if mutation == "revoke":
        auth.revoke_client(db, owner[0])
    else:
        (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
    with patch("subprocess.run", side_effect=AssertionError("local worker")):
        with pytest.raises((auth.Denied, IntegrityError)):
            value = answer_tasks.model_request(
                config, db, execution_ledger.get_run(db, task[0]), answer_tasks.get(db, task[0])
            )
            complete(routing_policy(1).local_query, value, allow_inference=True)
    assert answer_tasks.get(db, task[0])["result_json"] is None
    db.close()


def test_host_still_rejects_invented_quotation_from_structurally_valid_local_response(
    config, policy, task
):
    db = database.connect(config.state_root)
    execution_ledger.start(config, db, task[0], policy)
    run = execution_ledger.get_run(db, task[0])
    value = answer_tasks.model_request(config, db, run, answer_tasks.get(db, task[0]))
    output = copy.deepcopy(completion(prepare(routing_policy(1).local_query, value)))
    proposal = json.loads(output["choices"][0]["message"]["content"])
    proposal["quotations"][0]["quote"] = "invented 999 psi"
    output["choices"][0]["message"]["content"] = json.dumps(proposal)
    normalized = accept(routing_policy(1).local_query, output)
    with pytest.raises(InvalidInput, match="quote_mismatch"):
        answer_tasks.accept(config, db, run, normalized)
    assert answer_tasks.get(db, task[0])["result_json"] is None
    db.close()
