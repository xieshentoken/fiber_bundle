"""Explicit configuration changes and query-only lifecycle; no Agent/model execution."""

import json
import sqlite3
import time
from typing import Any
from uuid import uuid4

from .. import auth, database
from ..config import Config
from ..inputs import canonical
from . import files, store
from .contracts import ConnectionSpec


def pause_grants(db: sqlite3.Connection, cid: str) -> None:
    db.execute(
        "UPDATE client_vault_grants SET can_query=0,can_original=0,can_add=0 WHERE client_id=?",
        (cid,),
    )


def create(
    config: Config, db: sqlite3.Connection, value: ConnectionSpec, request_id: str
) -> dict[str, Any]:
    prior = db.execute(
        "SELECT * FROM agent_connection_operations WHERE kind='create' AND request_id=?",
        (request_id,),
    ).fetchone()
    if prior:
        if json.loads(prior["payload_json"]) != {"configuration": value.model_dump()}:
            raise store.Conflict("agent_request_conflict")
        return store.public(config, db, store.get(db, prior["connection_id"]))
    store.validate_scope(config, db, value)
    if db.execute("SELECT count(*) FROM agent_connections").fetchone()[0] >= 50:
        raise store.Conflict("agent_connection_limit")
    identity = str(uuid4())
    now = time.time()
    with database.transaction(db):
        client = auth.create_client(db, value.name, False)
        for vid in value.vault_ids:
            auth.set_grant(db, client["id"], vid, query=False, original=False, add=False)
        db.execute(
            "INSERT INTO agent_connections VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identity,
                client["id"],
                canonical(value.model_dump()),
                value.digest,
                1,
                1,
                "repair_required",
                canonical(store.epochs(db, client["id"])),
                None,
                None,
                None,
                now,
                now,
            ),
        )
        intent = store.operation(
            db, identity, "create", request_id, {"configuration": value.model_dump()}
        )
    files.put(config, identity, "credential.json", {"token": client["token"]}, secret=True)
    with database.transaction(db):
        db.execute("UPDATE agent_connections SET state='paused' WHERE id=?", (identity,))
        store.finish(db, intent["id"], "succeeded")
    return store.public(config, db, store.get(db, identity))


def save(
    config: Config,
    db: sqlite3.Connection,
    identity: str,
    value: ConnectionSpec,
    expected: int,
    request_id: str,
) -> dict[str, Any]:
    row = store.get(db, identity)
    payload = {"configuration": value.model_dump(), "expected_revision": expected}
    prior = db.execute(
        "SELECT * FROM agent_connection_operations WHERE connection_id=? AND request_id=?",
        (identity, request_id),
    ).fetchone()
    if prior:
        if json.loads(prior["payload_json"]) != payload or prior["kind"] != "save":
            raise store.Conflict("agent_request_conflict")
        return store.public(config, db, row)
    store.cas(row, expected)
    store.validate_scope(config, db, value)
    with database.transaction(db):
        intent = store.operation(db, identity, "save", request_id, payload)
        pause_grants(db, row["client_id"])
        for vid in value.vault_ids:
            auth.set_grant(db, row["client_id"], vid, query=False, original=False, add=False)
        db.execute(
            "UPDATE agent_connections SET configuration_json=?,"
            "configuration_sha256=?,revision=revision+1,"
            "state='paused',paused_epochs_json=?,applied_json=NULL,"
            "probe_json=NULL,test_json=NULL,updated_at=? WHERE id=?",
            (
                canonical(value.model_dump()),
                value.digest,
                canonical(store.epochs(db, row["client_id"])),
                time.time(),
                identity,
            ),
        )
        db.execute("UPDATE clients SET name=? WHERE id=?", (value.name, row["client_id"]))
        store.finish(db, intent["id"], "succeeded")
    return store.public(config, db, store.get(db, identity))


