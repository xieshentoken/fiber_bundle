import io
import json
import subprocess
import sys
import threading
from dataclasses import asdict
from unittest.mock import patch

import pytest
from test_cloud_answer_tasks import cloud_task as cloud_task
from test_cloud_answer_tasks import mutate, result
from test_local_query_tasks import local_task as local_task
from test_local_query_tasks import route as route
from test_query_routing_preparation import routing as routing
from test_session_search import owner as owner

from fiber_bundle import cloud_answer_execution as execution
from fiber_bundle import cloud_answer_gateway as gateway
from fiber_bundle import cloud_answer_tasks as tasks
from fiber_bundle import database
from fiber_bundle.config import InvalidInput
from fiber_bundle.local_query_process import run as credential_run
from fiber_bundle.model_gateway import ModelFailure


def test_default_off_performs_no_io():
    with patch("sqlite3.connect", side_effect=AssertionError("IO")):
        with pytest.raises(InvalidInput, match="not_enabled"):
            execution.execute(None, None, "who", "run")
        with pytest.raises(InvalidInput, match="not_enabled"):
            gateway.complete(None, "run", {})


def test_executor_reserves_before_child_and_delivers_safe_fields(config, route, owner, cloud_task):
    def provider(policy, run, answer, **kwargs):
        db = database.connect(config.state_root)
        assert tasks.status(db, owner[0], run)["cloud_call"]["state"] == "reserved"
        assert db.execute("SELECT state FROM execution_runs").fetchone()[0] == "running"
        db.close()
        assert kwargs["allow_inference"] is True and kwargs["should_stop"]() is False
        assert set(answer) == {"schema", "answer"} and "What pressure?" not in json.dumps(answer)
        return result()

    with patch.object(gateway, "complete", side_effect=provider) as call:
        assert (
            execution.execute(config, route, owner[0], cloud_task, allow_inference=True)["state"]
            == "succeeded"
        )
        with pytest.raises(InvalidInput, match="not_replayable"):
            execution.execute(config, route, owner[0], cloud_task, allow_inference=True)
        assert call.call_count == 1


@pytest.mark.parametrize(
    "error",
    [
        ModelFailure("cloud_timeout"),
        ModelFailure("cloud_transport_failed"),
        RuntimeError("SECRET ERROR BODY"),
        KeyboardInterrupt(),
    ],
)
def test_unknown_call_cannot_replay_and_diagnostics_are_content_free(
    config, route, owner, cloud_task, error
):
    with patch.object(gateway, "complete", side_effect=error):
        if isinstance(error, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                execution.execute(config, route, owner[0], cloud_task, allow_inference=True)
        else:
            report = execution.execute(config, route, owner[0], cloud_task, allow_inference=True)
            assert "SECRET" not in json.dumps(report)
    db = database.connect(config.state_root)
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "uncertain"
    with pytest.raises(InvalidInput, match="not_replayable"):
        tasks.claim(config, db, route, owner[0], cloud_task)
    db.close()


@pytest.mark.parametrize("change", ["cancel", "revoke", "evidence"])
def test_real_child_is_killed_and_reaped_when_ownership_changes(
    config, route, owner, cloud_task, change, monkeypatch, tmp_path
):
    started = threading.Event()
    processes = []
    popen = subprocess.Popen

    def child(command, **kwargs):
        # Exercise the real communicate/poll/kill/wait lifetime with a deliberately
        # slow credential child, without a provider or credential file.
        p = popen(
            [sys.executable, "-c", "import sys,time; sys.stdin.buffer.read(); time.sleep(30)"],
            **kwargs,
        )
        processes.append(p)
        started.set()
        return p

    monkeypatch.setattr(subprocess, "Popen", child)
    errors = []

    def change_scope():
        try:
            assert started.wait(5)
            if change == "cancel":
                execution.cancel(config, owner[0], cloud_task)
            else:
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        mutate(config, db, owner, change)
                    finally:
                        db.close()
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=change_scope)
    thread.start()
    report = execution.execute(config, route, owner[0], cloud_task, allow_inference=True)
    thread.join(5)
    assert not thread.is_alive() and not errors
    assert report["state"] == ("cancelled" if change == "cancel" else "failed")
    if change != "cancel":
        assert report["error_code"] == "cloud_authority_changed"
    assert len(processes) == 1 and processes[0].poll() is not None
    db = database.connect(config.state_root)
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "uncertain"
    assert db.execute("SELECT result_text FROM cloud_answer_tasks").fetchone()[0] is None
    db.close()


def test_recover_owned_inactive_attempt_preserves_unknown_without_replay(
    config, route, owner, cloud_task
):
    db = database.connect(config.state_root)
    tasks.claim(config, db, route, owner[0], cloud_task)
    db.close()
    assert execution.recover(config, owner[0], cloud_task)["state"] == "interrupted"
    db = database.connect(config.state_root)
    assert db.execute("SELECT state FROM model_calls").fetchone()[0] == "uncertain"
    db.close()


