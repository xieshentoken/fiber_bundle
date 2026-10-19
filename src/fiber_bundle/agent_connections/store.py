"""Bounded ledger access and current connection projections; callers hold storage lease."""

import hashlib
import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .. import auth, database, vaults
from ..config import Config, InvalidInput
from ..inputs import canonical
from .contracts import ConnectionContext, ConnectionSpec


class Conflict(Exception):
    pass


def get(db: sqlite3.Connection, identity: str) -> sqlite3.Row:
    row = db.execute("SELECT * FROM agent_connections WHERE id=?", (identity,)).fetchone()
    if row is None:
        raise auth.Denied(404)
    if not isinstance(row, sqlite3.Row):
        raise InvalidInput("agent_row_invalid")
    return row


def spec(row: sqlite3.Row) -> ConnectionSpec:
    return ConnectionSpec.model_validate_json(row["configuration_json"])


def epochs(db: sqlite3.Connection, cid: str) -> dict[str, Any]:
    return {
        r["vault_id"]: dict(r)
        for r in db.execute(
            "SELECT g.*,e.revision AS epoch,v.status,v.attachment_state FROM client_vault_grants g "
            "JOIN graph_permission_epochs e ON e.client_id=g.client_id AND e.vault_id=g.vault_id "
            "JOIN vaults v ON v.id=g.vault_id WHERE g.client_id=? ORDER BY g.vault_id",
            (cid,),
        )
    }


def context(config: Config, db: sqlite3.Connection, identity: str) -> dict[str, Any]:

    row = get(db, identity)
    value = spec(row)
    active = db.execute("SELECT active FROM clients WHERE id=?", (row["client_id"],)).fetchone()
    permitted = True
    for vid in value.vault_ids:
        try:
            auth.grant(db, row["client_id"], vid, "query")
        except auth.Denied:
            permitted = False
    from .models import binding_digest

    model_digest = (
        binding_digest(config, value.model_configuration_id)
        if value.model_configuration_id
        else None
    )
    runtime_sha = None
    if row["applied_json"] and value.agent_type == "hermes":
        from . import installation

        try:
            runtime_sha = hashlib.sha256(canonical(installation.current()).encode()).hexdigest()
        except (OSError, InvalidInput):
            runtime_sha = "unavailable"
    binding = {
        "epochs": epochs(db, row["client_id"]),
        "active": bool(active and active[0]),
        "model_configuration_sha256": model_digest,
        "applied": row["applied_json"],
        "runtime_sha256": runtime_sha,
    }
    result = ConnectionContext(
        connection_id=identity,
        client_id=row["client_id"],
        revision=row["revision"],
        credential_generation=row["credential_generation"],
        authorization_sha256=hashlib.sha256(canonical(binding).encode()).hexdigest(),
        configuration_sha256=row["configuration_sha256"],
        enabled=row["state"] == "enabled" and bool(active and active[0]) and permitted,
        configuration=value,
    )
    return result.model_dump()


