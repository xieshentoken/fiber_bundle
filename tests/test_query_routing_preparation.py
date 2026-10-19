import copy
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import pytest

from fiber_bundle.config import InvalidInput
from fiber_bundle.execution_config import ExecutionConfig
from fiber_bundle.query_routing_boundary import cloud_answer_request, local_failure
from fiber_bundle.query_routing_config import SCHEMA, load, parse


@pytest.fixture
def routing(tmp_path):
    cloud = ExecutionConfig(
        image="sha256:" + "a" * 64,
        endpoint="https://opencode.ai/zen/go/v1/chat/completions",
        model="glm-5.3-flash",
        secret_file=str(tmp_path / "unread-cloud-secret"),
        price_revision="synthetic-pricing",
        input_nusd=150,
        output_nusd=500,
        max_input_tokens=1024,
        max_output_tokens=512,
        max_request_bytes=65536,
        task_limit_nusd=1000000,
        daily_limit_nusd=2000000,
        max_calls=1,
        daily_calls=2,
        max_attempts=1,
        timeout_seconds=60,
        timezone="UTC",
    )
    return {
        "schema": SCHEMA,
        "enabled": False,
        "local_query": {
            "image": "sha256:" + "b" * 64,
            "endpoint": "http://127.0.0.1:8000/v1/chat/completions",
            "model": "synthetic-local-model",
            "secret_file": str(tmp_path / "unread-local-secret"),
            "max_input_tokens": 1024,
            "max_output_tokens": 512,
            "max_request_bytes": 65536,
            "max_calls": 1,
            "daily_calls": 2,
            "timeout_seconds": 60,
        },
        "cloud_answer": asdict(cloud),
        "approved_backups": [],
    }


def test_preflight_has_no_provider_secret_or_database_access(routing, tmp_path):
    path = tmp_path / "policy.json"
    path.write_text(json.dumps(routing))
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("provider")),
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("sqlite3.connect", side_effect=AssertionError("database")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("subprocess.run", side_effect=AssertionError("subprocess")),
    ):
        config = load(path)
        report = config.inspect()
        payload = cloud_answer_request(
            config, {"schema": "local-answer/v1", "answer": "Synthetic answer."}
        )
        assert payload["model"] == routing["cloud_answer"]["model"]
        assert local_failure(config, "unavailable")["cloud_requests"] == []
    assert report["state"] == "prepared"
    assert report["activation_allowed"] is report["runtime_verified"] is False
    assert report["credentials_read"] is False and report["model_calls"] == 0
    assert "secret" not in json.dumps(report) and "127.0.0.1" not in json.dumps(report)
    assert not Path(routing["cloud_answer"]["secret_file"]).exists()


@pytest.mark.parametrize(
    "field,value", [("enabled", True), ("enabled", 0), ("schema", "old"), ("fallback", True)]
)
def test_unknown_fields_or_activation_are_rejected(routing, field, value):
    routing[field] = value
    with pytest.raises(InvalidInput):
        parse(routing)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:8000/v1/chat/completions",
        "http://host.docker.internal:8000/v1/chat/completions",
        "https://example.com/v1/chat/completions",
        "http://0.0.0.0:8000/v1/chat/completions",
        "http://127.0.0.1:65536/v1/chat/completions",
        "http://127.0.0.1:0/v1/chat/completions",
        "http://user:secret@127.0.0.1:8000/v1/chat/completions",
        "http://127.0.0.1:8000/v1/chat/completions?key=secret",
        "http://127.0.0.1:8000/v1/chat/completions#secret",
    ],
)
def test_local_endpoint_cannot_expand_to_remote_or_secret_arguments(routing, endpoint):
    routing["local_query"]["endpoint"] = endpoint
    with pytest.raises(InvalidInput):
        parse(routing)