def test_active_run_lock_prevents_recovery(config, route, owner, cloud_task):
    from fiber_bundle.executor_container import run_lock

    with run_lock(config, cloud_task):
        with pytest.raises(InvalidInput, match="owned"):
            execution.recover(config, owner[0], cloud_task)


@pytest.mark.parametrize("malformed", [b"{}", b'{"x":1,"x":2}', b"NaN", b"x" * 266241])
def test_real_credential_child_refuses_malformed_input_before_key_read(malformed):
    outcome = subprocess.run(
        [sys.executable, "-m", "fiber_bundle.cloud_answer_gateway"],
        input=malformed,
        capture_output=True,
        timeout=5,
    )
    assert outcome.returncode == 0 and outcome.stderr == b""
    assert json.loads(outcome.stdout) == {"reason": "cloud_transport_failed"}


def test_child_reconstructs_only_bound_envelope_and_discards_provider_extras(route, cloud_task):
    value = {
        "policy": asdict(route.cloud_answer),
        "run_id": cloud_task,
        "answer": {"schema": "local-answer/v1", "answer": "Validated synthetic answer."},
    }

    class Input:
        buffer = io.BytesIO(json.dumps(value).encode())

    captured = {}

    def provider(policy, run_id, payload, *, require_message):
        assert require_message is False
        captured.update(payload)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "role": "assistant",
                        "content": "Edited answer.",
                        "reasoning_content": "PRIVATE DEBUG",
                    },
                }
            ],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "private": "SECRET"},
            "debug": "PRIVATE",
        }

    with (
        patch.object(sys, "stdin", Input()),
        patch.object(gateway.model_gateway, "complete", side_effect=provider),
        patch("builtins.print") as output,
    ):
        assert gateway._main() == 0
    safe = json.loads(output.call_args.args[0])
    assert safe == {
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
        "answer": "Edited answer.",
    }
    assert set(captured) == {"model", "messages", "max_tokens", "stream"}
    assert json.loads(captured["messages"][1]["content"]) == {
        "answer": "Validated synthetic answer."
    }


@pytest.mark.parametrize("change", ["finish", "role", "tools", "multiple", "unicode", "oversize"])
def test_rejected_content_retains_usage_for_host_accounting(change):
    body = {
        "choices": [
            {"finish_reason": "stop", "message": {"role": "assistant", "content": "Answer"}}
        ],
        "usage": {"prompt_tokens": 20, "completion_tokens": 30},
    }
    choice = body["choices"][0]
    if change == "finish":
        choice["finish_reason"] = "length"
    elif change == "role":
        choice["message"]["role"] = "tool"
    elif change == "tools":
        choice["message"]["tool_calls"] = [{"id": "evil"}]
    elif change == "multiple":
        body["choices"] *= 2
    elif change == "unicode":
        choice["message"]["content"] = "\ud800"
    else:
        choice["message"]["content"] = "x" * 16385
    assert gateway.clean(body) == {"usage": body["usage"], "error_code": "cloud_response_rejected"}


def test_gateway_refuses_forged_context_before_process_or_key(route, cloud_task):
    with patch.object(gateway, "run", side_effect=AssertionError("worker")):
        with pytest.raises(InvalidInput):
            gateway.complete(
                route.cloud_answer,
                cloud_task,
                {"schema": "local-answer/v1", "answer": "data", "question": "PRIVATE"},
                allow_inference=True,
            )


def test_credential_worker_module_allowlist():
    with pytest.raises(ValueError, match="unsupported"):
        credential_run(b"{}", 1, None, module="arbitrary.module")


def test_transport_can_preserve_usage_without_valid_message(route, cloud_task, monkeypatch):
    raw = json.dumps(
        {"choices": None, "usage": {"prompt_tokens": 20, "completion_tokens": 30}}
    ).encode()

    class Response(io.BytesIO):
        pass

    class Opener:
        def open(self, request, **kwargs):
            assert request.full_url == route.cloud_answer.endpoint
            assert request.get_header("X-opencode-session") == cloud_task
            return Response(raw)

    monkeypatch.setattr(
        gateway.model_gateway, "read_secret", lambda path: "synthetic-secret-for-test-only"
    )
    monkeypatch.setattr(
        gateway.model_gateway.urllib.request, "build_opener", lambda *args: Opener()
    )
    payload = {"messages": [], "model": route.cloud_answer.model}
    with pytest.raises(InvalidInput, match="invalid model response"):
        gateway.model_gateway.complete(route.cloud_answer, cloud_task, payload)
    data = gateway.model_gateway.complete(
        route.cloud_answer, cloud_task, payload, require_message=False
    )
    assert gateway.clean(data) == {"usage": data["usage"], "error_code": "cloud_response_rejected"}
