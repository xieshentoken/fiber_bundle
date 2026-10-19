"""Finite model errors and explicit replacement attempts preserve all prior accounting."""

import sys

import pytest

from fiber_bundle import database, execution, execution_ledger, model_gateway
from fiber_bundle.channels.intake_pi import prepare_retry
from fiber_bundle.config import InvalidInput
from fiber_bundle.model_configuration.errors import ModelError

pytest_plugins = ["test_execution"]


@pytest.mark.parametrize(
    "code",
    [
        "model_authentication_failed",
        "model_timeout",
        "model_transport_failure",
        "model_response_invalid",
        "model_keychain_unavailable",
    ],
)
def test_model_error_reaches_run_and_retry_keeps_unknown_call(
    config, policy, bound, monkeypatch, code
):
    script = (
        "import sys,json;json.loads(sys.stdin.readline());"
        'print(json.dumps({"type":"model","id":"one","body":{"messages":[{"role":"user","content":"synthetic"}]}}),flush=True);'
        "sys.stdin.readline()"
    )
    monkeypatch.setattr(execution, "command", lambda *a, **kw: [sys.executable, "-u", "-c", script])
    monkeypatch.setattr(execution, "stop", lambda *a, **kw: None)

    def fail(*args):
        raise ModelError(code)

    monkeypatch.setattr(model_gateway, "complete", fail)
    result = execution.execute(config, policy, bound[0], "synthetic bounded test")
    assert result["state"] == "failed"
    with database.connect(config.state_root) as db:
        assert execution_ledger.get_run(db, bound[0])["error_code"] == code
        before = [tuple(r) for r in db.execute("select * from model_calls")]
        assert len(before) == 1
        link = {"pi_processing": {"runs": {"0": bound[0]}, "state": "failed"}}
        with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
            prepare_retry(db, link)
        assert link["pi_processing"]["runs"] == {"0": bound[0]}
        prepare_retry(db, link, approve_new_attempt=True)
        assert link["pi_processing"]["runs"] == {}
        assert link["pi_processing"]["previous_runs"][0]["run_id"] == bound[0]
        assert [tuple(r) for r in db.execute("select * from model_calls")] == before
        assert db.execute("select state from model_calls").fetchone()[0] == "uncertain"
        assert execution_ledger.get_run(db, bound[0])["state"] == "failed"


def test_processing_retry_cannot_replace_a_running_call(config, policy, bound):
    with database.connect(config.state_root) as db:
        execution_ledger.start(config, db, bound[0], policy)
        link = {"pi_processing": {"runs": {"0": bound[0]}, "state": "processing"}}
        with pytest.raises(InvalidInput, match="requires_reconciliation"):
            prepare_retry(db, link, approve_new_attempt=True)
        assert link["pi_processing"]["runs"]["0"] == bound[0]


def test_retry_retains_completed_chunk_and_rejects_nonboolean_approval(config, policy, bound):
    from pydantic import ValidationError

    from fiber_bundle.channels.intake_contract import Retry

    with pytest.raises(ValidationError):
        Retry(expected_revision=1, approve_new_model_attempt="yes")
    with database.connect(config.state_root) as db:
        execution_ledger.transition(db, bound[0], "succeeded")
        link = {"pi_processing": {"runs": {"0": bound[0]}, "state": "failed"}}
        prepare_retry(db, link, approve_new_attempt=True)
        assert link["pi_processing"]["runs"] == {"0": bound[0]}
        assert "previous_runs" not in link["pi_processing"]


