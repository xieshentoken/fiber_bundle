"""Role-scoped launch/stop intents; no transport or unrelated worker is stopped."""

import fcntl
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from ... import database
from ...config import Config, InvalidInput, make_directory
from ...query_policy_store import write_private
from .. import operations, store
from . import policy, runtime_image


def home(config: Config, identity: str, role: str, *, create: bool = False) -> Path:
    UUID(identity)
    if role not in policy.ROLES:
        raise InvalidInput("channel_service_role_invalid")
    path = runtime_image.root(config) / "instances" / identity / role
    if create:
        make_directory(path)
    return path


def status(config: Config, identity: str, role: str) -> dict[str, Any]:
    desired = policy.get(config, identity, role)
    if desired is None:
        return {"role": role, "managed": False, "enabled": False, "state": "legacy", "revision": 0}
    path = home(config, identity, role)
    observed = {}
    try:
        observed = json.loads((path / "status.json").read_text())
    except (FileNotFoundError, ValueError):
        pass
    locked = False
    lock = path / "lease.lock"
    if lock.exists():
        with lock.open("rb") as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                locked = True
    current = observed.get("generation") == desired["revision"]
    return {
        "role": role,
        "managed": True,
        "enabled": desired["enabled"],
        "revision": desired["revision"],
        "admit_after": desired["admit_after"],
        "process_running": locked,
        "state": observed.get("state", "starting")
        if locked and current
        else "draining"
        if locked
        else "stopped",
        "heartbeat_fresh": locked
        and current
        and 0 <= time.time() - observed.get("heartbeat_at", 0) < 20,
        "runtime_version": observed.get("image") if current else None,
        "error": observed.get("error"),
    }


def start(
    config: Config,
    identity: str,
    role: str,
    expected: int,
    *,
    authorize: Callable[[], Any] | None = None,
    image: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Prepare code/dependency image outside the storage lease. Routes must not wrap this IO.
    prior = policy.get(config, identity, role)
    image = image or (
        runtime_image.resolve(config, prior["runtime_image"])
        if prior and prior.get("runtime_image")
        else runtime_image.pin(config)
    )
    with database.locked(config), store.opened(config) as db:
        if authorize:
            authorize()
        channel = operations.entry(config, db, identity)
        main = database.connect(config.state_root)
        try:
            store.authorize(config, main, channel)
        finally:
            main.close()
        current = status(config, identity, role)
        if current.get("process_running"):
            raise InvalidInput("channel_service_still_running")
        desired = policy.change(
            config, identity, role, expected, enabled=True, runtime_image=image["id"]
        )
        path = home(config, identity, role, create=True)
        launch = {
            "storage": config.storage_configuration(),
            "channel_id": identity,
            "role": role,
            "generation": desired["revision"],
            "nonce": str(uuid4()),
            "image": image["id"],
        }
        write_private(path / "launch.json", json.dumps(launch).encode())
        python = Path(image["root"]) / image["python"]
        try:
            subprocess.Popen(
                [
                    str(python),
                    "-B",
                    "-m",
                    "fiber_bundle.channels.services.worker",
                    "--project-root",
                    str(config.project_root),
                    "--configuration",
                    str(path / "launch.json"),
                    "--nonce",
                    launch["nonce"],
                ],
                env=runtime_image.environment(image),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            policy.change(config, identity, role, desired["revision"], enabled=False)
            raise InvalidInput("channel_service_launch_failed") from None
    return status(config, identity, role)


def pause(
    config: Config,
    identity: str,
    role: str,
    expected: int,
    *,
    authorize: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    with database.locked(config):
        if authorize:
            authorize()
        policy.change(config, identity, role, expected, enabled=False)
    return status(config, identity, role)


def ensure_new(config: Config, identity: str) -> None:
    """New purpose-specific entries always start with independent processing."""
    with store.readonly(config) as db:
        if db is None:
            raise InvalidInput("channel_service_binding_unavailable")
        entry = store.get(db, identity)
    if not entry["value"].get("purpose"):
        return
    for role in policy.roles(entry):
        if policy.get(config, identity, role) is None:
            start(config, identity, role, 0)
