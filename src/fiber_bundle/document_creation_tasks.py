"""Owned, default-off document tasks; durable intent and requirements, no executor I/O."""

import re
import sqlite3
import time
from typing import Any
from uuid import uuid4

from . import auth, database
from . import document_creation_binding as execution_binding
from . import document_creation_contract as contract
from . import document_creation_ledger as ledger
from . import document_creation_preparation as preparation
from .config import Config, IntegrityError, InvalidInput
from .inputs import text
from .objects import digest


def _active(main: sqlite3.Connection, client_id: str) -> None:
    text(client_id, limit=128)
    row = main.execute("SELECT active FROM clients WHERE id=?", (client_id,)).fetchone()
    if row is None or row[0] != 1:
        raise auth.Denied(401)


def _owned(db: sqlite3.Connection | None, client_id: str, task_id: str) -> sqlite3.Row:
    row = (
        None
        if db is None
        else db.execute(
            "SELECT * FROM tasks WHERE id=? AND client_id=?", (task_id, client_id)
        ).fetchone()
    )
    if not isinstance(row, sqlite3.Row):
        raise auth.Denied(404)
    return row


def _expected(task: sqlite3.Row, expected_revision: int, expected_sha256: str) -> None:
    if (
        type(expected_revision) is not int
        or not 1 <= expected_revision <= contract.MAX_REVISIONS
        or not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
    ):
        raise InvalidInput("document_revision_invalid")
    if (task["head_revision"], task["head_sha256"]) != (expected_revision, expected_sha256):
        raise InvalidInput("document_plan_conflict")


def status(
    config: Config, main: sqlite3.Connection, client_id: str, task_id: str
) -> dict[str, Any]:
    _active(main, client_id)
    with ledger.connection(config) as db:
        task = _owned(db, client_id, task_id)
        assert db is not None
        plan = ledger.versions(db, task)[-1]
        state, error = "not_checked", None
        if task["state"] == "prepared":
            try:
                preparation.check(config, main, client_id, plan)
                state = "current"
            except auth.Denied:
                state, error = "unavailable", "input_not_authorized"
            except IntegrityError:
                state, error = "changed", "input_changed"
            except InvalidInput:
                state, error = "unavailable", "input_task_not_ready"
        result = {
            "task_id": task["id"],
            "query_task_id": task["query_task_id"],
            "state": task["state"],
            "revision": task["head_revision"],
            "plan_sha256": task["head_sha256"],
            "input_sha256": task["input_sha256"],
            "input_state": state,
            "error_code": error,
            "unverified_draft": True,
            "execution_allowed": False,
            "delivery_allowed": False,
            "save_allowed": False,
        }
        execution = execution_binding.get(main, task_id)
        if execution is not None:
            if execution["client_id"] != client_id:
                raise IntegrityError("document_execution_binding_invalid")
            result["execution"] = execution_binding.report(main, execution)
        return result


def submit(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    submission_id: str,
    query_task_id: str,
    requirements: str,
    requested_format: str,
) -> dict[str, Any]:
    _active(main, client_id)
    text(submission_id, limit=400)
    text(query_task_id, limit=128)
    text(requested_format, limit=32)
    contract.requirement(requirements)
    fingerprint = ledger.submission(query_task_id, requirements, requested_format)
    with ledger.connection(config) as db:
        previous = (
            None
            if db is None
            else db.execute(
                "SELECT * FROM tasks WHERE client_id=? AND submission_id=?",
                (client_id, submission_id),
            ).fetchone()
        )
        if previous is not None:
            assert db is not None
            ledger.versions(db, previous)
            if previous["submission_sha256"] != fingerprint:
                raise InvalidInput("document_submission_conflict")
            return status(config, main, client_id, previous["id"])
    plan = preparation.prepare(
        config, main, client_id, query_task_id, requirements, requested_format
    )
    task_id, now = str(uuid4()), time.time()
    with ledger.connection(config, write=True, create=True) as db:
        assert db is not None
        with database.transaction(db):
            if (
                db.execute("SELECT COUNT(*) FROM tasks WHERE client_id=?", (client_id,)).fetchone()[
                    0
                ]
                >= ledger.MAX_TASKS
            ):
                raise InvalidInput("document_task_limit")
            db.execute(
                "INSERT INTO tasks VALUES(?,?,?,?,?,?,1,?,'prepared',?,?)",
                (
                    task_id,
                    client_id,
                    submission_id,
                    fingerprint,
                    query_task_id,
                    digest(plan.input_json.encode()),
                    plan.sha256,
                    now,
                    now,
                ),
            )
            ledger.append(db, task_id, plan, now, "submitted")
    return status(config, main, client_id, task_id)


