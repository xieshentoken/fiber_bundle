"""Opt-in two-round Pi supervisor; synthetic and provider candidate modes are distinct."""

import json
import os
import select
import selectors
import subprocess
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import database, execution_ledger, execution_scheduler, executor_container, model_gateway
from . import document_creation_artifacts as artifacts
from . import document_creation_binding as binding
from . import document_creation_dialogue as dialogue
from . import document_creation_execution as execution
from . import document_creation_profile as profile
from . import document_creation_rounds as rounds
from . import document_creation_tasks as tasks
from .answer_model_output import reject_constant, unique_object
from .config import Config, InvalidInput, contained, make_directory
from .document_creation_config import CreationExecutionConfig
from .document_creation_output import context
from .inputs import canonical


def _reply(policy: CreationExecutionConfig, ordinal: int, draft: str) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": "Synthetic completion"}
    if ordinal == 1:
        message = {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "synthetic-draft-call",
                    "type": "function",
                    "function": {
                        "name": "fiber_write_draft",
                        "arguments": json.dumps({"draft": draft}, ensure_ascii=True),
                    },
                }
            ],
        }
    return {
        "id": "synthetic-completion",
        "created": 0,
        "model": policy.policy().model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": "tool_calls" if ordinal == 1 else "stop",
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }


def _send(
    process: subprocess.Popen[bytes],
    frame: dict[str, Any],
    deadline: float,
    check: Callable[[], None],
) -> None:
    assert process.stdin is not None
    raw = (canonical(frame) + "\n").encode()
    if len(raw) > 1024 * 1024:
        raise InvalidInput("document_runtime_frame_too_large")
    offset = 0
    while offset < len(raw):
        if time.monotonic() >= deadline:
            raise TimeoutError("document_runtime_timeout")
        check()
        try:
            offset += os.write(process.stdin.fileno(), raw[offset:])
        except BlockingIOError:
            select.select([], [process.stdin], [], 0.1)


def _stop(run_id: str) -> None:
    executor_container.stop(run_id)
    result = subprocess.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "name=" + executor_container.container_name(run_id),
            "--format",
            "{{.Names}}",
        ],
        capture_output=True,
        timeout=15,
        env={k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ},
    )
    if result.returncode != 0 or result.stdout.strip():
        raise InvalidInput("document_runtime_cleanup_unconfirmed")


