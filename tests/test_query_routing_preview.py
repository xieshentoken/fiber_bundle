import hashlib
import json
import os
import subprocess
import sys
from unittest.mock import patch

import pytest
from test_query_routing_preparation import routing as routing

from fiber_bundle.config import InvalidInput
from fiber_bundle.query_routing_boundary import cloud_answer_request
from fiber_bundle.query_routing_config import parse
from fiber_bundle.query_routing_preview import MAX_INPUT_BYTES, preview


def write_answer(tmp_path, answer="PRIVATE ANSWER SENTINEL", **extra):
    path = tmp_path / "private-answer.json"
    path.write_text(json.dumps({"schema": "local-answer/v1", "answer": answer, **extra}))
    return path


def test_preview_no_network_secret_database_worker_or_raw_output(routing, tmp_path):
    config = parse(routing)
    path = write_answer(tmp_path)
    raw = path.read_bytes()
    payload = cloud_answer_request(config, json.loads(raw))
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("model")),
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("sqlite3.connect", side_effect=AssertionError("database")),
        patch("subprocess.run", side_effect=AssertionError("worker")),
    ):
        report = preview(config, path)
    summary = report["answer_preview"]
    wire = json.dumps(payload).encode("utf-8")
    assert summary["input_sha256"] == hashlib.sha256(raw).hexdigest()
    assert summary["request_sha256"] == hashlib.sha256(wire).hexdigest()
    assert summary["request_bytes"] == len(wire) and summary["input_bytes"] == len(raw)
    assert report["configuration_sha256"] == config.digest
    assert summary["boundary_checked"] is True and summary["requests_sent"] == 0
    assert summary["provenance_verified"] is summary["semantic_privacy_verified"] is False
    assert report["runtime_verified"] is report["activation_allowed"] is False
    assert report["model_calls"] == 0 and report["credentials_read"] is False
    output = json.dumps(report)
    for sensitive in [
        "PRIVATE ANSWER",
        str(path),
        "127.0.0.1",
        routing["cloud_answer"]["model"],
        routing["cloud_answer"]["secret_file"],
    ]:
        assert sensitive not in output
    assert path.read_bytes() == raw


@pytest.mark.parametrize(
    "field",
    [
        "sources",
        "evidence",
        "raw",
        "messages",
        "tools",
        "endpoint",
        "api_key",
        "question",
        "run_id",
        "provenance_verified",
    ],
)
def test_extra_evidence_transport_or_claim_fields_rejected(routing, tmp_path, field):
    path = write_answer(tmp_path, **{field: "RAW EVIDENCE SENTINEL"})
    with pytest.raises(InvalidInput):
        preview(parse(routing), path)


@pytest.mark.parametrize(
    "value",
    [
        [],
        None,
        {"schema": "local-answer/v2", "answer": "text"},
        {"schema": "local-answer/v1", "answer": " "},
        {"schema": "local-answer/v1", "answer": 12},
        {"schema": "local-answer/v1", "answer": "\ud800"},
    ],
)
def test_answer_contract_rejection(routing, tmp_path, value):
    path = tmp_path / "answer.json"
    path.write_text(json.dumps(value))
    with pytest.raises(InvalidInput):
        preview(parse(routing), path)


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema":"local-answer/v1","answer":"one","answer":"two"}',
        b'{"schema":"local-answer/v1","answer":NaN}',
        b'{"schema":"local-answer/v1","answer":Infinity}',
        b"{private-secret",
        b"\xff",
        b"[" * 2000,
        '{"schema":"local-answer/v1","answer":"text"}'.encode("utf-16"),
    ],
)
def test_strict_json_utf8_rejection(routing, tmp_path, raw):
    path = tmp_path / "answer.json"
    path.write_bytes(raw)
    with pytest.raises(InvalidInput):
        preview(parse(routing), path)