def test_literal_ipv6_loopback_and_keyless_declaration(routing):
    routing["local_query"]["endpoint"] = "http://[::1]:8000/v1/chat/completions"
    routing["local_query"]["secret_file"] = None
    assert parse(routing).local_query.secret_file is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("image", "executor:latest"),
        ("image", True),
        ("model", "../bad model"),
        ("secret_file", "relative"),
        ("secret_file", 123),
        ("secret_file", "/bad\x00key"),
        ("max_input_tokens", True),
        ("max_output_tokens", 8193),
        ("max_request_bytes", 0),
        ("max_calls", 3),
        ("daily_calls", 0),
        ("timeout_seconds", 601),
        ("timeout_seconds", 2.0),
        ("extra", "secret"),
    ],
)
def test_local_fields_and_limits_are_explicit(routing, field, value):
    routing["local_query"][field] = value
    with pytest.raises(InvalidInput):
        parse(routing)


@pytest.mark.parametrize(
    "field,value",
    [
        ("endpoint", "http://127.0.0.1:8000/v1/chat/completions"),
        ("input_nusd", None),
        ("max_attempts", 2),
        ("max_calls", 3),
        ("task_limit_nusd", 100),
        ("daily_limit_nusd", 999999),
        ("secret_file", "relative"),
        ("api_key", "private-sentinel"),
    ],
)
def test_cloud_reuses_provider_contract_and_requires_funded_single_attempt(routing, field, value):
    routing["cloud_answer"][field] = value
    with pytest.raises(InvalidInput):
        parse(routing)


def test_backup_list_is_explicit_unique_and_never_auto_selected(routing):
    backup = copy.deepcopy(routing["cloud_answer"])
    backup["model"] = "synthetic-approved-backup"
    routing["approved_backups"] = [backup]
    config = parse(routing)
    assert config.inspect()["declared_backup_count"] == 1
    assert (
        cloud_answer_request(config, {"schema": "local-answer/v1", "answer": "answer"})["model"]
        == "glm-5.3-flash"
    )
    for category in ["unavailable", "timeout", "cancelled", "rejected"]:
        stopped = local_failure(config, category)
        assert stopped["state"] == "stopped" and stopped["cloud_requests"] == []
        assert stopped["automatic_switch"] is False


@pytest.mark.parametrize(
    "kind", ["duplicate", "primary_duplicate", "too_many", "no_primary", "not_list"]
)
def test_invalid_backup_lists(routing, kind):
    backup = copy.deepcopy(routing["cloud_answer"])
    backup["model"] = "synthetic-backup"
    routing["approved_backups"] = [backup]
    if kind == "duplicate":
        routing["approved_backups"].append(backup)
    elif kind == "primary_duplicate":
        routing["approved_backups"] = [routing["cloud_answer"]]
    elif kind == "too_many":
        routing["approved_backups"] *= 5
    elif kind == "no_primary":
        routing["cloud_answer"] = None
    else:
        routing["approved_backups"] = {}
    with pytest.raises(InvalidInput):
        parse(routing)


@pytest.mark.parametrize(
    "field",
    [
        "sources",
        "quotations",
        "evidence",
        "raw",
        "messages",
        "tools",
        "question",
        "endpoint",
        "api_key",
    ],
)
def test_retrieval_and_injected_transport_fields_do_not_enter_cloud_payload(routing, field):
    value = {
        "schema": "local-answer/v1",
        "answer": "derived synthetic answer",
        field: "RAW_SENTINEL_NOT_AN_ANSWER",
    }
    with pytest.raises(InvalidInput):
        cloud_answer_request(parse(routing), value)


def test_answer_projection_preserves_allowed_excerpts_without_original_package(routing):
    evidence = {"raw": "RAW_SENTINEL_NOT_AN_ANSWER", "path": "PRIVATE_PATH_SENTINEL"}
    config = parse(routing)
    answer = "Unverified answer: synthetic excerpt 42."
    payload = cloud_answer_request(config, {"schema": "local-answer/v1", "answer": answer})
    text = json.dumps(payload)
    assert all(marker not in text for marker in evidence.values())
    assert json.loads(payload["messages"][1]["content"]) == {"answer": answer}
    assert set(payload) == {"model", "messages", "max_tokens", "stream"}
    assert "tools" not in payload and payload["stream"] is False