def _drive(
    config: Config,
    policy: CreationExecutionConfig,
    owner: execution.Responsibility,
    attempt: execution.Attempt,
    task: Path,
    init: dict[str, Any],
    draft: str,
    mode: str = "synthetic",
) -> None:
    command = executor_container.command(
        policy.policy().image, task, attempt.run_id, network="none"
    )
    deadline = time.monotonic() + policy.policy().timeout_seconds
    environment = {k: os.environ[k] for k in ("PATH", "HOME") if k in os.environ}
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=environment,
    )
    assert process.stdin and process.stdout and process.stderr
    os.set_blocking(process.stdin.fileno(), False)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ, "out")
    selector.register(process.stderr, selectors.EVENT_READ, "err")
    buffer, total, ended, count = b"", 0, False, 0

    def current() -> None:
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                execution.verify(config, main, attempt, policy, owner)
            finally:
                main.close()

    try:
        _send(process, init, deadline, current)
        while not ended:
            current()
            if time.monotonic() >= deadline:
                raise TimeoutError("document_runtime_timeout")
            for key, _ in selector.select(min(0.2, max(0, deadline - time.monotonic()))):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    if key.data == "out" and not ended:
                        raise InvalidInput("document_runtime_exited")
                    continue
                total += len(chunk)
                if total > 2 * 1024 * 1024:
                    raise InvalidInput("document_runtime_output_limit")
                if key.data == "err":
                    continue
                buffer += chunk
                if len(buffer) > 512 * 1024:
                    raise InvalidInput("document_runtime_frame_too_large")
                while b"\n" in buffer:
                    raw, buffer = buffer.split(b"\n", 1)
                    frame = json.loads(
                        raw, object_pairs_hook=unique_object, parse_constant=reject_constant
                    )
                    if not isinstance(frame, dict):
                        raise InvalidInput("document_runtime_protocol_invalid")
                    if frame.get("type") == "event":
                        if set(frame) != {"type", "event"} or not isinstance(frame["event"], str):
                            raise InvalidInput("document_runtime_event_invalid")
                        if frame["event"] == "agent_end":
                            if count != 2:
                                raise InvalidInput("document_runtime_incomplete")
                            ended = True
                        continue
                    if (
                        frame.get("type") != "model"
                        or set(frame) != {"type", "id", "body"}
                        or not isinstance(frame["id"], str)
                        or frame["id"] != str(count + 1)
                    ):
                        raise InvalidInput("document_runtime_protocol_invalid")
                    with database.locked(config):
                        main = database.connect(config.state_root)
                        try:
                            call, payload = rounds.dispatch(
                                config, main, attempt, policy, owner, frame["body"]
                            )
                        finally:
                            main.close()
                    remaining = deadline - time.monotonic()
                    if remaining < 1:
                        raise TimeoutError("document_runtime_timeout")
                    body = (
                        model_gateway.complete(
                            replace(policy.policy(), timeout_seconds=max(1, int(remaining))),
                            attempt.run_id,
                            payload,
                            require_message=False,
                        )
                        if mode == "provider"
                        else _reply(policy, count + 1, draft)
                    )
                    with database.locked(config):
                        main = database.connect(config.state_root)
                        try:
                            rounds.accept(config, main, call, policy, owner, body)
                        finally:
                            main.close()
                    count += 1
                    _send(
                        process,
                        {"type": "reply", "id": frame["id"], "ok": True, "body": body},
                        deadline,
                        current,
                    )
        if buffer.strip():
            raise InvalidInput("document_runtime_trailing_frame")
        current()
        process.stdin.close()
        if process.wait(timeout=min(5, max(0.1, deadline - time.monotonic()))) != 0:
            raise InvalidInput("document_runtime_exit_failed")
    finally:
        selector.close()
        # Stop the exact bound container even if the CLI detached or disappeared.
        try:
            _stop(attempt.run_id)
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=5)
            process.stdout.close()
            process.stderr.close()
            if not process.stdin.closed:
                process.stdin.close()