def plan(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    task_id: str,
    revision: int | None = None,
) -> contract.CreationPlan:
    _active(main, client_id)
    with ledger.connection(config) as db:
        task = _owned(db, client_id, task_id)
        assert db is not None
        if task["state"] != "prepared":
            raise InvalidInput("document_task_not_prepared")
        values = ledger.versions(db, task)
        revision = len(values) if revision is None else revision
        if type(revision) is not int or not 1 <= revision <= len(values):
            raise InvalidInput("document_revision_invalid")
        value = values[revision - 1]
        preparation.check(config, main, client_id, value)
        return value


def history(
    config: Config, main: sqlite3.Connection, client_id: str, task_id: str
) -> list[dict[str, Any]]:
    _active(main, client_id)
    with ledger.connection(config) as db:
        task = _owned(db, client_id, task_id)
        assert db is not None
        return [
            {
                "revision": p.revision,
                "plan_sha256": p.sha256,
                "parent_sha256": p.parent_sha256,
                "requirement_count": len(p.requirements),
                "apply_at": p.apply_at,
            }
            for p in ledger.versions(db, task)
        ]


def amend(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    task_id: str,
    addition: str,
    *,
    expected_revision: int,
    expected_sha256: str,
) -> dict[str, Any]:
    _active(main, client_id)
    contract.requirement(addition)
    with ledger.connection(config, write=True) as db:
        task = _owned(db, client_id, task_id)
        assert db is not None
        with database.transaction(db):
            task = _owned(db, client_id, task_id)
            values = ledger.versions(db, task)
            _expected(task, expected_revision, expected_sha256)
            if task["state"] != "prepared":
                raise InvalidInput("document_task_not_prepared")
            if execution_binding.get(main, task_id) is not None:
                raise InvalidInput("document_task_execution_bound; continuation not yet supported")
            updated = preparation.amend(
                config,
                main,
                client_id,
                values[-1],
                addition,
                expected_sha256=expected_sha256,
                phase="prepared",
            )
            now = time.time()
            changed = db.execute(
                "UPDATE tasks SET head_revision=?,head_sha256=?,updated_at=? "
                "WHERE id=? AND head_revision=? AND head_sha256=? AND state='prepared'",
                (
                    updated.revision,
                    updated.sha256,
                    now,
                    task_id,
                    expected_revision,
                    expected_sha256,
                ),
            )
            if changed.rowcount != 1:
                raise InvalidInput("document_plan_conflict")
            ledger.append(db, task_id, updated, now, "amended")
    return status(config, main, client_id, task_id)


def cancel(
    config: Config,
    main: sqlite3.Connection,
    client_id: str,
    task_id: str,
    *,
    expected_revision: int,
    expected_sha256: str,
) -> dict[str, Any]:
    """Cancellation can reduce intent even after its former input permission is revoked."""
    _active(main, client_id)
    with ledger.connection(config, write=True) as db:
        task = _owned(db, client_id, task_id)
        assert db is not None
        with database.transaction(db):
            task = _owned(db, client_id, task_id)
            value = ledger.versions(db, task)[-1]
            _expected(task, expected_revision, expected_sha256)
            execution = execution_binding.get(main, task_id)
            if execution is not None:
                if execution["client_id"] != client_id:
                    raise IntegrityError("document_execution_binding_invalid")
                execution_binding.stop(main, execution)
            if task["state"] == "prepared":
                now = time.time()
                changed = db.execute(
                    "UPDATE tasks SET state='cancelled',updated_at=? WHERE id=? "
                    "AND head_revision=? AND head_sha256=? AND state='prepared'",
                    (now, task_id, expected_revision, expected_sha256),
                )
                if changed.rowcount != 1:
                    raise InvalidInput("document_plan_conflict")
                ledger.event(db, task_id, value, now, "cancelled", "cancelled")
    return status(config, main, client_id, task_id)
