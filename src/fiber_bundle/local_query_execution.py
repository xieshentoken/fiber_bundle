"""One isolated Pi per client-owned local query; no cloud capability or automatic replay."""

import json
import os
import selectors
import sqlite3
import subprocess
import time
from pathlib import Path
from typing import Any

from . import auth, database, execution_scheduler, executor_container, local_query_gateway
from . import local_query_ledger as ledger
from . import local_query_tasks as tasks
from .answer_model_output import reject_constant, unique_object
from .answer_tasks import PROMPT
from .config import Config, InvalidInput, StorageError, contained, make_directory
from .local_query_process import LocalCallStopped
from .model_gateway import ModelFailure
from .query_routing_config import QueryRoutingConfig


def command(policy: QueryRoutingConfig, task: Path, task_id: str) -> list[str]:
    local = tasks.local_policy(policy)
    return executor_container.command(local.image, task, task_id, network="none", local=True)


def stop_container(task_id: str) -> bool:
    """Remove only the named local container; verify absence with a successful daemon query."""
    name = executor_container.container_name(task_id, local=True)
    try:
        subprocess.run(
            ["docker", "rm", "--force", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
            check=False,
        )
        result = subprocess.run(
            ["docker", "container", "ls", "--all", "--quiet", "--filter", "name=^/" + name + "$"],
            capture_output=True,
            timeout=5,
            check=False,
        )
        return result.returncode == 0 and not result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return False


def stop(config: Config, client_id: str, task_id: str, *, recover: bool = False) -> dict[str, Any]:
    executor_container.identifier(task_id)
    if type(recover) is not bool:
        raise InvalidInput("local_recovery_mode_invalid")
    if recover:
        with executor_container.run_lock(config, task_id):
            return _stop(config, client_id, task_id, recover=True)
    return _stop(config, client_id, task_id, recover=False)


def _stop(config: Config, client_id: str, task_id: str, *, recover: bool) -> dict[str, Any]:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            task = tasks.get(db, client_id, task_id)
            active = db.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
            if active is None or not active[0]:
                raise auth.Denied(401)
            if not recover:
                ledger.stop(config, db, client_id, task_id)
            task_id = task["id"]
        finally:
            db.close()
    if not stop_container(task_id):
        raise InvalidInput("local_container_cleanup_unverified")
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            return ledger.stop(config, db, client_id, task_id, interrupt=recover)
        finally:
            db.close()


def _active(config: Config, owned: ledger.Claim) -> bool:
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            task = tasks.get(db, owned.client_id, owned.task_id)
            if task["state"] != "running":
                return False
            tasks.current(config, db, task)
            return True
        finally:
            db.close()


def execute(
    config: Config, policy: QueryRoutingConfig, client_id: str, task_id: str
) -> dict[str, Any]:
    local = tasks.local_policy(policy)
    executor_container.identifier(task_id)
    with (
        execution_scheduler.admit(config, "interactive"),
        executor_container.run_lock(config, task_id),
    ):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                owned = ledger.claim(config, db, policy, client_id, task_id)
            finally:
                db.close()
        process = None
        completion = None
        success = False
        interrupted = False
        failure = "local_executor_failed"
        deadline = time.monotonic() + local.timeout_seconds
        cleanup = True
        try:
            task = contained(config.state_root, "local-query-tasks/" + task_id)
            make_directory(task)
            if any(task.iterdir()):
                raise InvalidInput("local_task_directory_not_empty")
            # The dedicated unprivileged Pi needs access to its empty scratch directory.
            task.chmod(0o777)
            if not _active(config, owned):
                raise LocalCallStopped()
            process = subprocess.Popen(
                command(policy, task, task_id),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
            )
            assert process.stdin is not None and process.stdout is not None

            def send(frame: dict[str, Any]) -> None:
                assert process is not None and process.stdin is not None
                process.stdin.write(json.dumps(frame, allow_nan=False).encode() + b"\n")
                process.stdin.flush()

            send(
                {
                    "type": "init",
                    "taskMode": "answer",
                    "model": local.model,
                    "context": local.max_input_tokens,
                    "output": local.max_output_tokens,
                    "inputPrice": 0,
                    "outputPrice": 0,
                    "prompt": PROMPT,
                }
            )
            pending = b""
            model_requested = False
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while time.monotonic() < deadline:
                    if not _active(config, owned):
                        raise LocalCallStopped()
                    if not selector.select(0.2):
                        continue
                    chunk = os.read(process.stdout.fileno(), 65536)
                    if not chunk:
                        raise InvalidInput("local_executor_stopped")
                    pending += chunk
                    if len(pending) > 3 * 1024 * 1024:
                        raise InvalidInput("local_executor_frame_too_large")
                    while b"\n" in pending:
                        line, pending = pending.split(b"\n", 1)
                        frame = json.loads(
                            line, object_pairs_hook=unique_object, parse_constant=reject_constant
                        )
                        if not isinstance(frame, dict):
                            raise InvalidInput("local_executor_frame_invalid")
                        if frame.get("type") == "model":
                            if (
                                model_requested
                                or set(frame) != {"type", "id", "body"}
                                or not isinstance(frame["id"], str)
                                or not 0 < len(frame["id"]) <= 80
                            ):
                                raise InvalidInput("local_model_capability_invalid")
                            model_requested = True
                            # Pi cannot substitute its prompt, evidence, model, scope or endpoint.
                            completion = local_query_gateway.complete(
                                local,
                                owned.request,
                                allow_inference=True,
                                should_stop=lambda: not _active(config, owned),
                                deadline=deadline,
                            )
                            if not _active(config, owned):
                                raise LocalCallStopped()
                            send(
                                {"type": "reply", "id": frame["id"], "ok": True, "body": completion}
                            )
                        elif frame.get("type") == "event" and isinstance(frame.get("event"), dict):
                            event = frame["event"]
                            if event.get("type") == "agent_end":
                                messages = event.get("messages", [])
                                if not isinstance(messages, list):
                                    raise InvalidInput("local_executor_end_invalid")
                                assistant = [
                                    m
                                    for m in messages
                                    if isinstance(m, dict) and m.get("role") == "assistant"
                                ]
                                if (
                                    completion is None
                                    or not assistant
                                    or assistant[-1].get("stopReason") != "stop"
                                ):
                                    raise InvalidInput("local_executor_end_invalid")
                                success = True
                                break
                        else:
                            raise InvalidInput("local_executor_capability_refused")
                    if success:
                        break
                if not success:
                    failure = "local_timeout"
        except KeyboardInterrupt:
            interrupted = True
        except ModelFailure as exc:
            failure = (
                exc.code if exc.code in local_query_gateway.REASONS else "local_response_invalid"
            )
        except (
            LocalCallStopped,
            auth.Denied,
            StorageError,
            OSError,
            ValueError,
            sqlite3.Error,
            subprocess.SubprocessError,
            RecursionError,
        ):
            pass
        finally:
            if process is not None:
                cleanup = stop_container(task_id)
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)
                if process.stdin is not None:
                    process.stdin.close()
                if process.stdout is not None:
                    process.stdout.close()
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    if interrupted:
                        ledger.stop(config, db, client_id, task_id, interrupt=True)
                    else:
                        if completion is not None:
                            ledger.finish(
                                config,
                                db,
                                policy,
                                owned,
                                completion,
                                execution_succeeded=success and cleanup,
                            )
                        else:
                            ledger.fail(config, db, policy, owned, failure)
                    row = tasks.get(db, client_id, task_id)
                    result = {
                        "task_id": task_id,
                        "state": row["state"],
                        "error_code": row["error_code"],
                        "container_removed": cleanup,
                    }
                finally:
                    db.close()
        if interrupted:
            raise KeyboardInterrupt()
        return result