@pytest.mark.parametrize(
    "value",
    [
        None,
        {},
        {"schema": "local-answer/v1", "answer": ""},
        {"schema": "local-answer/v1", "answer": 1},
        {"schema": "other", "answer": "text"},
        {"schema": "local-answer/v1", "answer": "字" * 6000},
    ],
)
def test_invalid_or_oversized_answer_is_rejected(routing, value):
    with pytest.raises(InvalidInput):
        cloud_answer_request(parse(routing), value)


def test_cloud_request_byte_limit_still_applies(routing):
    routing["cloud_answer"]["max_request_bytes"] = 100
    with pytest.raises(InvalidInput, match="request too large"):
        cloud_answer_request(parse(routing), {"schema": "local-answer/v1", "answer": "answer"})


def test_digest_binds_all_roles_limits_and_approved_list(routing):
    original = parse(routing).digest
    routing["local_query"]["timeout_seconds"] = 61
    assert parse(routing).digest != original
    routing["approved_backups"] = [dict(routing["cloud_answer"], model="synthetic-backup")]
    assert parse(routing).digest != original


def test_invalid_unicode_has_bounded_errors(routing):
    config = parse(routing)
    with pytest.raises(InvalidInput, match="cloud_answer_invalid"):
        cloud_answer_request(config, {"schema": "local-answer/v1", "answer": "\ud800"})
    routing["cloud_answer"]["price_revision"] = "\ud800"
    with pytest.raises(InvalidInput, match="routing_unicode_invalid"):
        parse(routing)


def test_default_example_is_incomplete_and_cannot_prepare_cloud_answer():
    root = Path(__file__).resolve().parents[1]
    config = load(root / "configs/query-routing.example.json")
    assert config.inspect()["state"] == "incomplete"
    with pytest.raises(InvalidInput):
        cloud_answer_request(config, {"schema": "local-answer/v1", "answer": "answer"})


@pytest.mark.parametrize(
    "kind",
    [
        "symlink",
        "parent_symlink",
        "hardlink",
        "fifo",
        "oversized",
        "duplicate",
        "nested_duplicate",
        "array",
        "bad_json",
    ],
)
def test_policy_file_rejects_unsafe_or_ambiguous_input(routing, tmp_path, kind):
    original = tmp_path / "original.json"
    original.write_text(json.dumps(routing))
    path = tmp_path / "candidate.json"
    if kind == "symlink":
        path.symlink_to(original)
    elif kind == "parent_symlink":
        directory = tmp_path / "link"
        directory.symlink_to(tmp_path, target_is_directory=True)
        path = directory / "original.json"
    elif kind == "hardlink":
        os.link(original, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "oversized":
        path.write_bytes(b" " * 65537)
    elif kind == "duplicate":
        path.write_text('{"enabled":false,"enabled":true}')
    elif kind == "nested_duplicate":
        path.write_text('{"local_query":{"model":"one","model":"two"}}')
    elif kind == "array":
        path.write_text("[]")
    else:
        path.write_text("{private-sentinel")
    with pytest.raises((InvalidInput, OSError)):
        load(path)


def test_cli_real_process_reports_only_redacted_diagnostics(routing, tmp_path):
    path = tmp_path / "private-policy-name.json"
    path.write_text(json.dumps(routing))
    result = subprocess.run(
        [sys.executable, "-m", "fiber_bundle.query_routing_cli", "--policy-file", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0 and json.loads(result.stdout)["activation_allowed"] is False
    assert str(path) not in result.stdout and "secret" not in result.stdout
    routing["api_key"] = "PRIVATE_SENTINEL"
    path.write_text(json.dumps(routing))
    result = subprocess.run(
        [sys.executable, "-m", "fiber_bundle.query_routing_cli", "--policy-file", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert json.loads(result.stdout) == {"state": "rejected", "reason": "routing_preflight_failed"}
    assert "PRIVATE_SENTINEL" not in result.stdout + result.stderr
