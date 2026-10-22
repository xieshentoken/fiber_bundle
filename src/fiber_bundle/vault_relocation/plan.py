"""Administrator previews freeze identities and facts, without moving or pausing data."""

import json
import os
import shutil
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import database, vault_scope
from ..config import Config
from ..inputs import canonical
from ..vault_merge_guard import check as operation_check
from . import evidence, files, guard, store


def vault(db: Any, identity: str) -> dict[str, Any]:
    row = db.execute("SELECT * FROM vaults WHERE id=?", (identity,)).fetchone()
    if row is None:
        files.reject("unavailable")
    result = dict(row)
    if result["status"] != "active" or result["attachment_state"] != "attached":
        files.reject("unavailable")
    if result["root_kind"] not in {"external", "managed"}:
        files.reject("legacy_unsupported")
    return result


def conflicts(db: Any, identity: str) -> None:
    operation_check(db, identity)
    for row in db.execute("SELECT kind,state FROM vault_operations WHERE vault_id=?", (identity,)):
        if row["kind"] != "vault_relocation" and row["state"] in {
            "prepared",
            "launch_requested",
            "running",
            "committing",
            "reconcile",
            "trash_pending",
        }:
            files.reject("operation_conflict")


def busy(config: Config, db: Any, identity: str) -> bool:
    """Only executing work blocks; durable queues stay queued without cancelling history."""
    for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        name = '"' + table.replace('"', '""') + '"'
        cols = {r[1] for r in db.execute("PRAGMA table_info(" + name + ")")}
        if "state" not in cols or table == "vault_operations":
            continue
        states = ("running", "reserved", "claimed", "committing", "publishing", "processing")
        if (
            "vault_id" in cols
            and db.execute(
                "SELECT 1 FROM " + name + " WHERE vault_id=? AND state IN (?,?,?,?,?,?) LIMIT 1",
                (identity, *states),
            ).fetchone()
        ):
            return True
        if (
            "roots_json" in cols
            and db.execute(
                "SELECT 1 FROM " + name + " WHERE state IN (?,?,?,?,?,?) AND "
                "EXISTS(SELECT 1 FROM json_each(roots_json) WHERE "
                "json_extract(value,'$.vault_id')=?) LIMIT 1",
                (*states, identity),
            ).fetchone()
        ):
            return True
    from ..channels import store as channels

    with channels.readonly(config) as side:
        if (
            side is not None
            and side.execute(
                "SELECT 1 FROM events e JOIN channels c ON c.id=e.channel_id "
                "WHERE (json_extract(c.value,'$.vault_id')=? OR EXISTS(SELECT 1 "
                "FROM json_each(json_extract(c.value,'$.query_vaults')) q "
                "WHERE json_extract(q.value,'$.vault_id')=?)) AND e.state IN "
                "('processing','gui_processing','sending') LIMIT 1",
                (identity, identity),
            ).fetchone()
        ):
            return True
    for filename in ("knowledge-graph.sqlite3", "organization.sqlite3"):
        import sqlite3

        path = config.state_root / filename
        if path.exists():
            with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as side:
                for (table,) in side.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                    name = '"' + table.replace('"', '""') + '"'
                    cols = {r[1] for r in side.execute("PRAGMA table_info(" + name + ")")}
                    if {"vault", "value"} <= cols and side.execute(
                        "SELECT 1 FROM " + name + " WHERE vault=? AND "
                        "json_extract(value,'$.state') IN "
                        "('running','claimed','publishing','processing') LIMIT 1",
                        (identity,),
                    ).fetchone():
                        return True
                    if {"vault_id", "state"} <= cols and side.execute(
                        "SELECT 1 FROM " + name + " WHERE vault_id=? AND "
                        "state IN ('running','claimed') LIMIT 1",
                        (identity,),
                    ).fetchone():
                        return True
    return False


def parent_policy(path: Path) -> str:
    return vault_scope.effective(path.parent, {"include": [""], "exclude": []}).policy_sha256