@pytest.mark.parametrize(
    "kind",
    ["symlink", "parent_symlink", "hardlink", "fifo", "directory", "empty", "oversized", "missing"],
)
def test_file_type_path_and_size_rejection(routing, tmp_path, kind):
    original = write_answer(tmp_path)
    path = tmp_path / "candidate.json"
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "parent_symlink":
        path.symlink_to(tmp_path, target_is_directory=True)
        path = path / original.name
    elif kind == "hardlink":
        os.link(original, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    elif kind == "empty":
        path.touch()
    elif kind == "oversized":
        path.write_bytes(b" " * (MAX_INPUT_BYTES + 1))
    with pytest.raises((InvalidInput, OSError)):
        preview(parse(routing), path)


def test_answer_byte_limit_including_unicode(routing, tmp_path):
    config = parse(routing)
    path = write_answer(tmp_path, "x" * 16384)
    assert preview(config, path)["answer_preview"]["answer_utf8_bytes"] == 16384
    path = write_answer(tmp_path, "x" * 16385)
    with pytest.raises(InvalidInput):
        preview(config, path)
    path = write_answer(tmp_path, "中" * 5461)
    assert preview(config, path)["answer_preview"]["answer_utf8_bytes"] == 16383
    path = write_answer(tmp_path, "中" * 5462)
    with pytest.raises(InvalidInput):
        preview(config, path)


def test_wire_byte_limit_includes_escaped_unicode(routing, tmp_path):
    config = parse(routing)
    path = write_answer(tmp_path, "中" * 150)
    payload = cloud_answer_request(config, json.loads(path.read_bytes()))
    routing["cloud_answer"]["max_request_bytes"] = len(
        json.dumps(payload, ensure_ascii=False).encode()
    )
    config = parse(routing)
    assert len(json.dumps(payload).encode()) > config.cloud_answer.max_request_bytes
    with pytest.raises(InvalidInput, match="answer_preview_request_too_large"):
        preview(config, path)


def test_exact_wire_limit_and_summary_binding(routing, tmp_path):
    path = write_answer(tmp_path)
    original = preview(parse(routing), path)
    routing["cloud_answer"]["max_request_bytes"] = original["answer_preview"]["request_bytes"]
    exact = preview(parse(routing), path)
    assert exact["answer_preview"] == original["answer_preview"]
    assert exact["configuration_sha256"] != original["configuration_sha256"]
    routing["cloud_answer"]["max_request_bytes"] -= 1
    with pytest.raises(InvalidInput):
        preview(parse(routing), path)


def test_missing_cloud_role_is_rejected(routing, tmp_path):
    routing["cloud_answer"] = None
    path = write_answer(tmp_path)
    with pytest.raises(InvalidInput, match="cloud_answer_not_configured"):
        preview(parse(routing), path)


def test_answer_claim_is_not_authenticated_or_semantically_filtered(routing, tmp_path):
    path = write_answer(tmp_path, "Quoted original and secret-looking text inside the answer.")
    report = preview(parse(routing), path)
    assert report["answer_preview"]["state"] == "prepared"
    assert report["answer_preview"]["semantic_privacy_verified"] is False
    assert report["answer_preview"]["provenance_verified"] is False
    assert "Quoted original" not in json.dumps(report)


def cli(policy, *args):
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "fiber_bundle.query_routing_cli",
            "--policy-file",
            str(policy),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=5,
    )


def test_cli_offline_success_and_rejection_are_redacted(routing, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(routing))
    answer = write_answer(tmp_path)
    result = cli(policy, "--preview-answer-file", str(answer))
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == preview(parse(routing), answer)
    assert "PRIVATE ANSWER" not in result.stdout and str(tmp_path) not in result.stdout
    answer = write_answer(tmp_path, evidence="PRIVATE RAW SENTINEL")
    result = cli(policy, "--preview-answer-file", str(answer))
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout) == {"state": "rejected", "reason": "routing_preflight_failed"}
    assert "PRIVATE RAW" not in result.stdout and str(tmp_path) not in result.stdout


def test_cli_network_probe_and_offline_preview_are_mutually_exclusive(tmp_path):
    result = cli(
        tmp_path / "NONEXISTENT_POLICY",
        "--preview-answer-file",
        "PRIVATE_FILENAME",
        "--probe-local",
    )
    assert result.returncode == 2 and not result.stdout
    assert "not allowed with argument" in result.stderr
    assert "PRIVATE_FILENAME" not in result.stderr and "NONEXISTENT_POLICY" not in result.stderr


def test_selected_input_changes_bound_digests(routing, tmp_path):
    config = parse(routing)
    path = write_answer(tmp_path, "answer one")
    first = preview(config, path)
    path = write_answer(tmp_path, "answer two")
    second = preview(config, path)
    assert first["configuration_sha256"] == second["configuration_sha256"]
    for key in ["input_sha256", "request_sha256"]:
        assert first["answer_preview"][key] != second["answer_preview"][key]


def test_exact_input_file_limit_and_utf8_snapshot(routing, tmp_path):
    path = write_answer(tmp_path, "中文答案")
    raw = json.dumps(json.loads(path.read_bytes()), ensure_ascii=False).encode("utf-8")
    path.write_bytes(raw + b" " * (MAX_INPUT_BYTES - len(raw)))
    result = preview(parse(routing), path)["answer_preview"]
    assert result["input_bytes"] == MAX_INPUT_BYTES
    assert result["answer_utf8_bytes"] == len("中文答案".encode())


def test_input_changed_during_snapshot_is_rejected(routing, tmp_path):
    path = write_answer(tmp_path)
    real_stat = os.fstat
    count = 0

    def changing_stat(fd):
        nonlocal count
        count += 1
        if count == 2:
            current = path.stat()
            os.utime(path, ns=(current.st_atime_ns, current.st_mtime_ns + 1000000))
        return real_stat(fd)

    with patch("fiber_bundle.query_routing_files.os.fstat", side_effect=changing_stat):
        with pytest.raises(InvalidInput, match="routing_file_changed"):
            preview(parse(routing), path)
