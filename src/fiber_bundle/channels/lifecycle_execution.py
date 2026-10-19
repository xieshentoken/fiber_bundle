"""One recoverable lifecycle step; callers hold the storage lease, never wait under it."""

import hashlib
import json
from pathlib import Path
from typing import Any

from .. import database, feishu_runtime, query_policy_store
from ..config import Config, InvalidInput, StorageError, contained
from . import cleanup, configuration, control, health, legacy, legacy_control, operations, store


def advance(config: Config, operation_id: str) -> bool:
    """Return True when complete or awaiting review; False only while an owned process exits."""
    with store.opened(config) as db:
        operation = operations.get(db, operation_id)
        if operation["state"] in {"completed", "interrupted"}:
            return True
        intent = operation["value"]
        channel = operations.entry(config, db, operation["channel_id"])
        old = intent["origin"] == "legacy"
        home = legacy.home(config, channel) if old else store.home(config, channel["id"])
        try:
            if channel["revision"] != intent["request"]["expected_revision"]:
                raise InvalidInput("channel_revision_conflict")
            if old:
                path = contained(config.state_root, channel["settings_path"])
                if path.exists():
                    legacy_control.disable(config, intent["channel"], intent)
            else:
                if health.fingerprint(channel) != intent["configuration_sha256"]:
                    raise InvalidInput("channel_revision_conflict")
                main = database.connect(config.state_root)
                try:
                    from .services.policy import managed

                    if intent["request"]["action"] != "pause" or not managed(config, channel["id"]):
                        control.cancel_pending(config, main, db, channel)
                finally:
                    main.close()
            runtime = feishu_runtime.inspect(home)
            if runtime["process_state"] != "stopped":
                if runtime.get("instance_id") != intent["request"]["expected_instance_id"]:
                    raise InvalidInput("channel_revision_conflict")
                if old and not intent["signal_reserved"]:
                    # Reserve before signalling. An interrupted signal is never blindly replayed.
                    intent["signal_reserved"] = True
                    operations.update(db, operation, "pending")
                    legacy_control.signal_stop(config, channel, runtime.get("instance_id"))
                return False
            unresolved = (
                0
                if old
                else db.execute(
                    "SELECT count(*) FROM events WHERE channel_id=? "
                    "AND state IN ('sending','unknown')",
                    (channel["id"],),
                ).fetchone()[0]
            )
            if operation["action"] != "pause" and unresolved:
                raise InvalidInput("channel_delivery_requires_reconciliation")
            with cleanup.stopped_home(home):
                if operation["action"] != "pause":
                    intent["cleanup_started"] = True
                    operations.update(db, operation, "pending")
                    extras: tuple[Path, ...] = ()
                    if operation["action"] == "remove":
                        if old:
                            extras = (contained(config.state_root, channel["settings_path"]),)
                            private_key = _owned_legacy_secret(config, channel)
                            if private_key:
                                extras += (private_key,)
                        else:
                            extras = (configuration.secret_path(config, channel["id"]),)
                    cleanup.identity_files(home, extras)
                    channel["owner"] = None
                    if old and operation["action"] == "unbind":
                        path = contained(config.state_root, channel["settings_path"])
                        raw = {**channel["value"], "enabled": False, "owner_id": "ou_unpaired"}
                        wire = json.dumps(raw).encode()
                        intent["unbound_sha256"] = hashlib.sha256(wire).hexdigest()
                        operations.update(db, operation, "pending")
                        query_policy_store.write_private(path, wire)
                        channel.update(
                            value=raw,
                            configuration_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                        )
                state = (
                    "removed"
                    if operation["action"] == "remove"
                    else "paused"
                    if channel["owner"]
                    else "unpaired"
                )
                with database.transaction(db):
                    if old:
                        operations.set_entry_state(db, channel, state)
                    else:
                        db.execute(
                            "UPDATE channels SET state=?,owner=?,candidate=NULL,challenge=NULL "
                            "WHERE id=?",
                            (state, channel["owner"], channel["id"]),
                        )
                    operations.update(db, operation, "completed")
            return True
        except (StorageError, OSError, ValueError) as exc:
            code = (
                str(exc)
                if isinstance(exc, InvalidInput)
                and str(exc)
                in {
                    "channel_revision_conflict",
                    "channel_legacy_process_unverified",
                    "channel_cleanup_requires_review",
                    "channel_process_not_stopped",
                    "channel_delivery_requires_reconciliation",
                }
                else "channel_cleanup_requires_review"
            )
            operations.update(db, operation, "needs_review", code)
            return True


def _owned_legacy_secret(config: Config, channel: dict[str, Any]) -> Path | None:
    value = channel["value"].get("secret_file")
    if not isinstance(value, str):
        return None
    path = Path(value)
    home = legacy.home(config, channel)
    if not path.is_relative_to(home) or path.name in {
        "receiver.json",
        "fiber-instance.lock",
        "runtime-manifest.json",
    }:
        return None
    values, issues = legacy.entries(config)
    if issues or any(
        item["id"] != channel["id"] and item["value"].get("secret_file") == value for item in values
    ):
        return None
    return contained(home, path.relative_to(home).as_posix())
