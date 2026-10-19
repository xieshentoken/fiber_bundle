"""Explicit process launch and cooperative stop for generated private homes only."""

import json
import subprocess
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

from .. import feishu_runtime, query_policy_store
from ..config import Config, InvalidInput
from ..inputs import canonical
from . import store


def status(config: Config, identity: str) -> dict[str, Any]:
    home = store.home(config, identity)
    result = feishu_runtime.inspect(home)
    path = home / "channel-status.json"
    try:
        from ..query_routing_files import read

        value = json.loads(read(path, 4096))
        if (
            isinstance(value, dict)
            and result.get("instance_id") == value.get("instance_id")
            and type(value.get("revision")) is int
            and isinstance(value.get("nonce"), str)
        ):
            return {
                **result,
                "transport_state": result["state"],
                **{
                    key: value[key]
                    for key in ("state", "platform", "revision", "nonce", "runtime_version")
                    if key in value
                },
            }
    except (OSError, ValueError, InvalidInput):
        pass
    if result["process_state"] == "running":
        return {
            **result,
            "available": False,
            "transport_state": "unknown",
            "reason": "channel_status_missing",
        }
    return result


def launch(config: Config, main: Any, identity: str, action: str) -> dict[str, Any]:
    if action not in {"check", "pair", "login", "run"}:
        raise InvalidInput("channel_action_invalid")
    with store.opened(config) as db:
        channel = store.get(db, identity)
        from .operations import pending

        if pending(db, identity):
            raise InvalidInput("channel_operation_pending")
        store.authorize(config, main, channel)
        from .configuration import public

        if status(config, identity).get("instance_id"):
            raise InvalidInput("channel_instance_already_running")
        if channel["state"] not in {"paused", "unpaired", "error"}:
            raise InvalidInput("channel_pause_required")
        if action == "run":
            if not channel["owner"]:
                raise InvalidInput("channel_pairing_required")
            from .intake_repository import policy as intake_policy

            intake = intake_policy(db, identity)["settings"]
            from .validation import ChannelValidationError, start_issues

            issues = start_issues(channel["value"], intake_enabled=intake["enabled"])
            if issues:
                raise ChannelValidationError(issues)
            if db.execute(
                "SELECT 1 FROM events WHERE channel_id=? AND state IN ('sending','unknown')",
                (identity,),
            ).fetchone():
                raise InvalidInput("channel_delivery_requires_reconciliation")
        if action == "pair" and channel["value"]["platform"] != "feishu":
            raise InvalidInput("channel_action_invalid")
        if action == "login" and channel["value"]["platform"] != "weixin":
            raise InvalidInput("channel_action_invalid")
        if action in {"pair", "login"}:
            db.execute(
                "UPDATE channels SET candidate=NULL,challenge=? WHERE id=?",
                ("Fiber配对-" + str(uuid4()) if action == "pair" else None, identity),
            )
        if action == "login":
            (store.home(config, identity) / "login-qr.png").unlink(missing_ok=True)
        nonce = str(uuid4())
        store.set_state(db, identity, "running" if action == "run" else action)
        path = store.home(config, identity) / "launch.json"
        query_policy_store.write_private(
            path,
            canonical(
                {
                    "storage": config.storage_configuration(),
                    "channel_id": identity,
                    "revision": channel["revision"],
                    "action": action,
                    "nonce": nonce,
                }
            ).encode(),
        )
        from .services import runtime_image

        image = runtime_image.pin(config)
        launch_value = json.loads((path).read_text())
        launch_value["runtime_image"] = image["id"]
        query_policy_store.write_private(path, canonical(launch_value).encode())
        try:
            subprocess.Popen(
                [
                    str(Path(image["root"]) / image["python"]),
                    "-B",
                    "-m",
                    "fiber_bundle.channels.worker",
                    "--project-root",
                    str(config.project_root),
                    "--configuration",
                    str(path),
                    "--nonce",
                    nonce,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=runtime_image.environment(image),
                start_new_session=True,
            )
        except OSError:
            store.set_state(db, identity, "error")
            raise InvalidInput("channel_launch_failed") from None
        return public(config, store.get(db, identity))


def pause(config: Config, main: Any, identity: str) -> dict[str, Any]:
    from .configuration import public
    from .operations import pending

    with store.opened(config) as db:
        channel = store.get(db, identity)
        if channel["state"] == "removed":
            raise InvalidInput("channel_removed")
        if pending(db, identity):
            raise InvalidInput("channel_operation_pending")
        # Withdraw first. Worker and final send guards stop even when the network is pending.
        store.set_state(db, identity, "paused" if channel["owner"] else "unpaired")
        from .services.policy import managed

        if not managed(config, identity):
            cancel_pending(config, main, db, channel)
        return public(config, store.get(db, identity))


def cancel_pending(config: Config, main: Any, db: Any, channel: dict[str, Any]) -> None:
    from .. import query_jobs

    identity = channel["id"]
    for event in db.execute(
        "SELECT value FROM events WHERE channel_id=? AND state IN ('processing','gui_processing')",
        (identity,),
    ):
        value = json.loads(event[0])
        if value.get("job_id"):
            query_jobs.cancel(config, main, channel["value"]["client_id"], value["job_id"])
    db.execute(
        "UPDATE events SET state='cancelled' WHERE channel_id=? "
        "AND state IN ('captured','processing','gui_processing','ready')",
        (identity,),
    )


def confirm_owner(
    config: Config, identity: str, expected_revision: int, owner: str
) -> dict[str, Any]:
    from .configuration import public

    with store.opened(config) as db:
        channel = store.get(db, identity)
        from .operations import pending

        if pending(db, identity):
            raise InvalidInput("channel_operation_pending")
        candidate = channel["candidate"]
        if (
            channel["revision"] != expected_revision
            or not candidate
            or channel["state"] != "unpaired"
            or candidate.get("expires_at", 0) < time.time()
            or candidate["owner_id"] != owner
        ):
            raise InvalidInput("channel_pairing_candidate_changed")
        db.execute(
            "UPDATE channels SET owner=?,state='paused',candidate=NULL,challenge=NULL WHERE id=?",
            (owner, identity),
        )
        return public(config, store.get(db, identity))


def offline_ready(config: Config) -> bool:
    import sqlite3
    from contextlib import closing

    from ..config import contained
    from . import legacy, operations

    values, issues = legacy.entries(config)
    if issues or any(
        feishu_runtime.inspect(legacy.home(config, entry))["process_state"] != "stopped"
        for entry in values
    ):
        return False
    path = contained(config.state_root, "message-channels.sqlite3")
    if not path.exists():
        return True
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.row_factory = sqlite3.Row
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='channels'").fetchone():
            return True
        from .services import control as service_control
        from .services import policy as service_policy

        for identity, state in db.execute("SELECT id,state FROM channels"):
            for role in service_policy.ROLES:
                intent = service_policy.get(config, identity, role)
                if intent and (
                    intent["enabled"]
                    or service_control.status(config, identity, role).get("process_running")
                ):
                    return False
            if state in {"running", "pair", "login", "check"} or status(config, identity).get(
                "instance_id"
            ):
                return False
            pending_operation = operations.latest(db, identity)
            if pending_operation and pending_operation["state"] == "pending":
                return False
    return True
