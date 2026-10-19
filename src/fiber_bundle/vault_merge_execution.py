"""Finite staged merge execution; interrupted effects require explicit reconciliation."""

from pathlib import Path
from typing import Any

from . import (
    auth,
    database,
    vault_merge_guard,
    vault_merge_inventory,
    vault_merge_plan,
    vault_save_policy,
)
from . import vault_merge_journal as journal
from .config import Config, InvalidInput, StorageError


def authorize(config: Config, db: Any, identity: str, value: dict[str, Any]) -> None:
    current, state = journal.load(db, identity)
    if state not in vault_merge_guard.BLOCKING or current.get("cancel_requested"):
        raise InvalidInput("vault_merge_execution_not_active")
    plan = value["plan"]
    selection = plan["selection"]
    if current["plan"]["plan_sha256"] != plan["plan_sha256"]:
        raise InvalidInput("vault_merge_frozen_plan_changed")
    if value.get("switched"):
        from .vault_merge_switch import check_committed

        check_committed(config, db, value)
        return
    for source in plan["inventories"]:
        context = vault_merge_inventory.context(
            config, db, selection["client_id"], source["context"]["vault_id"]
        )
        if context != source["context"]:
            raise InvalidInput("vault_merge_current_scope_changed")
    current_policy = vault_save_policy.get(config, db, selection["target_vault_id"])
    # MOC display choices grow during our own binding stage; they are not save policy.
    if {k: v for k, v in current_policy.items() if k not in {"mocs", "mocs_truncated"}} != {
        k: v for k, v in plan["target_policy"].items() if k not in {"mocs", "mocs_truncated"}
    }:
        raise InvalidInput("vault_merge_target_policy_changed")


def start(
    config: Config, identity: str, plan_sha: str, backup: Path, password: str
) -> dict[str, Any]:
    from .vault_merge_backup import verify

    with database.connect(config.state_root) as db:
        value, state = journal.load(db, identity)
        if state != "prepared" or value["plan"]["plan_sha256"] != plan_sha:
            raise InvalidInput("vault_merge_frozen_confirmation_changed")
        before = vault_merge_plan.preflight(
            config, db, vault_merge_plan.Selection.model_validate(value["plan"]["selection"])
        )
        if before["plan_sha256"] != value["plan"]["plan_sha256"]:
            raise InvalidInput("vault_merge_frozen_scope_changed")
    verified = verify(backup, password, value["plan"])
    with database.locked(config), database.connect(config.state_root) as db:
        latest, state = journal.load(db, identity)
        if state != "prepared" or latest != value:
            raise InvalidInput("vault_merge_state_changed")
        # Another operation may not own a barrier on any affected Vault.
        for inventory in value["plan"]["inventories"]:
            vault_merge_guard.check(db, inventory["context"]["vault_id"])
        value["backup"] = verified
        value["phase"] = "pausing"
        journal.save(db, identity, value, "pausing")
    return view(config, identity)


def view(config: Config, identity: str) -> dict[str, Any]:
    with database.connect(config.state_root) as db:
        return journal.view(db, identity)


def advance(config: Config, identity: str) -> dict[str, Any]:
    from .executor_container import run_lock

    with run_lock(config, identity):
        return _advance(config, identity)


def _advance(config: Config, identity: str) -> dict[str, Any]:
    from . import vault_merge_bindings, vault_merge_files, vault_merge_originals, vault_merge_switch

    with vault_merge_guard.executing(identity):
        try:
            with database.locked(config), database.connect(config.state_root) as db:
                value, state = journal.load(db, identity)
                authorize(config, db, identity, value)
            if state in {"rebinding", "switching"}:
                from .vault_merge_verify import current

                current(config, identity)
            if state == "pausing":
                with database.locked(config), database.connect(config.state_root) as db:
                    value, state = journal.load(db, identity)
                    if not vault_merge_switch.pause(config, db, value):
                        return view(config, identity)
                    journal.save(db, identity, value, state)
                following = "copying"
            elif state == "copying":
                pending = next(
                    (
                        i
                        for i in range(len(value["plan"]["files"]))
                        if value["progress"].get(str(i), {}).get("state") != "copied"
                    ),
                    None,
                )
                if pending is not None:
                    vault_merge_files.copy(config, identity, pending)
                    return view(config, identity)
                following = "originals"
            elif state == "originals":
                pending_original = next(
                    (
                        (s["context"]["vault_id"], v)
                        for s in value["plan"]["inventories"][:-1]
                        for v in s["sources"]
                        if v["version_id"] not in value.get("originals", {})
                    ),
                    None,
                )
                if pending_original:
                    vault_merge_originals.copy(
                        config, identity, pending_original[1], pending_original[0]
                    )
                    return view(config, identity)
                following = "rebinding"
            elif state == "rebinding":
                with database.locked(config), database.connect(config.state_root) as db:
                    value, _ = journal.load(db, identity)
                    authorize(config, db, identity, value)
                    vault_merge_bindings.apply(config, db, identity, value)
                following = "switching"
            elif state == "switching":
                with database.locked(config), database.connect(config.state_root) as db:
                    value, _ = journal.load(db, identity)
                    authorize(config, db, identity, value)
                    vault_merge_switch.apply(config, db, identity, value)
                    value["phase"] = "index"
                    journal.save(db, identity, value, "index")
                return view(config, identity)
            elif state == "index":
                from .note_search_build import build

                selection = value["plan"]["selection"]
                build(config, selection["client_id"], selection["target_vault_id"])
                from .vault_merge_verify import current

                current(config, identity)
                with database.locked(config), database.connect(config.state_root) as db:
                    value, _ = journal.load(db, identity)
                    # Switch changes epochs intentionally; use its committed snapshot afterwards.
                    vault_merge_switch.check_committed(config, db, value)
                    value["phase"] = "complete"
                    journal.save(db, identity, value, "complete")
                return view(config, identity)
            else:
                raise InvalidInput("vault_merge_explicit_reconciliation_required")
            with database.locked(config), database.connect(config.state_root) as db:
                value, _ = journal.load(db, identity)
                value["phase"] = following
                journal.save(db, identity, value, following)
            return view(config, identity)
        except (StorageError, auth.Denied, OSError):
            with database.locked(config), database.connect(config.state_root) as db:
                value, state = journal.load(db, identity)
                value["reason"] = "vault_merge_stage_requires_reconciliation"
                journal.save(db, identity, value, "reconcile")
            raise