def _run(
    config: Config,
    client_id: str,
    task_id: str,
    policy: CreationExecutionConfig,
    draft: str,
    *,
    expected_revision: int,
    expected_plan_sha256: str,
    expected_configuration_sha256: str,
    allow_synthetic: bool = False,
    mode: str = "synthetic",
) -> dict[str, Any]:
    """Explicit opt-in drives real Pi with canned replies in isolated projects."""
    if allow_synthetic is not True:
        raise InvalidInput("document_synthetic_execution_not_enabled")
    if mode == "synthetic":
        dialogue.response(policy.policy(), _reply(policy, 1, draft), 1)
    attempt = None
    with database.locked(config):
        main = database.connect(config.state_root)
        try:
            amendment = (
                main.execute(
                    "SELECT 1 FROM document_creation_continuations WHERE child_task_id=?",
                    (task_id,),
                ).fetchone()
                is not None
            )
        finally:
            main.close()
    with (
        execution_scheduler.admit(config, "interactive" if amendment else "heavy"),
        execution.responsibility(config, client_id, task_id) as owner,
    ):
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                plan = tasks.plan(config, main, client_id, task_id)
                profile.check(policy, plan.requested_format, mode)
                attempt = execution.claim(
                    config,
                    main,
                    client_id,
                    task_id,
                    policy,
                    owner,
                    expected_revision=expected_revision,
                    expected_plan_sha256=expected_plan_sha256,
                    expected_configuration_sha256=expected_configuration_sha256,
                    allow_reservation=True,
                )
                rounds.attach(config, main, attempt, policy, owner, mode=mode)
                bound = binding.get(main, task_id)
                assert bound is not None
                output_context = context(
                    plan, attempt.run_id, policy.sha256, bound["request_sha256"]
                )
                init = {
                    "type": "init",
                    "model": policy.policy().model,
                    **(
                        {"protocol": policy.policy().protocol, "effort": policy.policy().effort}
                        if hasattr(policy.policy(), "configuration_id")
                        else {}
                    ),
                    "format": plan.requested_format,
                    "context": output_context,
                    "prompt": dialogue.prompt(bound["request_json"]),
                    "output": policy.policy().max_output_tokens,
                    "contextTokens": policy.policy().max_input_tokens,
                    "inputPrice": policy.policy().input_nusd / 1000,
                    "outputPrice": policy.policy().output_nusd / 1000,
                }
            finally:
                main.close()
        try:
            task = contained(config.state_root, "document-runs/" + attempt.run_id)
            if task.exists():
                raise InvalidInput("document_runtime_directory_not_replayable")
            make_directory(task)
            task.chmod(0o777)  # Private 0700 state parent; only this leaf is mounted for UID 1000.
            _drive(config, policy, owner, attempt, task, init, draft, mode)
            with database.locked(config):
                main = database.connect(config.state_root)
                try:
                    artifacts.register(config, main, attempt, policy, owner, task)
                    return execution.status(main, client_id, task_id)
                finally:
                    main.close()
        except Exception as exc:
            code = (
                "document_runtime_timeout"
                if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired))
                else "document_budget_exhausted"
                if isinstance(exc, execution_ledger.BudgetExceeded)
                else "document_runtime_failed"
            )
            with database.locked(config):
                main = database.connect(config.state_root)
                try:
                    rounds.fail(main, attempt, code)
                finally:
                    main.close()
            raise


def recover_synthetic(config: Config, client_id: str, task_id: str) -> dict[str, Any]:
    """Stop a released synthetic run's exact container; never take over or replay requests."""
    with executor_container.run_lock(config, task_id):
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                bound = binding.owned(main, client_id, task_id)
                run, _ = binding.checked(main, bound)
                session = main.execute(
                    "SELECT mode FROM document_creation_sessions WHERE run_id=?", (run["id"],)
                ).fetchone()
                if session is None or session["mode"] not in {"synthetic", "provider"}:
                    raise InvalidInput("document_runtime_not_attached")
                run_id = run["id"]
                binding.stop(main, bound, interrupt=True)
            finally:
                main.close()
        _stop(run_id)
        with database.locked(config):
            main = database.connect(config.state_root)
            try:
                return execution.status(main, client_id, task_id)
            finally:
                main.close()


def run_synthetic(
    config: Config,
    client_id: str,
    task_id: str,
    policy: CreationExecutionConfig,
    draft: str,
    *,
    expected_revision: int,
    expected_plan_sha256: str,
    expected_configuration_sha256: str,
    allow_synthetic: bool = False,
) -> dict[str, Any]:
    return _run(
        config,
        client_id,
        task_id,
        policy,
        draft,
        expected_revision=expected_revision,
        expected_plan_sha256=expected_plan_sha256,
        expected_configuration_sha256=expected_configuration_sha256,
        allow_synthetic=allow_synthetic,
    )


def run_provider(
    config: Config,
    client_id: str,
    task_id: str,
    policy: CreationExecutionConfig,
    *,
    expected_revision: int,
    expected_plan_sha256: str,
    expected_configuration_sha256: str,
    allow_provider: bool = False,
) -> dict[str, Any]:
    """Explicit candidate dispatch, no retries/fallback. Qualification is separately recorded."""
    if allow_provider is not True:
        raise InvalidInput("document_provider_execution_not_enabled")
    return _run(
        config,
        client_id,
        task_id,
        policy,
        "",
        expected_revision=expected_revision,
        expected_plan_sha256=expected_plan_sha256,
        expected_configuration_sha256=expected_configuration_sha256,
        allow_synthetic=True,
        mode="provider",
    )
