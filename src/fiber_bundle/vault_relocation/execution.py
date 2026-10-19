"""Finite, journaled moves. Neither rollback nor recovery restores a whole shared DB."""

import os
import sqlite3
import time
from pathlib import Path
from typing import Any

from .. import database
from ..config import Config, StorageError
from ..executor_container import run_lock
from . import evidence, files, guard, plan, store

ACTIVE = frozenset({"quiescing", "checkpointing", "moving", "switching", "verifying", "restoring"})


def view(config: Config, identity: str) -> dict[str, Any]:
    with database.connect(config.state_root, readonly=True) as db:
        return store.view(db, identity)


def start(config: Config, identity: str, sha: str) -> dict[str, Any]:
    with run_lock(config, identity):
        with database.locked(config), database.connect(config.state_root) as db:
            value, state = store.load(db, identity)
            if value["plan_sha256"] != sha:
                files.reject("preview_changed")
            # Repeated submission of the same intent never starts a second move.
            if state != "prepared":
                return store.view(db, identity)
            p = value["plan"]
            if p["expires_at"] < time.time():
                files.reject("preview_expired")
            plan.conflicts(db, p["vault"]["id"])
            plan.current(config, db, value, Path(p["source"]))
            files.target(config, db, p["vault"], p["destination"])
            if plan.busy(config, db, p["vault"]["id"]):
                files.reject("busy")
            value["phase"] = "quiescing"
            store.save(db, identity, value, "quiescing")
        return view(config, identity)


def step(config: Config, identity: str) -> dict[str, Any]:
    with run_lock(config, identity), guard.executing(identity):
        try:
            return _step(config, identity)
        except (StorageError, OSError, sqlite3.Error) as exc:
            with database.locked(config), database.connect(config.state_root) as db:
                value, state = store.load(db, identity)
                if state in ACTIVE:
                    code = str(exc)
                    value["reason"] = (
                        code if code.startswith("relocation_") else "relocation_storage_unavailable"
                    )
                    store.save(db, identity, value, "needs_recovery")
            return view(config, identity)


def _step(config: Config, identity: str) -> dict[str, Any]:
    with database.connect(config.state_root) as db:
        value, state = store.load(db, identity)
    if state not in ACTIVE:
        return view(config, identity)
    p = value["plan"]
    path = plan.where(value)
    with database.locked(config), database.connect(config.state_root) as db:
        plan.current(config, db, value, path)
        if plan.busy(config, db, p["vault"]["id"]):
            value["reason"] = "relocation_busy"
            store.save(db, identity, value, state)
            return store.view(db, identity)
    if state == "quiescing":
        if files.inventory(path) != p["files"]:
            files.reject("preview_changed")
        following = "checkpointing"
    elif state == "checkpointing":
        value["checkpoint"] = evidence.checkpoint(config, path, p, identity)
        if files.inventory(path) != p["files"]:
            files.reject("source_changed")
        following = "moving"
    elif state == "moving":
        if not value.get("checkpoint", {}).get("verified"):
            files.reject("checkpoint_invalid")
        evidence.verify_checkpoint(value["checkpoint"], p)
        if files.inventory(path) != p["files"]:
            files.reject("source_changed")
        if str(path) == p["source"]:
            with database.locked(config), database.connect(config.state_root) as db:
                plan.current(config, db, value, path)
                files.target(config, db, p["vault"], p["destination"])
                files.move(
                    path,
                    Path(p["destination"]),
                    p["source_parent"],
                    p["target_parent"],
                    p["root_key"],
                )
        value["current_path"] = p["destination"]
        following = "switching"
    elif state == "switching":
        if str(path) != p["destination"]:
            files.reject("directory_changed")
        with database.locked(config), database.connect(config.state_root) as db:
            plan.current(config, db, value, path)
            previous_root = db.execute(
                "SELECT absolute_root FROM vaults WHERE id=?", (p["vault"]["id"],)
            ).fetchone()[0]
            with database.transaction(db):
                db.execute(
                    "UPDATE vaults SET absolute_root=? WHERE id=?",
                    (p["destination"], p["vault"]["id"]),
                )
                if previous_root != p["destination"]:
                    value["location_revision"] += 1
                value["current_path"] = p["destination"]
                value["phase"] = "verifying"
                value["completed_phases"].append(state)
                store.save(db, identity, value, "verifying")
        return view(config, identity)
    elif state == "verifying":
        if str(path) != p["destination"] or files.inventory(path) != p["files"]:
            files.reject("source_changed")
        following = "complete"
    elif state == "restoring":
        if files.inventory(path) != p["files"]:
            files.reject("source_changed")
        if str(path) != p["source"]:
            files.move(
                path, Path(p["source"]), p["target_parent"], p["source_parent"], p["root_key"]
            )
        if files.inventory(Path(p["source"])) != p["files"]:
            files.reject("source_changed")
        value["current_path"] = p["source"]
        with database.locked(config), database.connect(config.state_root) as db:
            plan.current(config, db, value, Path(p["source"]))
            previous_root = db.execute(
                "SELECT absolute_root FROM vaults WHERE id=?", (p["vault"]["id"],)
            ).fetchone()[0]
            with database.transaction(db):
                db.execute(
                    "UPDATE vaults SET absolute_root=? WHERE id=?", (p["source"], p["vault"]["id"])
                )
                if previous_root != p["source"]:
                    value["location_revision"] += 1
                value["completed_phases"].append("restoring")
                store.save(db, identity, value, "restored")
        return view(config, identity)
    else:
        return view(config, identity)
    with database.locked(config), database.connect(config.state_root) as db:
        latest, latest_state = store.load(db, identity)
        if latest_state != state:
            files.reject("state_changed")
        plan.current(config, db, value, plan.where(value))
        value["completed_phases"].append(state)
        value["phase"] = following
        value.pop("reason", None)
        store.save(db, identity, value, following)
    return view(config, identity)


