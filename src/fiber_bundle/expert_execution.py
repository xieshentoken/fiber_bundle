"""Finite full-compiler attempts, with revocation polling and preserved paused jobs."""

import json
import subprocess
import time
from typing import Any
from uuid import uuid4

from . import database, document_worker, expert_tasks
from .config import Config, InvalidInput, contained, make_directory
from .document_capture import reject_duplicate
from .document_source import DocumentFile
from .executor_container import run_lock
from .expert_workpack import unpack
from .inputs import canonical
from .objects import digest
from .query_policy_store import write_private
from .query_routing_files import read


def execute(config: Config, identity: str) -> None:
    with run_lock(config, identity):
        with database.locked(config):
            db = database.connect(config.state_root)
            try:
                task = expert_tasks.get(db, identity)
                if task is None or task["state"] != "prepared":
                    raise InvalidInput("expert_start_consumed")
                source, plan, file = expert_tasks.authorize(
                    config, db, _client(db, identity), identity
                )
                packed = expert_tasks.workpack(
                    config, db, source["client_id"], source["vault_id"], task["workpack_import_id"]
                )
                request = json.loads(source["request_json"])
                command = task["next_command"]
                previous = db.execute(
                    "SELECT capture_id FROM source_expert_attempts WHERE task_id=? AND ordinal=?",
                    (identity, task["current_attempt"]),
                ).fetchone()
                ordinal = task["current_attempt"] + 1
                capture_id = str(uuid4())
                db.execute(
                    "INSERT INTO source_expert_attempts VALUES(?,?,?,?,'running',NULL,?)",
                    (identity, ordinal, capture_id, task["workpack_import_id"], time.time()),
                )
                db.execute(
                    "UPDATE source_expert_tasks SET "
                    "state='running',current_attempt=?,updated_at=? WHERE id=?",
                    (ordinal, time.time(), identity),
                )
            finally:
                db.close()
        deadline = time.monotonic() + plan["policy"]["timeout_seconds"]

        def guard() -> None:
            if time.monotonic() >= deadline:
                raise InvalidInput("expert_deadline")
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    expert_tasks.authorize(
                        config, db, source["client_id"], identity, verify_file=False
                    )
                    current = expert_tasks.get(db, identity)
                    if (
                        current is None
                        or current["state"] != "running"
                        or current["current_attempt"] != ordinal
                    ):
                        raise InvalidInput("expert_attempt_stopped")
                    expert_tasks.workpack(
                        config,
                        db,
                        source["client_id"],
                        source["vault_id"],
                        current["workpack_import_id"],
                        verify_file=False,
                    )
                finally:
                    db.close()

        name = "fiber-document-" + capture_id
        created = False
        process = None
        try:
            root = contained(config.state_root, "source-expert/" + capture_id)
            inputs, outputs = root / "input", root / "output"
            make_directory(inputs)
            make_directory(outputs)
            file.copy_new(inputs / "source.pdf")
            if packed:
                unpack(packed, inputs / "workpack")
            if command == "resume":
                if previous is None:
                    raise InvalidInput("expert_resume_missing")
                from .expert_artifacts import copy_tree

                copy_tree(
                    contained(config.state_root, "source-expert/" + previous[0])
                    / "output/workspace",
                    outputs / "workspace",
                )
            runtime = plan["policy"]["expert_runtime"]
            body = {
                "schema": 1,
                "command": command,
                "profile": request.get("expert_profile", "native-text"),
                "backend": request.get("expert_backend"),
                "skill_sha256": runtime["skill_sha256"],
                "workpack": packed is not None,
                "limits": plan["limits"],
            }
            from .project_skill_contract import figure_mode

            figures = figure_mode(plan.get("project_skill"), body["profile"])
            if figures is not None:
                body["figures"] = figures
            write_private(
                root / "request.json",
                canonical(
                    {
                        "input": body,
                        "plan_sha256": plan["plan_sha256"],
                        "source_sha256": file.sha256,
                        "workpack_sha256": packed.sha256 if packed else None,
                    }
                ).encode(),
            )
            guard()
            invocation = document_worker.command(
                document_worker.DocumentPolicy(runtime["image"], plan["policy"]["timeout_seconds"]),
                inputs,
                outputs,
                name,
            )
            if plan.get("project_skill"):
                from .project_skill_runtime import compiler_mounts

                pos = invocation.index(runtime["image"])
                invocation[pos:pos] = compiler_mounts(config, plan["project_skill"])
            subprocess.run(
                invocation,
                check=True,
                timeout=20,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            created = True
            process = subprocess.Popen(
                ["docker", "start", "--attach", "--interactive", name],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            assert process.stdin is not None
            process.stdin.write(canonical(body).encode())
            process.stdin.close()
            while process.poll() is None:
                guard()
                time.sleep(0.1)
            if process.returncode != 0:
                raise InvalidInput("expert_worker_failed")
            guard()
            raw = read(
                outputs / ("result.json" if command == "start" else "resume-result.json"),
                1024 * 1024,
            )
            capture = json.loads(raw, object_pairs_hook=reject_duplicate)
            if (
                canonical(capture).encode() != raw
                or set(capture)
                != {
                    "schema",
                    "source_sha256",
                    "skill_sha256",
                    "doctor",
                    "result",
                    "status",
                    "knowledge_verified",
                    "model_calls",
                    "artifact_validation",
                }
                or capture["schema"] != "fiber-expert-capture/v1"
                or capture["source_sha256"] != file.sha256
                or capture["skill_sha256"] != runtime["skill_sha256"]
                or capture["knowledge_verified"] is not False
                or type(capture["model_calls"]) is not int
                or capture["model_calls"] != 0
            ):
                raise InvalidInput("expert_capture_binding")
            DocumentFile(inputs / "source.pdf", file.sha256, file.byte_count).verify()
            result = capture["result"]
            state = result["status"]
            if (
                state not in {"paused", "completed", "rejected"}
                or capture["status"]["status"] != state
            ):
                raise InvalidInput("expert_capture_state")
            from .expert_artifacts import capture_package

            artifact = (
                capture_package(outputs, capture, file, plan) if state == "completed" else None
            )
            counts = artifact or result.get("direct_reference", {})
            summary: dict[str, Any] = {
                "code": result.get("code"),
                "next_action": result.get("next_action", {}).get("meaning"),
                "object_count": counts.get("object_count", 0),
                "anchor_count": counts.get("anchor_count", 0),
                "artifact_manifest": artifact,
            }
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    expert_tasks.authorize(config, db, source["client_id"], identity)
                    expert_tasks.workpack(
                        config,
                        db,
                        source["client_id"],
                        source["vault_id"],
                        task["workpack_import_id"],
                    )
                    expert_tasks.finish(
                        db,
                        identity,
                        ordinal,
                        "failed" if state == "rejected" else state,
                        summary,
                        digest(raw),
                    )
                finally:
                    db.close()
        except BaseException:
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    task = expert_tasks.get(db, identity)
                    if task is not None and task["state"] == "running":
                        expert_tasks.finish(
                            db,
                            identity,
                            ordinal,
                            "failed",
                            {
                                "code": "expert_attempt_failed",
                                "next_action": "保留原件与本次作业，核对运行条件后显式重建任务。",
                            },
                            None,
                        )
                finally:
                    db.close()
            raise
        finally:
            if created:
                document_worker.remove(name)
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait(timeout=5)


def _client(db: Any, identity: str) -> str:
    row = db.execute("SELECT client_id FROM source_imports WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise InvalidInput("expert_source_missing")
    return str(row[0])
