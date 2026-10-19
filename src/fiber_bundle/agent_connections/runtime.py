"""Owned finite operations; never kill a process recovered solely by its PID."""

import hashlib
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from typing import Any

from .. import database, local_query_budget
from ..config import Config
from ..inputs import canonical
from ..runtime_environment import child_environment
from . import application, cloud_budget, configuration, files, models, store


class Runtime:
    def __init__(self, config: Config, endpoint: str):
        self.config, self.endpoint = config, endpoint
        self.owned: dict[str, subprocess.Popen[bytes]] = {}
        self.lock = threading.Lock()

    def start(
        self,
        db: sqlite3.Connection,
        identity: str,
        kind: str,
        request_id: str,
        expected: int,
        request: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if kind == "rotate_credential":
            return configuration.rotate(self.config, db, identity, expected, request_id)
        existing = db.execute(
            "SELECT * FROM agent_connection_operations WHERE connection_id=? AND request_id=?",
            (identity, request_id),
        ).fetchone()
        payload = {"expected_revision": expected, "request": request}
        if existing:
            if (
                existing["request_sha256"]
                != hashlib.sha256(
                    canonical({"kind": kind, "payload": payload}).encode()
                ).hexdigest()
            ):
                raise store.Conflict("agent_request_conflict")
            return store.op_view(existing)
        row = store.get(db, identity)
        store.cas(row, expected)
        with database.transaction(db):
            if db.execute(
                "SELECT 1 FROM agent_connection_operations WHERE connection_id=? AND "
                "state IN ('queued','running')",
                (identity,),
            ).fetchone():
                raise store.Conflict("agent_operation_busy")
            if (
                kind in ("probe", "test_query")
                and not store.context(self.config, db, identity)["enabled"]
            ):
                raise store.Conflict("agent_paused")
            if kind == "test_query":
                if store.spec(row).agent_type != "hermes":
                    raise store.Conflict("agent_managed_test_unavailable")
                model = models.resolve(self.config, store.spec(row).model_configuration_id)
                day = time.time() - time.time() % 86400
                count = db.execute(
                    "SELECT count(*) FROM agent_connection_operations WHERE "
                    "connection_id=? AND kind='test_query' AND created_at>=?",
                    (identity, day),
                ).fetchone()[0]
                total = local_query_budget.count(db, datetime.now(UTC).date().isoformat())
                if count >= 10 or (
                    model["processing_location"] == "local" and total >= model["daily_calls"]
                ):
                    raise store.Conflict("agent_daily_budget_exhausted")
                reservation = (
                    cloud_budget.admit(db, model)
                    if model["processing_location"] == "cloud"
                    else None
                )
            op = store.operation(db, identity, kind, request_id, payload)
            if kind == "test_query":
                admission: dict[str, Any] = {"expected_revision": expected}
                if reservation is not None:
                    admission["cloud_reservation"] = reservation
                admission["model_snapshot"] = model
                db.execute(
                    "UPDATE agent_connection_operations SET payload_json=? WHERE id=?",
                    (canonical(admission), op["id"]),
                )
        if kind == "apply_config":
            try:
                result = application.apply(self.config, db, row, self.endpoint)
                store.finish(db, op["id"], "succeeded", result)
            except Exception:
                configuration.pause_grants(db, row["client_id"])
                db.execute(
                    "UPDATE agent_connections SET state='repair_required',"
                    "applied_json=NULL WHERE id=?",
                    (identity,),
                )
                store.finish(db, op["id"], "reconcile", error="agent_apply_failed")
            return store.op_view(store.op(db, identity, op["id"]))
        path = files.put(
            self.config,
            identity,
            op["id"] + ".job.json",
            {
                "storage": self.config.storage_configuration(),
                "project_root": str(self.config.project_root),
                "connection_id": identity,
                "operation_id": op["id"],
                "request": request,
            },
        )
        try:
            child = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "fiber_bundle.agent_connections.cli",
                    "operation",
                    "--job-file",
                    str(path),
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=child_environment(),
                start_new_session=True,
            )
        except OSError:
            store.finish(db, op["id"], "failed", error="agent_launch_failed")
            return store.op_view(store.op(db, identity, op["id"]))
        with self.lock:
            self.owned[op["id"]] = child
        limit = store.spec(row).limits.timeout_seconds
        threading.Thread(
            target=self.observe,
            args=(identity, op["id"], child, limit),
            daemon=True,
            name="agent-operation-observer",
        ).start()
        return store.op_view(store.op(db, identity, op["id"]))

    def observe(self, identity: str, oid: str, child: subprocess.Popen[bytes], limit: int) -> None:
        deadline = time.monotonic() + limit
        reason = None
        while child.poll() is None:
            with database.locked(self.config):
                db = database.connect(self.config.state_root)
                try:
                    operation = store.op(db, identity, oid)
                    row = store.get(db, identity)
                    if operation["state"] in ("queued", "running") and (
                        operation["cancel_requested"]
                        or row["revision"] != operation["revision"]
                        or row["credential_generation"] != operation["credential_generation"]
                    ):
                        reason = "agent_cancelled"
                finally:
                    db.close()
            if time.monotonic() >= deadline:
                reason = "agent_timeout"
            if reason:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        child.kill()
                    child.wait(timeout=2)
                break
            time.sleep(0.1)
        child.wait()
        with self.lock:
            self.owned.pop(oid, None)
        with database.locked(self.config):
            db = database.connect(self.config.state_root)
            try:
                op = store.op(db, identity, oid)
                if op["state"] in ("queued", "running"):
                    row = store.get(db, identity)
                    if (
                        row["revision"] == op["revision"]
                        and row["credential_generation"] == op["credential_generation"]
                    ):
                        column = "test_json" if op["kind"] == "test_query" else "probe_json"
                        db.execute(
                            "UPDATE agent_connections SET " + column + "=? WHERE id=?",
                            (
                                canonical(
                                    {
                                        "state": "cancelled"
                                        if reason == "agent_cancelled"
                                        else "failed",
                                        "context": store.context(self.config, db, identity),
                                        "operation_id": oid,
                                        "error_code": reason or "agent_worker_failed",
                                    }
                                ),
                                identity,
                            ),
                        )
                    store.finish(
                        db,
                        oid,
                        "cancelled" if reason == "agent_cancelled" else "failed",
                        error=reason or "agent_worker_failed",
                    )
            finally:
                db.close()

    def close(self) -> None:
        with self.lock:
            items = list(self.owned.items())
        for _, child in items:
            if child.poll() is None:
                try:
                    os.killpg(child.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    child.terminate()
                try:
                    child.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(child.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    except PermissionError:
                        child.kill()
                    child.wait(timeout=2)
