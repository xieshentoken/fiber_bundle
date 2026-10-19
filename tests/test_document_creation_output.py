import os
from dataclasses import FrozenInstanceError, replace
from unittest.mock import patch
from uuid import uuid4

import pytest
from test_document_creation_execution import active as active
from test_document_creation_execution import creation_policy as creation_policy
from test_document_creation_execution import prepared_task as prepared_task
from test_document_creation_tasks import input_task as input_task
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import auth
from fiber_bundle import document_creation_execution as execution
from fiber_bundle import document_creation_output as output
from fiber_bundle import document_creation_tasks as tasks
from fiber_bundle.config import IntegrityError, InvalidInput
from fiber_bundle.document_creation_contract import MAX_ARTIFACT_BYTES, CreationPlan
from fiber_bundle.inputs import canonical
from fiber_bundle.objects import digest


@pytest.fixture
def plan():
    source = {
        "client_id": "synthetic-client",
        "query_task_id": "synthetic-query",
        "binding_sha256": "0" * 64,
        "vault_ids": ["synthetic-vault"],
        "source_version_id": None,
        "sources": [{"citation": "source-1", "snippet": "100 psi evidence"}],
        "quotations": [{"citation": "source-1", "quote": "100 psi evidence"}],
    }
    return CreationPlan("synthetic-client", "synthetic-query", canonical(source), ("报告",), "md")


def write_output(root, plan, context, content=None):
    raw = (
        content
        if content is not None
        else (output.DRAFT_MARKER + "# Draft\n100 psi [source-1]\n").encode()
    )
    name = "draft." + plan.requested_format
    (root / name).write_bytes(raw)
    receipt = {
        "schema": output.SCHEMA,
        "context": context,
        "filename": name,
        "sha256": digest(raw),
        "byte_count": len(raw),
    }
    (root / "output.json").write_text(canonical(receipt))
    return receipt


@pytest.fixture
def candidate(tmp_path, plan):
    context = output.context(plan, str(uuid4()), "1" * 64, "2" * 64)
    receipt = write_output(tmp_path, plan, context)
    return tmp_path, plan, context, receipt


@pytest.mark.parametrize("fmt", ["md", "txt"])
def test_checked_utf8_draft_is_immutable_and_does_not_enable_delivery(candidate, fmt):
    root, plan, context, _ = candidate
    plan = replace(plan, requested_format=fmt)
    context = output.context(plan, context["run_id"], "1" * 64, "2" * 64)
    write_output(root, plan, context)
    with (
        patch("socket.create_connection", side_effect=AssertionError("network")),
        patch("subprocess.Popen", side_effect=AssertionError("process")),
    ):
        result = output.inspect(root, plan, context, upload_limit_bytes=MAX_ARTIFACT_BYTES)
    report = result.report()
    assert (
        report["format_verified"]
        is report["file_exists_verified"]
        is report["upload_size_checked"]
        is True
    )
    for flag in (
        "semantic_verified",
        "knowledge_verified",
        "runtime_verified",
        "uploadable_verified",
        "delivery_allowed",
        "save_allowed",
        "execution_allowed",
    ):
        assert report[flag] is False
    assert report["artifact"]["original_name"] == "draft." + fmt
    assert report["artifact"]["media_type"] == output.FORMATS[fmt]
    assert "# Draft" not in canonical(report) and "# Draft" not in repr(result)
    (root / ("draft." + fmt)).write_bytes(b"tampered after check")
    assert result.content.startswith(output.DRAFT_MARKER.encode())
    with pytest.raises(FrozenInstanceError):
        result.content = b"tampered"
    report["context"]["revision"] = 999
    assert result.report()["context"]["revision"] == 1


def test_unspecified_upload_limit_does_not_claim_upload_check(candidate):
    root, plan, context, _ = candidate
    assert output.inspect(root, plan, context).report()["upload_size_checked"] is False


@pytest.mark.parametrize(
    "key,value",
    [
        ("schema", "old"),
        ("filename", "../draft.md"),
        ("filename", "other.md"),
        ("sha256", "0" * 64),
        ("byte_count", 1),
        ("byte_count", True),
        ("byte_count", MAX_ARTIFACT_BYTES + 1),
        ("byte_count", 0),
        ("extra", True),
    ],
)
def test_bad_or_unbound_manifest_rejected(candidate, key, value):
    root, plan, context, receipt = candidate
    receipt[key] = value
    (root / "output.json").write_text(canonical(receipt))
    with pytest.raises(InvalidInput, match="document_output_invalid"):
        output.inspect(root, plan, context)


