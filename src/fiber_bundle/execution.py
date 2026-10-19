"""One isolated Pi process per bound run; stdio is the only host capability channel."""

import json
import os
import selectors
import subprocess
import time
from pathlib import Path
from typing import Any

from fiber_bundle import (
    answer_tasks,
    article_bindings,
    article_tasks,
    auth,
    cloud_answer_binding,
    database,
    document_bindings,
    document_tasks,
    execution_diagnostics,
    execution_scheduler,
    model_gateway,
    vaults,
)
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.answer_drafts import DraftRejected
from fiber_bundle.answer_model_output import AnswerOutputRejected
from fiber_bundle.config import Config, IntegrityError, InvalidInput, contained, make_directory
from fiber_bundle.execution_config import ExecutionConfig
from fiber_bundle.executor_container import command as container_command
from fiber_bundle.executor_container import container_name as container_name
from fiber_bundle.executor_container import identifier as identifier
from fiber_bundle.executor_container import run_lock as run_lock
from fiber_bundle.executor_container import stop as stop
from fiber_bundle.model_budgets import Exhausted as KeyBudgetExceeded
from fiber_bundle.model_configuration.errors import ModelError


def command(
    policy: ExecutionConfig,
    task: Path,
    run_id: str,
    *,
    article: bool = False,
    answer: bool = False,
    document: bool = False,
    processing: bool = False,
) -> list[str]:
    policy.validate()
    if hasattr(policy, "configuration_id"):
        from .model_configuration.runtime import require

        require(policy.image)
    return container_command(
        policy.image,
        task,
        run_id,
        network="none" if article or answer or document or processing else "bridge",
    )


def cancel(config: Config, run_id: str) -> None:
    identifier(run_id)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            ledger.get_run(db, run_id)
            ledger.transition(db, run_id, "cancelled", "cancelled")
        finally:
            db.close()
    try:
        stop(run_id)
    finally:
        try:
            article_tasks.stop(config, run_id)
        finally:
            document_tasks.stop(config, run_id)


def recover(config: Config, run_id: str) -> None:
    with run_lock(config, run_id):
        try:
            stop(run_id)
        finally:
            try:
                article_tasks.stop(config, run_id)
            finally:
                document_tasks.stop(config, run_id)
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                run = ledger.get_run(db, run_id)
                if run["state"] == "running":
                    ledger.interrupt(db, run_id)
            finally:
                db.close()