def preview(
    config: Config, identity: str, destination: str, *, icloud: bool = False
) -> dict[str, Any]:
    with database.locked(config), database.connect(config.state_root) as db:
        row = vault(db, identity)
        conflicts(db, identity)
        source, target = files.target(config, db, row, destination, icloud=icloud)
        source_key = files.key(source)
        if source_key != [row["directory_dev"], row["directory_ino"]]:
            files.reject("directory_changed")
        location_revision = guard.revision(db, identity)
        preserved = evidence.preservation(config, db, identity)
        if busy(config, db, identity):
            files.reject("busy")
    options: dict[str, Any] = {}
    if icloud:
        from .icloud import scopes

        effective, destination_scope = scopes(source, target, row)
        options = {
            "icloud": True,
            "effective_scope": effective,
            "destination_scope_json": destination_scope,
        }
    observed = files.inventory(
        source, icloud=icloud, excluded=tuple(options.get("effective_scope", {}).get("exclude", []))
    )
    size = sum(f.get("bytes", 0) for f in observed)
    if shutil.disk_usage(config.state_root).free < size + 16 * 1024**2:
        files.reject("space_required")
    now = time.time()
    plan = {
        **options,
        "vault": row,
        "location_revision": location_revision,
        "source": str(source),
        "destination": str(target),
        "root_key": source_key,
        "source_parent": files.key(source.parent),
        "target_parent": files.key(target.parent),
        "source_policy": parent_policy(source),
        "target_policy": parent_policy(target),
        "files": observed,
        "preservation": preserved,
        "summary": evidence.summary(preserved),
        "expires_at": now + 600,
    }
    with database.locked(config), database.connect(config.state_root) as db:
        if vault(db, identity) != row or evidence.preservation(config, db, identity) != preserved:
            files.reject("preview_changed")
        conflicts(db, identity)
        operation = str(uuid4())
        value = {
            "plan": plan,
            "plan_sha256": store.digest(plan),
            "completed_phases": [],
            "location_revision": location_revision,
        }
        db.execute(
            "INSERT INTO vault_operations VALUES(?,?,?,?,?,?,?)",
            (operation, identity, "vault_relocation", "prepared", canonical(value), now, now),
        )
        return store.view(db, operation)


def current(config: Config, db: Any, value: dict[str, Any], path: Path) -> None:
    p = value["plan"]
    row = vault(db, p["vault"]["id"])
    original = dict(p["vault"])
    actual = row.pop("absolute_root")
    original.pop("absolute_root")
    if p.get("icloud") and actual == p["destination"]:
        original["scope_json"] = p["destination_scope_json"]
    if guard.revision(db, row["id"]) != value["location_revision"]:
        files.reject("location_changed")
    if row != original or actual not in {p["source"], p["destination"]}:
        files.reject("bindings_changed")
    if evidence.preservation(config, db, row["id"]) != p["preservation"]:
        files.reject("bindings_changed")
    if (
        files.key(Path(p["source"]).parent) != p["source_parent"]
        or files.key(Path(p["destination"]).parent) != p["target_parent"]
    ):
        files.reject("parent_changed")
    if (
        parent_policy(Path(p["source"])) != p["source_policy"]
        or parent_policy(Path(p["destination"])) != p["target_policy"]
    ):
        files.reject("policy_changed")
    configured_scope = (
        p.get("destination_scope_json")
        if p.get("icloud") and str(path) == p["destination"]
        else row["scope_json"]
    )
    scope = vault_scope.effective(path, json.loads(configured_scope))
    if p.get("icloud"):
        if scope.wire() != p["effective_scope"]:
            files.reject("policy_changed")
    elif scope.exclude or scope.include != ("",):
        files.reject("policy_changed")
    if files.key(path) != p["root_key"]:
        files.reject("directory_changed")


def where(value: dict[str, Any]) -> Path:
    p = value["plan"]
    found = []
    for raw in (p["source"], p["destination"]):
        path = Path(raw)
        if os.path.lexists(path) and not path.is_symlink() and path.is_dir():
            if files.key(path) == p["root_key"]:
                found.append(path)
    if len(found) != 1:
        files.reject("directory_changed")
    return found[0]