def stop(config: Config, identity: str, sha: str) -> dict[str, Any]:
    with (
        run_lock(config, identity),
        database.locked(config),
        database.connect(config.state_root) as db,
    ):
        value, state = store.load(db, identity)
        if sha != value["plan_sha256"]:
            files.reject("preview_changed")
        if state == "prepared":
            store.save(db, identity, value, "cancelled")
        elif state in ACTIVE:
            value["reason"] = "relocation_stopped"
            store.save(db, identity, value, "needs_recovery")
        return store.view(db, identity)


def recovery_preview(config: Config, identity: str) -> dict[str, Any]:
    with run_lock(config, identity), guard.executing(identity):
        with database.connect(config.state_root) as db:
            value, state = store.load(db, identity)
        if state != "needs_recovery":
            files.reject("state_changed")
        decision: dict[str, Any] = {"resume": False, "restore": False, "reason": None}
        try:
            path = plan.where(value)
            with database.locked(config), database.connect(config.state_root) as db:
                plan.current(config, db, value, path)
                if plan.busy(config, db, value["plan"]["vault"]["id"]):
                    files.reject("busy")
            if files.inventory(path) != value["plan"]["files"]:
                files.reject("source_changed")
            p = value["plan"]
            decision.update(
                current_path=str(path),
                resume=True,
                restore=str(path) == p["source"] or not os.path.lexists(p["source"]),
            )
            if not decision["restore"]:
                decision["reason"] = "relocation_target_exists"
        except (StorageError, OSError, sqlite3.Error) as exc:
            decision["reason"] = (
                str(exc) if str(exc).startswith("relocation_") else "relocation_storage_unavailable"
            )
        decision["checked_at"] = time.time()
        decision["expires_at"] = time.time() + 120
        decision["sha256"] = store.digest(decision)
        with database.locked(config), database.connect(config.state_root) as db:
            latest, latest_state = store.load(db, identity)
            if latest_state != state or latest != value:
                files.reject("state_changed")
            value["recovery"] = decision
            store.save(db, identity, value, state)
            return store.view(db, identity)


def recover(config: Config, identity: str, action: str, sha: str) -> dict[str, Any]:
    with run_lock(config, identity), guard.executing(identity):
        with database.connect(config.state_root) as db:
            value, state = store.load(db, identity)
        decision = value.get("recovery", {})
        if (
            state != "needs_recovery"
            or action not in {"resume", "restore"}
            or not decision.get(action)
        ):
            files.reject("recovery_unavailable")
        if decision.get("sha256") != sha or decision.get("expires_at", 0) < time.time():
            files.reject("preview_expired")
        path = plan.where(value)
        if files.inventory(path) != value["plan"]["files"]:
            files.reject("source_changed")
        with database.locked(config), database.connect(config.state_root) as db:
            plan.current(config, db, value, path)
            if (
                action == "restore"
                and str(path) != value["plan"]["source"]
                and os.path.lexists(value["plan"]["source"])
            ):
                files.reject("target_exists")
            next_state = (
                "restoring"
                if action == "restore"
                else ("switching" if str(path) == value["plan"]["destination"] else "quiescing")
            )
            value.pop("recovery", None)
            value.pop("reason", None)
            value["phase"] = next_state
            store.save(db, identity, value, next_state)
        return view(config, identity)


def startup(config: Config) -> None:
    with database.locked(config), database.connect(config.state_root) as db:
        for row in db.execute(
            "SELECT id FROM vault_operations WHERE kind='vault_relocation'"
        ).fetchall():
            value, state = store.load(db, row[0])
            if state in ACTIVE:
                value["reason"] = "relocation_interrupted"
                value.pop("recovery", None)
                store.save(db, row[0], value, "needs_recovery")