def handle(
    config: Config,
    policy: ExecutionConfig,
    run_id: str,
    frame: dict[str, Any],
    *,
    deadline: float | None = None,
) -> dict[str, Any]:
    if frame.get("type") not in (
        "model",
        "vaults",
        "article_capture",
        "document_extract",
    ) or not isinstance(frame.get("id"), str):
        raise InvalidInput("invalid capability frame")
    if len(frame["id"]) > 80:
        raise InvalidInput("invalid capability identifier")
    if frame["type"] == "document_extract":
        if set(frame) != {"type", "id", "body"}:
            raise InvalidInput("invalid document capability frame")
        body = document_tasks.extract_bound(
            config,
            policy,
            run_id,
            frame["body"],
            deadline=deadline
            if deadline is not None
            else time.monotonic() + policy.timeout_seconds,
        )
        return {"type": "reply", "id": frame["id"], "ok": True, "body": body}
    if frame["type"] == "article_capture":
        if set(frame) != {"type", "id", "body"}:
            raise InvalidInput("invalid article capability frame")
        body = article_tasks.capture_bound(
            config,
            policy,
            run_id,
            frame.get("body"),
            deadline=deadline
            if deadline is not None
            else time.monotonic() + policy.timeout_seconds,
        )
        return {"type": "reply", "id": frame["id"], "ok": True, "body": body}
    call_id = None
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            run = ledger.get_run(db, run_id)
            if cloud_answer_binding.get(db, run_id) is not None:
                raise InvalidInput("cloud_answer_requires_bound_executor")
            if run["state"] != "running":
                raise InvalidInput("execution stopped")
            ledger.authorize(config, db, run)
            article = article_bindings.get(db, run_id)
            answer = answer_tasks.get(db, run_id)
            document = document_bindings.get(db, run_id)
            processing = db.execute(
                "SELECT 1 FROM pi_processing_runs WHERE run_id=?", (run_id,)
            ).fetchone()
            if frame["type"] == "vaults":
                if article is not None or answer is not None or document is not None or processing:
                    raise InvalidInput("bound tasks cannot use vault query tools")
                if frame.get("body") != {}:
                    raise InvalidInput("vault request does not accept scope")
                vault_list = [
                    v
                    for v in vaults.client_vaults(db, run["client_id"])
                    if v["id"] == run["vault_id"]
                ]
                return {"type": "reply", "id": frame["id"], "ok": True, "body": vault_list}
            payload = model_gateway.prepare(
                policy,
                answer_tasks.model_request(config, db, run, answer)
                if answer is not None
                else article_bindings.model_request(article)
                if article is not None
                else document_bindings.model_request(document)
                if document is not None
                else frame.get("body"),
            )
            call_id = ledger.reserve(config, db, run_id, policy)
        finally:
            db.close()
    try:
        body = model_gateway.complete(policy, run_id, payload)
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                ledger.settle(db, call_id, policy, body.get("usage"))
                run = ledger.get_run(db, run_id)
                ledger.authorize(config, db, run)
                if run["state"] != "running":
                    raise InvalidInput("execution stopped during model call")
                if answer_tasks.get(db, run_id) is not None:
                    answer_tasks.accept(config, db, run, body)
            finally:
                db.close()
        return {"type": "reply", "id": frame["id"], "ok": True, "body": body}
    except Exception:
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                db.execute(
                    "UPDATE model_calls SET state='uncertain' WHERE id=? AND state='reserved'",
                    (call_id,),
                )
            finally:
                db.close()
        raise


def execute(config: Config, policy: ExecutionConfig, run_id: str, prompt: str) -> dict[str, Any]:
    identifier(run_id)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            interactive = answer_tasks.get(db, run_id) is not None
            if cloud_answer_binding.get(db, run_id) is not None:
                raise InvalidInput("cloud_answer_requires_bound_executor")
            if article_bindings.get(db, run_id) is not None:
                prompt = article_bindings.ARTICLE_PROMPT
            elif document_bindings.get(db, run_id) is not None:
                prompt = document_bindings.PROMPT
            elif answer_tasks.get(db, run_id) is not None:
                prompt = answer_tasks.PROMPT
            elif binding := db.execute(
                "SELECT kind,source_sha256,input_json FROM pi_processing_runs WHERE run_id=?",
                (run_id,),
            ).fetchone():
                from .pi_processing import task_prompt

                prompt = task_prompt(dict(binding))
        finally:
            db.close()
    if not prompt or len(prompt.encode()) > policy.max_request_bytes // 2:
        raise InvalidInput("invalid task prompt")
    with (
        execution_scheduler.admit(config, "interactive" if interactive else "heavy"),
        run_lock(config, run_id),
    ):
        return _execute(config, policy, run_id, prompt)