def control(
    config: Config,
    db: sqlite3.Connection,
    identity: str,
    action: str,
    expected: int,
    request_id: str,
) -> dict[str, Any]:
    row = store.get(db, identity)
    payload = {"expected_revision": expected}
    prior = db.execute(
        "SELECT * FROM agent_connection_operations WHERE connection_id=? AND request_id=?",
        (identity, request_id),
    ).fetchone()
    if prior:
        if prior["kind"] != action or json.loads(prior["payload_json"]) != payload:
            raise store.Conflict("agent_request_conflict")
        return store.public(config, db, row)
    store.cas(row, expected)
    value = store.spec(row)
    with database.transaction(db):
        intent = store.operation(db, identity, action, request_id, payload)
        if action == "enable":
            applied = json.loads(row["applied_json"]) if row["applied_json"] else None
            if (
                not applied
                or applied["revision"] != expected
                or applied["credential_generation"] != row["credential_generation"]
            ):
                raise store.Conflict("agent_apply_required")
            if canonical(store.epochs(db, row["client_id"])) != row["paused_epochs_json"]:
                raise store.Conflict("agent_permissions_changed")
            if (
                auth.digest(files.credential(config, identity))
                != db.execute(
                    "SELECT token_digest FROM clients WHERE id=?", (row["client_id"],)
                ).fetchone()[0]
            ):
                raise store.Conflict("agent_credential_changed")
            store.validate_scope(config, db, value)
            for vid in value.vault_ids:
                auth.set_grant(
                    db, row["client_id"], vid, query=True, original=value.allow_originals, add=False
                )
            state = "enabled"
        elif action == "pause":
            pause_grants(db, row["client_id"])
            state = "paused"
        elif action == "revoke":
            auth.revoke_client(db, row["client_id"])
            state = "revoked"
        else:
            raise store.Conflict("agent_action_invalid")
        db.execute(
            "UPDATE agent_connections SET state=?,revision=revision+1,"
            "paused_epochs_json=?,probe_json=NULL,test_json=NULL,updated_at=? "
            "WHERE id=?",
            (state, canonical(store.epochs(db, row["client_id"])), time.time(), identity),
        )
        # Lifecycle does not change the applied config, but binds it to the new revision.
        if row["applied_json"] and action != "revoke":
            applied = json.loads(row["applied_json"])
            applied["revision"] = expected + 1
            db.execute(
                "UPDATE agent_connections SET applied_json=? WHERE id=?",
                (canonical(applied), identity),
            )
        db.execute(
            "UPDATE agent_connection_operations SET cancel_requested=1 WHERE "
            "connection_id=? AND state IN ('queued','running') AND id!=?",
            (identity, intent["id"]),
        )
        store.finish(db, intent["id"], "succeeded")
    return store.public(config, db, store.get(db, identity))


def rotate(
    config: Config, db: sqlite3.Connection, identity: str, expected: int, request_id: str
) -> dict[str, Any]:
    row = store.get(db, identity)
    prior = db.execute(
        "SELECT * FROM agent_connection_operations WHERE connection_id=? AND request_id=?",
        (identity, request_id),
    ).fetchone()
    if prior:
        if prior["kind"] != "rotate_credential" or json.loads(prior["payload_json"]) != {
            "expected_revision": expected
        }:
            raise store.Conflict("agent_request_conflict")
        return store.op_view(prior)
    store.cas(row, expected)
    with database.transaction(db):
        intent = store.operation(
            db, identity, "rotate_credential", request_id, {"expected_revision": expected}
        )
        if intent["state"] != "queued":
            return store.op_view(intent)
        auth.revoke_client(db, row["client_id"])
        client = auth.create_client(db, store.spec(row).name, False)
        for vid in store.spec(row).vault_ids:
            auth.set_grant(db, client["id"], vid, query=False, original=False, add=False)
        db.execute(
            "UPDATE agent_connections SET client_id=?,"
            "credential_generation=credential_generation+1,revision=revision+1,"
            "state='repair_required',"
            "paused_epochs_json=?,applied_json=NULL,probe_json=NULL,"
            "test_json=NULL,updated_at=? WHERE id=?",
            (client["id"], canonical(store.epochs(db, client["id"])), time.time(), identity),
        )
    files.put(config, identity, "credential.json", {"token": client["token"]}, secret=True)
    with database.transaction(db):
        db.execute("UPDATE agent_connections SET state='paused' WHERE id=?", (identity,))
        store.finish(
            db,
            intent["id"],
            "succeeded",
            {"credential_generation": row["credential_generation"] + 1},
        )
    return store.op_view(store.op(db, identity, intent["id"]))


def cancel(
    db: sqlite3.Connection, identity: str, oid: str, expected: int, request_id: str
) -> dict[str, Any]:
    payload = {"expected_revision": expected, "operation_id": oid}
    prior = db.execute(
        "SELECT * FROM agent_connection_operations WHERE connection_id=? AND request_id=?",
        (identity, request_id),
    ).fetchone()
    if prior:
        if prior["kind"] != "cancel" or json.loads(prior["payload_json"]) != payload:
            raise store.Conflict("agent_request_conflict")
        return store.op_view(store.op(db, identity, oid))
    store.cas(store.get(db, identity), expected)
    target = store.op(db, identity, oid)
    with database.transaction(db):
        intent = store.operation(db, identity, "cancel", request_id, payload)
        if target["state"] in ("queued", "running"):
            db.execute(
                "UPDATE agent_connection_operations SET cancel_requested=1,updated_at=? WHERE id=?",
                (time.time(), oid),
            )
        store.finish(db, intent["id"], "succeeded", {"operation_id": oid})
    return store.op_view(store.op(db, identity, oid))