@pytest.mark.parametrize(
    "key",
    ["run_id", "revision", "plan_sha256", "input_sha256", "configuration_sha256", "request_sha256"],
)
def test_receipt_cannot_swap_task_plan_configuration_or_request(candidate, key):
    root, plan, context, receipt = candidate
    receipt["context"] = {
        **context,
        key: str(uuid4()) if key == "run_id" else (2 if key == "revision" else "9" * 64),
    }
    (root / "output.json").write_text(canonical(receipt))
    with pytest.raises(InvalidInput):
        output.inspect(root, plan, context)


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"ordinary unmarked text",
        output.DRAFT_MARKER.encode(),
        (output.DRAFT_MARKER + " \n\t").encode(),
        b"%PDF-1.7\x00",
        output.DRAFT_MARKER.encode() + b"\xff",
        (output.DRAFT_MARKER + "\x00test").encode(),
        (output.DRAFT_MARKER + "\x7ftest").encode(),
    ],
)
def test_empty_binary_unmarked_or_invalid_utf8_files_rejected(candidate, content):
    root, plan, context, _ = candidate
    write_output(root, plan, context, content)
    with pytest.raises(InvalidInput):
        output.inspect(root, plan, context)


@pytest.mark.parametrize(
    "kind", ["missing", "symlink", "hardlink", "directory", "fifo", "oversized"]
)
@pytest.mark.parametrize("filename", ["draft.md", "output.json"])
def test_untrusted_output_files_require_bounded_single_regular_snapshot(candidate, kind, filename):
    root, plan, context, _ = candidate
    target = root / filename
    raw = target.read_bytes()
    target.unlink()
    if kind == "symlink":
        (root / "other").write_bytes(raw)
        target.symlink_to(root / "other")
    elif kind == "hardlink":
        (root / "other").write_bytes(raw)
        os.link(root / "other", target)
    elif kind == "directory":
        target.mkdir()
    elif kind == "fifo":
        os.mkfifo(target)
    elif kind == "oversized":
        with target.open("wb") as stream:
            stream.truncate(MAX_ARTIFACT_BYTES + 1)
    with pytest.raises(InvalidInput, match="document_output_invalid"):
        output.inspect(root, plan, context)


def test_symlink_parent_and_relative_task_directory_refused(candidate):
    root, plan, context, _ = candidate
    link = root / "parent-link"
    link.symlink_to(root, target_is_directory=True)
    for path in (link, type(root)(".")):
        with pytest.raises(InvalidInput):
            output.inspect(path, plan, context)


@pytest.mark.parametrize(
    "value", ['{"schema":"x","schema":"y"}', "[]", "null", '{"n":NaN}', '{"n":Infinity}']
)
def test_non_object_duplicate_or_nonfinite_manifest_rejected(candidate, value):
    root, plan, context, _ = candidate
    (root / "output.json").write_text(value)
    with pytest.raises(InvalidInput):
        output.inspect(root, plan, context)


@pytest.mark.parametrize("limit", [0, -1, True, 1.5, MAX_ARTIFACT_BYTES + 1])
def test_upload_limit_requires_explicit_positive_bounded_integer(candidate, limit):
    root, plan, context, _ = candidate
    with pytest.raises(InvalidInput, match="upload_limit_invalid"):
        output.inspect(root, plan, context, upload_limit_bytes=limit)


def test_upload_limit_rejects_over_limit_and_accepts_exact_size(candidate):
    root, plan, context, receipt = candidate
    with pytest.raises(InvalidInput, match="upload_size_exceeded"):
        output.inspect(root, plan, context, upload_limit_bytes=receipt["byte_count"] - 1)
    assert output.inspect(root, plan, context, upload_limit_bytes=receipt["byte_count"]).content


@pytest.mark.parametrize("fmt", ["pdf", "docx", "pptx", "html"])
def test_unsupported_formats_are_not_faked_by_extensions(candidate, fmt):
    root, plan, context, _ = candidate
    with pytest.raises(InvalidInput, match="format_unqualified"):
        output.inspect(root, replace(plan, requested_format=fmt), context)