@pytest.mark.parametrize("processing", [False, True])
def test_multiround_processing_has_finite_total_budget(
    config, policy, bound, monkeypatch, processing
):
    from dataclasses import replace

    from fiber_bundle import model_budgets, pi_skills
    from fiber_bundle.inputs import canonical

    selected = replace(policy, timeout_seconds=1)
    with database.connect(config.state_root) as db:
        version = execution_ledger.get_run(db, bound[0])["version_id"]
        run = execution_ledger.create_run(config, db, selected, bound[1], bound[2], version)
        if processing:
            model_budgets.save(
                db,
                model_budgets.Save(
                    secret_file=selected.secret_file,
                    expected_revision=0,
                    settings=model_budgets.Policy(label="Synthetic", limit=1000000),
                ),
            )
            db.execute(
                "insert into pi_processing_runs values(?,?,?,?,?)",
                (
                    run,
                    "article",
                    "a" * 64,
                    pi_skills.selected("article")["sha256"],
                    canonical(
                        {
                            "schema": "fiber-processing-input/v1",
                            "kind": "article",
                            "source_sha256": "a" * 64,
                            "evidence": [],
                        }
                    ),
                ),
            )
    script = (
        "import sys,json,time;json.loads(sys.stdin.readline());time.sleep(1.2);"
        'print(json.dumps({"type":"model","id":"one","body":{"messages":[{"role":"user","content":"synthetic"}]}}),flush=True);'
        'sys.stdin.readline();print(json.dumps({"type":"event","event":{"type":"agent_end","messages":[{"role":"assistant","stopReason":"stop"}]}}),flush=True)'
    )
    monkeypatch.setattr(execution, "command", lambda *a, **k: [sys.executable, "-u", "-c", script])
    monkeypatch.setattr(execution, "stop", lambda *a, **k: None)
    monkeypatch.setattr(
        model_gateway,
        "complete",
        lambda *a: {
            "choices": [
                {"message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        },
    )
    result = execution.execute(config, selected, run, "synthetic")
    assert result["state"] == ("succeeded" if processing else "failed")
    with database.connect(config.state_root) as db:
        if not processing:
            assert execution_ledger.get_run(db, run)["error_code"] == "execution_failed"


def test_diagnostic_metadata_does_not_include_content_or_paths():
    from fiber_bundle import execution_diagnostics as safe

    value = safe.event(
        {
            "type": "tool_execution_end",
            "toolName": "write",
            "args": {"path": "/private/SECRET"},
            "isError": True,
            "result": {"content": "SECRET EACCES"},
        }
    )
    assert "SECRET" not in str(value) and value["io_errors"] == ["EACCES"]
    value = safe.model(
        {
            "body": {
                "choices": [{"message": {"content": "SECRET source data"}, "finish_reason": "stop"}]
            }
        }
    )
    assert "SECRET" not in str(value) and value["content_chars"] == 18


def test_write_diagnostic_classifies_arguments_without_recording_the_draft():
    from fiber_bundle import execution_diagnostics as safe

    start = safe.event(
        {
            "type": "tool_execution_start",
            "toolName": "write",
            "args": {"path": "/task/draft.json", "content": {"summary": "SECRET"}},
        }
    )
    assert start["target"] == "draft" and start["content_type"] == "object"
    end = safe.event(
        {
            "type": "tool_execution_end",
            "toolName": "write",
            "isError": True,
            "result": {"content": "Validation failed: SECRET content must be string"},
        }
    )
    assert end["error_kind"] == "arguments" and "SECRET" not in str(start) + str(end)


@pytest.mark.parametrize(
    "path,source,expected",
    [
        ("/task/draft.json", "a" * 64, 1),
        ("/task/input.json", "a" * 64, 0),
        ("/task/draft.json", "b" * 64, 0),
    ],
)
def test_only_typed_bound_draft_objects_are_serialized_for_the_string_tool(path, source, expected):
    import json

    from fiber_bundle import pi_processing

    draft = {
        "schema": "fiber-processing-draft/v1",
        "kind": "article",
        "source_sha256": source,
        "summary": "合成",
        "evidence_ids": ["capture:0"],
        "gaps": [],
    }
    body = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "write",
                                "arguments": json.dumps({"path": path, "content": draft}),
                            }
                        }
                    ]
                }
            }
        ]
    }
    assert (
        pi_processing.normalize_draft_write({"kind": "article", "source_sha256": "a" * 64}, body)
        == expected
    )
    content = json.loads(body["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"])[
        "content"
    ]
    assert (json.loads(content) if expected else content) == draft
    assert (
        pi_processing.normalize_draft_write({"kind": "article", "source_sha256": "a" * 64}, body)
        == 0
    )