def public(config: Config, db: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    ctx = context(config, db, row["id"])
    applied = json.loads(row["applied_json"]) if row["applied_json"] else None
    current = bool(
        applied
        and applied["revision"] == row["revision"]
        and applied["credential_generation"] == row["credential_generation"]
    )
    checks = {}
    for kind in ("probe", "test"):
        saved = json.loads(row[kind + "_json"]) if row[kind + "_json"] else None
        checks[kind] = (
            saved
            if saved and saved.get("context") == ctx
            else {"state": "outdated" if saved else "not_run"}
        )
    if current and spec(row).model_configuration_id:

        from .models import binding_digest

        model_digest = binding_digest(config, spec(row).model_configuration_id)
        current = bool(
            model_digest and applied and applied["model_configuration_sha256"] == model_digest
        )
    if current and spec(row).agent_type == "hermes":
        from . import installation

        try:
            current = bool(applied and applied["hermes"] == installation.current())
        except (OSError, InvalidInput):
            current = False
    saved_test = checks["test"]
    if saved_test.get("state") == "passed":
        from . import evidence_verification, files

        try:
            document = files.private_json(
                files.folder(config, row["id"]) / (saved_test["operation_id"] + ".answer.json"),
                262144,
            )
            if not isinstance(document, dict):
                raise InvalidInput("agent_result_invalid")
            evidence_verification.verify(
                config, db, row["client_id"], ctx, document["answer"]["sources"]
            )
        except Exception:
            checks["test"] = {"state": "outdated"}
    state = row["state"]
    if state == "enabled" and not ctx["enabled"]:
        state = "repair_required"
    return {
        "id": row["id"],
        "configuration": ctx["configuration"],
        "revision": row["revision"],
        "credential_generation": row["credential_generation"],
        "configuration_sha256": row["configuration_sha256"],
        "client_id": row["client_id"],
        "access_state": state,
        "configuration_state": (
            "repair_required"
            if row["state"] == "repair_required"
            else "outdated"
            if applied and not current
            else "external_configuration_required"
            if current and spec(row).agent_type == "mcp"
            else "applied"
            if current
            else "pending"
        ),
        "applied": applied if current else None,
        **checks,
    }


def cas(row: sqlite3.Row, expected: int) -> None:
    if row["revision"] != expected:
        raise Conflict("agent_revision_conflict")
    if row["state"] == "revoked":
        raise Conflict("agent_revoked")


def validate_scope(config: Config, db: sqlite3.Connection, value: ConnectionSpec) -> None:
    for vid in value.vault_ids:
        row = db.execute(
            "SELECT * FROM vaults WHERE id=? AND status='active' AND attachment_state='attached'",
            (vid,),
        ).fetchone()
        if row is None:
            raise auth.Denied(404)
        vaults.checked_vault(config, row)


def operation(
    db: sqlite3.Connection, identity: str, kind: str, request_id: str, payload: dict[str, Any]
) -> sqlite3.Row:
    row = get(db, identity)
    sha = hashlib.sha256(canonical({"kind": kind, "payload": payload}).encode()).hexdigest()
    old = db.execute(
        "SELECT * FROM agent_connection_operations WHERE connection_id=? AND request_id=?",
        (identity, request_id),
    ).fetchone()
    if old:
        if old["request_sha256"] != sha:
            raise Conflict("agent_request_conflict")
        return op(db, identity, old["id"])
    now = time.time()
    oid = str(uuid4())
    db.execute(
        "INSERT INTO agent_connection_operations "
        "(id,connection_id,kind,request_id,request_sha256,revision,"
        "credential_generation,state,payload_json,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            oid,
            identity,
            kind,
            request_id,
            sha,
            row["revision"],
            row["credential_generation"],
            "queued",
            canonical(payload),
            now,
            now,
        ),
    )
    return op(db, identity, oid)


def op(db: sqlite3.Connection, identity: str, oid: str) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM agent_connection_operations WHERE id=? AND connection_id=?", (oid, identity)
    ).fetchone()
    if row is None:
        raise auth.Denied(404)
    if not isinstance(row, sqlite3.Row):
        raise InvalidInput("agent_row_invalid")
    return row


def op_view(row: sqlite3.Row) -> dict[str, Any]:
    return {
        key: row[key]
        for key in (
            "id",
            "connection_id",
            "kind",
            "revision",
            "state",
            "cancel_requested",
            "error_code",
            "created_at",
            "updated_at",
        )
    } | {"result": json.loads(row["result_json"]) if row["result_json"] else None}


def finish(
    db: sqlite3.Connection, oid: str, state: str, result: object = None, error: str | None = None
) -> None:
    db.execute(
        "UPDATE agent_connection_operations SET state=?,result_json=?,"
        "error_code=?,updated_at=? WHERE id=?",
        (state, canonical(result) if result is not None else None, error, time.time(), oid),
    )


def recover(config: Config) -> None:
    # No replay or PID guessing after a host crash. A later explicit apply/rotate repairs it.
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            with database.transaction(db):
                db.execute(
                    "UPDATE agent_connections SET state='repair_required',"
                    "applied_json=NULL,probe_json=NULL,test_json=NULL "
                    "WHERE id IN (SELECT connection_id FROM agent_connection_operations "
                    "WHERE state IN ('queued','running'))"
                )
                db.execute(
                    "UPDATE client_vault_grants SET can_query=0 WHERE client_id IN "
                    "(SELECT client_id FROM agent_connections WHERE "
                    "state='repair_required')"
                )
                db.execute(
                    "UPDATE agent_connection_operations SET state='reconcile',"
                    "error_code='agent_interrupted',updated_at=? WHERE state IN "
                    "('queued','running')",
                    (time.time(),),
                )
        finally:
            db.close()
