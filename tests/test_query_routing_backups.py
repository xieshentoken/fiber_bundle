import copy
import hashlib
import json
from unittest.mock import patch

import pytest
from test_query_routing_preparation import routing as routing
from test_query_routing_preview import cli, write_answer

from fiber_bundle.config import InvalidInput
from fiber_bundle.query_routing_boundary import cloud_answer_request, local_failure
from fiber_bundle.query_routing_config import parse
from fiber_bundle.query_routing_preview import preview


@pytest.fixture
def declared(routing, tmp_path):
    for index in range(1, 5):
        item = copy.deepcopy(routing["cloud_answer"])
        item.update(
            model=f"synthetic-declared-backup-{index}",
            secret_file=str(tmp_path / f"unread-backup-secret-{index}"),
            input_nusd=10 * index,
            output_nusd=20 * index,
            max_output_tokens=256 * index,
        )
        routing["approved_backups"].append(item)
    return routing


@pytest.mark.parametrize("index", [1, 2, 3, 4])
def test_explicit_index_uses_only_that_declared_policy(declared, tmp_path, index):
    config = parse(declared)
    path = write_answer(tmp_path)
    original = config.digest
    policy = config.approved_backups[index - 1]
    payload = cloud_answer_request(config, json.loads(path.read_bytes()), backup_index=index)
    assert payload["model"] == policy.model
    assert payload["max_tokens"] == policy.max_output_tokens
    assert config.answer_policy(index) is policy
    with (
        patch("fiber_bundle.model_gateway.complete", side_effect=AssertionError("model")),
        patch("fiber_bundle.execution_config.read_secret", side_effect=AssertionError("secret")),
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("sqlite3.connect", side_effect=AssertionError("ledger")),
        patch("subprocess.run", side_effect=AssertionError("worker")),
    ):
        report = preview(config, path, backup_index=index)
    summary = report["answer_preview"]
    target = summary["target"]
    assert target["backup_index"] == index and target["role"] == "declared_backup"
    assert target["configuration_sha256"] == policy.digest
    assert target["declared_reserve_nusd"] == policy.reserve_nusd
    assert target["declared_task_limit_nusd"] == policy.task_limit_nusd
    assert target["declared_daily_limit_nusd"] == policy.daily_limit_nusd
    assert target["approval_verified"] is target["budget_reserved"] is False
    assert target["automatic_switch"] is False and summary["requests_sent"] == 0
    wire = json.dumps(payload).encode()
    assert summary["request_bytes"] == len(wire)
    assert summary["request_sha256"] == hashlib.sha256(wire).hexdigest()
    assert report["configuration_sha256"] == config.digest == original
    assert report["activation_allowed"] is report["runtime_verified"] is False
    assert report["credentials_read"] is False and report["model_calls"] == 0
    output = json.dumps(report)
    for item in declared["approved_backups"]:
        assert item["model"] not in output and item["secret_file"] not in output
    assert "PRIVATE ANSWER" not in output and "opencode.ai" not in output


def test_default_stays_primary_after_backup_preview(declared, tmp_path):
    config = parse(declared)
    path = write_answer(tmp_path)
    first = preview(config, path)
    preview(config, path, backup_index=2)
    assert preview(config, path) == first
    assert "target" not in first["answer_preview"]
    assert config.answer_policy() is config.cloud_answer
    payload = cloud_answer_request(config, json.loads(path.read_bytes()))
    assert payload["model"] == config.cloud_answer.model


@pytest.mark.parametrize("index", [True, False, 0, -1, 5, 1.0, "1", [], {}])
def test_invalid_or_undeclared_index_never_reads_answer(declared, tmp_path, index):
    config = parse(declared)
    with patch("fiber_bundle.query_routing_preview.read", side_effect=AssertionError("file")):
        with pytest.raises(InvalidInput, match="cloud_backup_index_invalid"):
            preview(config, tmp_path / "unread-answer.json", backup_index=index)
    with pytest.raises(InvalidInput, match="cloud_backup_index_invalid"):
        cloud_answer_request(
            config, {"schema": "local-answer/v1", "answer": "text"}, backup_index=index
        )


def test_empty_backup_list_rejects_explicit_index(routing, tmp_path):
    with pytest.raises(InvalidInput, match="cloud_backup_index_invalid"):
        preview(parse(routing), tmp_path / "unread-answer.json", backup_index=1)


def test_selected_request_limit_is_not_primary_or_another_backup_limit(declared, tmp_path):
    declared["approved_backups"][0]["max_request_bytes"] = 128
    config = parse(declared)
    path = write_answer(tmp_path)
    assert preview(config, path)["answer_preview"]["state"] == "prepared"
    assert preview(config, path, backup_index=2)["answer_preview"]["state"] == "prepared"
    with pytest.raises(InvalidInput):
        preview(config, path, backup_index=1)