@pytest.mark.parametrize("reasoning", [False, True])
def test_real_pi_write_tool_accepts_normalized_object_without_provider(
    config, policy, bound, monkeypatch, reasoning
):
    import json
    import os
    from dataclasses import replace

    from fiber_bundle import model_budgets, pi_processing

    image = os.environ.get("FIBER_PI_ACCEPTANCE_IMAGE")
    if not image:
        pytest.skip("fixed local Pi image requires explicit opt-in")
    selected = replace(policy, image=image)
    if reasoning:
        from dataclasses import asdict

        from fiber_bundle.model_configuration.policy import RegisteredPolicy

        original = RegisteredPolicy(
            **{
                **asdict(selected),
                "model": "glm-5.3-flash",
                "max_output_tokens": 4096,
                "max_attempts": 1,
                "price_revision": "unknown",
                "input_nusd": 0,
                "output_nusd": 0,
            },
            provider="opencodego",
        )
        selected = pi_processing.execution_policy(original)
        assert selected.max_output_tokens == 16384
        assert original.max_output_tokens == 4096
    with database.connect(config.state_root) as db:
        model_budgets.save(
            db,
            model_budgets.Save(
                secret_file=selected.secret_file,
                expected_revision=0,
                settings=model_budgets.Policy(label="Synthetic", limit=1000000),
            ),
        )
        version = execution_ledger.get_run(db, bound[0])["version_id"]
        run = pi_processing.prepare(
            config,
            db,
            selected,
            bound[1],
            bound[2],
            version,
            "article",
            "a" * 64,
            [{"id": "capture:0", "text": "合成原文"}],
            allow_cloud=True,
        )
    calls = []

    def provider(policy, rid, request):
        calls.append(request)
        if len(calls) == 1:
            draft = {
                "schema": "fiber-processing-draft/v1",
                "kind": "article",
                "source_sha256": "a" * 64,
                "summary": "合成原文的分析",
                "evidence_ids": ["capture:0"],
                "gaps": [],
            }
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "synthetic-write",
                        "type": "function",
                        "function": {
                            "name": "write",
                            "arguments": json.dumps({"path": "/task/draft.json", "content": draft}),
                        },
                    }
                ],
            }
            reason = "tool_calls"
        else:
            assert "Successfully wrote" in str(request["messages"][-1])
            message = {"role": "assistant", "content": "done"}
            reason = "stop"
        return {
            "id": "synthetic",
            "object": "chat.completion",
            "created": 1,
            "model": policy.model,
            "choices": [{"index": 0, "message": message, "finish_reason": reason}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5000 if reasoning else 10},
        }

    monkeypatch.setattr(model_gateway, "complete", provider)
    result = execution.execute(config, selected, run, "fixed processing")
    assert result["state"] == "succeeded", result
    with database.connect(config.state_root) as db:
        assert pi_processing.result(config, db, run)["summary"] == "合成原文的分析"
    assert len(calls) == 2


def test_waiting_budget_with_calls_requires_new_attempt_and_keeps_the_old_run(
    config, policy, bound
):
    with database.connect(config.state_root) as db:
        execution_ledger.start(config, db, bound[0], policy)
        execution_ledger.reserve(config, db, bound[0], policy)
        execution_ledger.transition(db, bound[0], "waiting_budget", "budget_exhausted")
        old = [tuple(r) for r in db.execute("select * from model_calls")]
        frozen = {"pi_processing": {"runs": {"2": bound[0]}, "state": "waiting_budget"}}
        with pytest.raises(InvalidInput, match="new_attempt_confirmation_required"):
            prepare_retry(db, frozen)
        prepare_retry(db, frozen, approve_new_attempt=True)
        assert frozen["pi_processing"]["runs"] == {}
        assert frozen["pi_processing"]["previous_runs"][0]["state"] == "waiting_budget"
        assert execution_ledger.get_run(db, bound[0])["state"] == "waiting_budget"
        assert [tuple(r) for r in db.execute("select * from model_calls")] == old


def test_processing_prompt_exposes_only_bound_metadata_and_keeps_source_in_the_input_file():
    import json

    from fiber_bundle import pi_processing

    prompt = pi_processing.task_prompt(
        {
            "kind": "article",
            "source_sha256": "a" * 64,
            "input_json": json.dumps(
                {"evidence": [{"id": "capture:2", "text": "PRIVATE_SOURCE_SENTINEL"}]}
            ),
        }
    )
    assert "a" * 64 in prompt and "capture:2" in prompt
    assert "PRIVATE_SOURCE_SENTINEL" not in prompt


def test_processing_output_capacity_preserves_user_settings_and_other_models(policy):
    from dataclasses import asdict, replace

    from fiber_bundle import pi_processing
    from fiber_bundle.model_configuration.policy import RegisteredPolicy

    assert pi_processing.execution_policy(policy) is policy
    base = RegisteredPolicy(**{**asdict(policy), "model": "glm-5.3-flash"}, provider="opencodego")
    expanded = pi_processing.execution_policy(base)
    assert expanded.max_output_tokens == 16384
    assert replace(expanded, max_output_tokens=base.max_output_tokens) == base
    assert expanded.digest != base.digest
    assert pi_processing.execution_policy(expanded) is expanded
    other = replace(base, model="other")
    assert pi_processing.execution_policy(other) is other
