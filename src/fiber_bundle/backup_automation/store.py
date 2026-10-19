"""Small durable private policy/job ledger, outside the snapshot scope; no recovery keys."""

import fcntl
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from ..config import Config, InvalidInput, contained, sync_directory
from ..inputs import canonical
from . import target
from .policy import Policy, Save


def initial() -> dict[str, Any]:
    return {
        "schema": "backup-automation/v1",
        "revision": 0,
        "storage_mode": "incremental",
        "full_verify_days": 7,
        "owner": None,
        "policy": Policy().model_dump(),
        "target": None,
        "account": None,
        "jobs": [],
        "baseline": None,
        "observed": None,
        "dirty_since": None,
        "growth": 0,
        "error": None,
    }


def directory(config: Config) -> Path:
    return contained(config.project_root, ".backup-automation")


def read(config: Config) -> dict[str, Any]:
    path = contained(directory(config), "state.json")
    if not path.exists():
        return initial()
    if path.stat().st_size > 64 * 1024 * 1024:
        raise InvalidInput("backup_ledger_limit")
    value = cast(dict[str, Any], json.loads(path.read_text()))
    if value.get("schema") != "backup-automation/v1":
        raise InvalidInput("backup_ledger_schema_invalid")
    value["policy"] = Policy.model_validate(value["policy"]).model_dump()
    return value


@contextmanager
def edit(config: Config) -> Iterator[dict[str, Any]]:
    root = directory(config)
    root.mkdir(mode=0o700, exist_ok=True)
    lock = contained(root, "ledger.lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        value = read(config)
        yield value
        temp = root / (str(uuid4()) + ".tmp")
        out = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(out, "w") as handle:
            handle.write(canonical(value))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, root / "state.json")
        sync_directory(root)
    finally:
        os.close(fd)


def save(config: Config, data: Save) -> dict[str, Any]:
    with edit(config) as value:
        if data.revision != value["revision"]:
            raise InvalidInput("backup_policy_changed")
        if any(j["state"] in {"queued", "running"} for j in value["jobs"]):
            raise InvalidInput("backup_job_busy")
        if data.storage_mode is not None:
            value["storage_mode"] = data.storage_mode
        if data.full_verify_days is not None:
            value["full_verify_days"] = data.full_verify_days
        previous_target = value["target"]
        if (
            data.policy.enabled
            and data.policy.after_moc
            and not (value["policy"]["enabled"] and value["policy"]["after_moc"])
        ):
            from .events import seed

            value.setdefault("moc_seen", {}).update(seed(config))
        selected = target.identity(config, data.policy.directory) if data.policy.directory else None
        account = value["account"]
        if data.recovery_key:
            if not data.key_saved:
                raise InvalidInput("backup_recovery_key_confirmation_required")
            from ..model_configuration import keychain

            account = "backup-" + str(uuid4())
            keychain.put(account, bytes.fromhex(data.recovery_key))
        if data.policy.enabled and (not selected or not account):
            raise InvalidInput("backup_configuration_required")
        if data.policy.enabled and not any(
            (
                data.policy.interval_minutes,
                data.policy.daily_time,
                data.policy.on_change,
                data.policy.growth_mb,
                data.policy.after_moc,
            )
        ):
            raise InvalidInput("backup_trigger_required")
        value.update(
            revision=value["revision"] + 1,
            owner=value["owner"] or str(uuid4()),
            policy=data.policy.model_dump(),
            target=selected,
            account=account,
            error=None,
            observed=None,
            dirty_since=None,
            growth=0,
            configured_at=time.time(),
        )
        if selected != previous_target:
            value["baseline"] = None
        value["checked_at"] = 0
        # Changing destinations starts a new baseline; historical job records are retained.
    return status(config)


def enqueue(config: Config, reason: str, observation: Any = None) -> dict[str, Any]:
    with edit(config) as value:
        if not value["target"] or not value["account"]:
            raise InvalidInput("backup_configuration_required")
        for job in value["jobs"]:
            if job["state"] in {"queued", "running"}:
                if reason == "manual":
                    job["pinned"] = True
                return cast(dict[str, Any], job)
        if len(value["jobs"]) >= 5000:
            raise InvalidInput("backup_history_limit")
        job = {
            "id": str(uuid4()),
            "reason": reason,
            "state": "queued",
            "created": time.time(),
            "attempts": 0,
            "error": None,
            "bytes": 0,
            "pinned": reason == "manual",
            "revision": value["revision"],
            "observation": observation,
            "moc_tokens": list(value.get("pending_moc", [])),
            "growth_at_start": value["growth"],
            "secret_account": value["account"],
        }
        value["jobs"].append(job)
    return job


def status(config: Config) -> dict[str, Any]:
    value = read(config)
    from .progress import read as read_progress

    return {
        "revision": value["revision"],
        "storage_mode": value.get("storage_mode", "incremental"),
        "full_verify_days": value.get("full_verify_days", 7),
        "policy": value["policy"],
        "key_configured": bool(value["account"]),
        "error": value["error"],
        "dirty": value["dirty_since"] is not None,
        "growth_bytes": value["growth"],
        "jobs": [
            {
                **{k: v for k, v in j.items() if k not in {"observation", "secret_account"}},
                **read_progress(config, j),
            }
            for j in reversed(value["jobs"])
        ],
        "baseline_at": value["baseline"].get("at") if value["baseline"] else None,
    }


def pin(config: Config, job_id: str, pinned: bool) -> dict[str, Any]:
    with edit(config) as value:
        job = next((j for j in value["jobs"] if j["id"] == job_id), None)
        if not job or job["state"] != "succeeded":
            raise InvalidInput("backup_job_unavailable")
        job["pinned"] = pinned
    return status(config)