def test_failed_primary_does_not_try_backup(declared, tmp_path):
    declared["cloud_answer"]["max_request_bytes"] = 128
    config = parse(declared)
    path = write_answer(tmp_path)
    with pytest.raises(InvalidInput):
        preview(config, path)
    assert preview(config, path, backup_index=1)["answer_preview"]["state"] == "prepared"
    assert config.answer_policy() is config.cloud_answer


def test_wire_unicode_bound_uses_selected_backup(declared, tmp_path):
    config = parse(declared)
    path = write_answer(tmp_path, "中" * 150)
    payload = cloud_answer_request(config, json.loads(path.read_bytes()), backup_index=2)
    declared["approved_backups"][1]["max_request_bytes"] = (
        len(json.dumps(payload, ensure_ascii=False).encode()) + 1
    )
    config = parse(declared)
    assert len(json.dumps(payload).encode()) > config.answer_policy(2).max_request_bytes
    with pytest.raises(InvalidInput, match="answer_preview_request_too_large"):
        preview(config, path, backup_index=2)


@pytest.mark.parametrize(
    "field,value",
    [("task_limit_nusd", 1), ("daily_limit_nusd", 1), ("max_calls", 3), ("max_attempts", 2)],
)
def test_invalid_backup_budget_or_retry_declaration_is_rejected(declared, field, value):
    declared["approved_backups"][0][field] = value
    with pytest.raises(InvalidInput):
        parse(declared)


def test_reordering_and_policy_edits_change_binding(declared, tmp_path):
    config = parse(declared)
    path = write_answer(tmp_path)
    first = preview(config, path, backup_index=1)
    declared["approved_backups"].reverse()
    second = preview(parse(declared), path, backup_index=1)
    assert first["configuration_sha256"] != second["configuration_sha256"]
    assert first["answer_preview"]["request_sha256"] != second["answer_preview"]["request_sha256"]
    assert (
        first["answer_preview"]["target"]["configuration_sha256"]
        != second["answer_preview"]["target"]["configuration_sha256"]
    )
    declared["approved_backups"][0]["task_limit_nusd"] += 1
    third = preview(parse(declared), path, backup_index=1)
    assert second["answer_preview"]["request_sha256"] == third["answer_preview"]["request_sha256"]
    assert (
        second["answer_preview"]["target"]["configuration_sha256"]
        != third["answer_preview"]["target"]["configuration_sha256"]
    )


@pytest.mark.parametrize("category", ["unavailable", "timeout", "cancelled", "rejected"])
def test_local_failure_still_stops_after_explicit_preview(declared, tmp_path, category):
    config = parse(declared)
    preview(config, write_answer(tmp_path), backup_index=1)
    report = local_failure(config, category)
    assert report["state"] == "stopped" and report["cloud_requests"] == []
    assert report["automatic_switch"] is False


def test_cli_explicit_backup_redacted_and_no_persistent_change(declared, tmp_path):
    path = write_answer(tmp_path)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(declared))
    before = policy.read_bytes()
    result = cli(policy, "--preview-answer-file", str(path), "--preview-backup", "3")
    assert result.returncode == 0 and not result.stderr
    assert json.loads(result.stdout) == preview(parse(declared), path, backup_index=3)
    assert "synthetic-declared" not in result.stdout and str(tmp_path) not in result.stdout
    assert policy.read_bytes() == before


@pytest.mark.parametrize(
    "args",
    [
        ["--preview-backup", "1"],
        ["--probe-local", "--preview-backup", "1"],
        ["--preview-answer-file", "PRIVATE_PATH", "--preview-backup", "PRIVATE_KEY"],
        ["--preview-answer-file", "PRIVATE_PATH", "--preview-backup", "0"],
    ],
)
def test_cli_invalid_selection_fixed_error_before_any_file_access(tmp_path, args):
    result = cli(tmp_path / "PRIVATE_POLICY", *args)
    assert result.returncode == 2 and not result.stdout
    for sentinel in ["PRIVATE_PATH", "PRIVATE_KEY", "PRIVATE_POLICY"]:
        assert sentinel not in result.stderr


def test_cli_missing_declared_index_is_redacted_rejection(routing, tmp_path):
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps(routing))
    result = cli(policy, "--preview-answer-file", "PRIVATE_PATH", "--preview-backup", "1")
    assert result.returncode == 1 and not result.stderr
    assert json.loads(result.stdout) == {"state": "rejected", "reason": "routing_preflight_failed"}
    assert "PRIVATE_PATH" not in result.stdout