def _execute(config: Config, policy: ExecutionConfig, run_id: str, prompt: str) -> dict[str, Any]:
    task = contained(config.state_root, "tasks/" + run_id)
    make_directory(task)
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            ledger.start(config, db, run_id, policy)
            article_mode = article_bindings.get(db, run_id) is not None
            answer_mode = answer_tasks.get(db, run_id) is not None
            document_mode = document_bindings.get(db, run_id) is not None
            from . import pi_processing

            processing = pi_processing.get(db, run_id)
            if processing is not None:
                pi_processing.materialize(config, processing)
        finally:
            db.close()
    process = None
    success = False
    failure = "execution_interrupted"
    answer_rejection = None
    tool_calls = 0
    trace: list[dict[str, Any]] = []
    elapsed_start = time.monotonic()
    try:
        invocation = command(
            policy,
            task,
            run_id,
            article=article_mode,
            answer=answer_mode,
            document=document_mode,
            processing=processing is not None,
        )
        project_skill = (
            json.loads(processing["input_json"]).get("project_skill") if processing else None
        )
        if project_skill:
            from .project_skill_runtime import pi_mounts

            pos = invocation.index(policy.image)
            invocation[pos:pos] = pi_mounts(config, project_skill)
        video_skill = (
            json.loads(processing["input_json"]).get("video_skill") if processing else None
        )
        if video_skill:
            from .video_skill import pi_mounts as video_mounts

            pos = invocation.index(policy.image)
            invocation[pos:pos] = video_mounts(config, video_skill)
        process = subprocess.Popen(
            invocation,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None and process.stdout is not None

        def send(frame: dict[str, Any]) -> None:
            assert process is not None and process.stdin is not None
            process.stdin.write(json.dumps(frame).encode() + b"\n")
            process.stdin.flush()

        send(
            {
                "type": "init",
                "model": policy.model,
                "protocol": getattr(policy, "protocol", "openai-completions"),
                "effort": getattr(policy, "effort", "default"),
                "context": policy.max_input_tokens,
                "output": policy.max_output_tokens,
                "inputPrice": policy.input_nusd / 1000,
                "outputPrice": policy.output_nusd / 1000,
                "prompt": prompt,
                "processingKind": processing["kind"] if processing else None,
                "taskMode": "processing"
                if processing
                else "document"
                if document_mode
                else "answer"
                if answer_mode
                else ("article" if article_mode else "read"),
            }
        )
        # A processing turn includes several model/tool rounds; retain each request's own timeout.
        task_seconds = (
            min(600, policy.timeout_seconds * 3) if processing else policy.timeout_seconds
        )
        deadline = time.monotonic() + task_seconds
        pending = b""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while time.monotonic() < deadline:
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        run = ledger.get_run(db, run_id)
                        if run["state"] != "running":
                            raise InvalidInput("execution no longer running")
                        ledger.authorize(config, db, run)
                    finally:
                        db.close()
                if not selector.select(0.2):
                    continue
                chunk = os.read(process.stdout.fileno(), 65536)
                if not chunk:
                    raise InvalidInput("executor stopped")
                pending += chunk
                if len(pending) > 3 * 1024 * 1024:
                    raise InvalidInput("executor frame too large")
                while b"\n" in pending:
                    line, pending = pending.split(b"\n", 1)
                    frame = json.loads(line)
                    if not isinstance(frame, dict):
                        raise InvalidInput("invalid executor frame")
                    if frame.get("type") in (
                        "model",
                        "vaults",
                        "article_capture",
                        "document_extract",
                    ):
                        tool_calls += frame["type"] != "model"
                        reply = handle(config, policy, run_id, frame, deadline=deadline)
                        if processing and frame["type"] == "model":
                            adapted = pi_processing.normalize_draft_write(processing, reply["body"])
                            if adapted and len(trace) < 100:
                                trace.append(
                                    {"event": "draft_write_content_normalized", "count": adapted}
                                )
                        if frame["type"] == "model" and len(trace) < 100:
                            trace.append(execution_diagnostics.model(reply))
                        send(reply)
                    elif frame.get("type") == "event":
                        event = frame.get("event")
                        if not isinstance(event, dict):
                            raise InvalidInput("invalid executor event")
                        diagnostic = execution_diagnostics.event(event)
                        if diagnostic and len(trace) < 100:
                            trace.append(diagnostic)
                        if event.get("type") == "agent_end":
                            messages = event.get("messages", [])
                            assistant = [
                                m
                                for m in messages
                                if isinstance(m, dict) and m.get("role") == "assistant"
                            ]
                            if not assistant or assistant[-1].get("stopReason") != "stop":
                                raise InvalidInput("model did not finish successfully")
                            success = True
                            break
                    elif frame.get("type") in ("exit", "protocol_error"):
                        raise InvalidInput("executor protocol failed")
                if success:
                    break
            if not success:
                raise InvalidInput("execution deadline exceeded")
    except AnswerOutputRejected as exc:
        failure = exc.code
        answer_rejection = {"code": exc.code, "schema_issues": exc.schema_issues}
        if exc.json_issue is not None:
            answer_rejection["json_issue"] = exc.json_issue
    except DraftRejected as exc:
        failure = exc.code
    except (model_gateway.ModelFailure, ModelError) as exc:
        failure = exc.code
    except (ledger.BudgetExceeded, KeyBudgetExceeded):
        failure = "budget_exhausted"
    except (
        OSError,
        ValueError,
        InvalidInput,
        IntegrityError,
        auth.Denied,
        subprocess.SubprocessError,
    ) as exc:
        failure = (
            {
                "execution deadline exceeded": "processing_execution_timeout",
                "executor stopped": "processing_executor_stopped",
                "executor protocol failed": "processing_executor_protocol",
                "model did not finish successfully": "processing_model_incomplete",
            }.get(str(exc), "execution_failed")
            if processing
            else "execution_failed"
        )
    finally:
        stop(run_id)
        if process is not None:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.stdin is not None:
                process.stdin.close()
            if process.stdout is not None:
                process.stdout.close()
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                if success:
                    run = ledger.get_run(db, run_id)
                    try:
                        ledger.authorize(config, db, run)
                        if document_bindings.get(db, run_id) is not None:
                            document_tasks.verify_complete(config, db, run)
                        answer = answer_tasks.get(db, run_id)
                        if answer is not None:
                            answer_tasks.current(config, db, run, answer)
                            if answer["result_json"] is None:
                                raise InvalidInput("missing validated answer")
                    except (auth.Denied, InvalidInput, IntegrityError, OSError, ValueError):
                        success = False
                        failure = "authorization_revoked"
                    # An untrusted runtime cannot claim success before any settled model call.
                    settled = db.execute(
                        "SELECT 1 FROM model_calls WHERE run_id=? AND state='settled'", (run_id,)
                    ).fetchone()
                    if not settled:
                        success = False
                    document = document_bindings.get(db, run_id)
                    if document is not None and document["state"] != "extracted":
                        success = False
                        failure = "document_extract_not_completed"
                    article = article_bindings.get(db, run_id)
                    if article is not None and article["state"] != "captured":
                        success = False
                        failure = "article_capture_not_completed"
                ledger.transition(
                    db,
                    run_id,
                    "succeeded"
                    if success
                    else ("waiting_budget" if failure == "budget_exhausted" else "failed"),
                    None if success else failure,
                )
                db.execute(
                    "UPDATE model_calls SET state='uncertain' WHERE run_id=? AND state='reserved'",
                    (run_id,),
                )
                from .inputs import canonical
                from .query_policy_store import write_private

                try:
                    write_private(
                        task / "execution-diagnostics.json",
                        canonical(
                            {
                                "state": "succeeded" if success else "failed",
                                "error": None if success else failure,
                                "elapsed_seconds": round(time.monotonic() - elapsed_start, 3),
                                "events": trace,
                            }
                        ).encode(),
                    )
                except OSError:
                    import logging

                    logging.getLogger(__name__).warning("Execution diagnostics could not be saved")
                row = ledger.get_run(db, run_id)
                result = {
                    "run_id": run_id,
                    "state": row["state"],
                    "error_code": row["error_code"],
                    "backend_calls": tool_calls,
                }
                if answer_rejection is not None:
                    result["answer_rejection"] = answer_rejection
                document_task = document_bindings.get(db, run_id)
                if document_task is not None:
                    result["document_extract"] = document_bindings.status(document_task)
                capture_task = article_bindings.get(db, run_id)
                if capture_task is not None:
                    result["article_capture"] = article_bindings.status(capture_task)
            finally:
                db.close()
    return result