@pytest.mark.parametrize("mutation", ["size", "bool", "whitespace"])
def test_changed_receipt_during_snapshot_rejected(candidate, monkeypatch, mutation):
    root, plan, context, receipt = candidate
    original = output.read

    def changing(path, limit):
        raw = original(path, limit)
        if path.name == "draft.md":
            if mutation == "size":
                receipt["byte_count"] = 1
            elif mutation == "bool":
                receipt["context"]["revision"] = True
            (root / "output.json").write_text(
                canonical(receipt) + (" " if mutation == "whitespace" else "")
            )
        return raw

    monkeypatch.setattr(output, "read", changing)
    with pytest.raises(InvalidInput):
        output.inspect(root, plan, context)


@pytest.mark.parametrize("key", ["run_id", "configuration_sha256", "request_sha256"])
def test_invalid_expected_context_is_not_a_binding(candidate, key):
    root, plan, context, _ = candidate
    context = {**context, key: "invalid"}
    with pytest.raises(InvalidInput):
        output.inspect(root, plan, context)


@pytest.mark.parametrize("change", ["source", "grant"])
def test_authority_is_checked_again_after_reading_output(
    config, owner, active, tmp_path, monkeypatch, change
):
    db, policy, guard, attempt = active
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    context = output.context(
        plan,
        attempt.run_id,
        policy.sha256,
        execution.status(db, attempt.client_id, attempt.task_id)["request_sha256"],
    )
    write_output(tmp_path, plan, context)
    execution.record_usage(db, attempt, {"prompt_tokens": 10, "completion_tokens": 20})
    original = execution.inspect_file_output

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == "source":
            (config.project_root / "test vault/temp_vault1/new.md").write_text("pressure 90 psi")
        else:
            auth.set_grant(
                db, owner[0], owner[2]["personal"], query=False, original=False, add=True
            )
        return result

    monkeypatch.setattr(execution, "inspect_file_output", changing)
    with pytest.raises((auth.Denied, IntegrityError)):
        execution.inspect_output(config, db, attempt, policy, guard, tmp_path)


def test_execution_candidate_requires_known_fee_and_current_owner_without_completing_run(
    config, active, tmp_path
):
    db, policy, guard, attempt = active
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    request_sha = execution.status(db, attempt.client_id, attempt.task_id)["request_sha256"]
    context = output.context(plan, attempt.run_id, policy.sha256, request_sha)
    write_output(tmp_path, plan, context)
    with pytest.raises(InvalidInput, match="usage_unsettled"):
        execution.inspect_output(config, db, attempt, policy, guard, tmp_path)
    execution.record_usage(db, attempt, {"prompt_tokens": 10, "completion_tokens": 20})
    before = list(db.iterdump())
    result = execution.inspect_output(config, db, attempt, policy, guard, tmp_path)
    assert result.report()["context"] == context
    assert list(db.iterdump()) == before
    assert execution.status(db, attempt.client_id, attempt.task_id)["state"] == "running"


@pytest.mark.parametrize(
    "mutation", ["cancel", "grant", "source", "client", "owner", "policy", "overrun"]
)
def test_output_cannot_bypass_late_cancellation_or_changed_authority(
    config, owner, active, tmp_path, mutation
):
    db, policy, guard, attempt = active
    plan = tasks.plan(config, db, attempt.client_id, attempt.task_id)
    context = output.context(
        plan,
        attempt.run_id,
        policy.sha256,
        execution.status(db, attempt.client_id, attempt.task_id)["request_sha256"],
    )
    write_output(tmp_path, plan, context)
    execution.record_usage(
        db,
        attempt,
        {
            "prompt_tokens": policy.policy().max_input_tokens + 1 if mutation == "overrun" else 10,
            "completion_tokens": 20,
        },
    )
    if mutation == "cancel":
        execution.cancel(db, attempt.client_id, attempt.task_id)
    elif mutation == "grant":
        auth.set_grant(db, owner[0], owner[2]["personal"], query=False, original=False, add=True)
    elif mutation == "client":
        auth.revoke_client(db, owner[0])
    elif mutation == "source":
        (config.project_root / "test vault/temp_vault1/new.md").write_text(
            "changed pressure 90 psi"
        )
    elif mutation == "owner":
        guard = execution.Responsibility(attempt.client_id, attempt.task_id)
    elif mutation == "policy":
        policy = replace(policy, capability_revision="changed")
    before = list(db.iterdump())
    with pytest.raises((auth.Denied, InvalidInput, IntegrityError)):
        execution.inspect_output(config, db, attempt, policy, guard, tmp_path)
    assert list(db.iterdump()) == before
