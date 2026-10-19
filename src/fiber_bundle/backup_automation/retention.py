"""Retention touches only catalogued owned archives, never arbitrary user files."""

import datetime as dt
import fcntl
import os
import stat
from pathlib import Path
from typing import Any
from uuid import UUID

from ..backup_bundle import completion_record
from ..config import Config, InvalidInput, sync_directory
from . import store, target, verification


def keep(jobs: list[dict[str, Any]], policy: dict[str, Any], now: float) -> set[str]:
    ordered = sorted(jobs, key=lambda j: j["completed"], reverse=True)
    protected = {j["id"] for j in ordered if j["pinned"]}
    if ordered:
        protected.add(ordered[0]["id"])
    automatic = [j for j in ordered if not j["pinned"]]
    protected.update(j["id"] for j in automatic[: policy["keep_recent"]])
    days, weeks = set(), set()
    today = dt.datetime.fromtimestamp(now).date()
    for job in ordered:
        date = dt.datetime.fromtimestamp(job["completed"]).date()
        age = (today - date).days
        week = date.isocalendar()[:2]
        if 0 <= age < policy["keep_days"] and date not in days:
            protected.add(job["id"])
            days.add(date)
        if 0 <= age < policy["keep_weeks"] * 7 and week not in weeks:
            protected.add(job["id"])
            weeks.add(week)
    return protected


def usage(root: Path) -> int:
    total = 0
    count = 0
    for parent, directories, files in os.walk(root, followlinks=False):
        relative = Path(parent).relative_to(root)
        if len(relative.parts) > 1 and not (
            len(relative.parts) == 2 and relative.parts[0] == ".objects"
        ):
            raise InvalidInput("backup_repository_unexpected_content")
        for name in [*directories, *files]:
            count += 1
            if count > 1000000:
                raise InvalidInput("backup_repository_entry_limit")
            path = Path(parent) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or info.st_nlink > 1 and stat.S_ISREG(info.st_mode):
                raise InvalidInput("backup_repository_unexpected_content")
            if stat.S_ISREG(info.st_mode):
                total += info.st_size
    return total


def clean(config: Config, *, now: float, runner_locked: bool = False) -> dict[str, int]:
    if runner_locked:
        return _clean(config, now=now)
    root = store.directory(config)
    root.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(root / "runner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InvalidInput("backup_job_busy") from None
        return _clean(config, now=now)
    finally:
        os.close(fd)


def _collect(config: Config, root: Path) -> None:
    from ..backup_incremental.retention import collect

    collect(config, root, store.read(config))


def _clean(config: Config, *, now: float) -> dict[str, int]:
    state = store.read(config)
    root = target.repository(config, state["target"], state["owner"])
    for interrupted in state["jobs"]:
        if (
            interrupted["state"] in {"deleting", "cleanup_interrupted"}
            and Path(interrupted["destination"]).parent == root
        ):
            finish_delete(config, interrupted["id"], root, now)
    state = store.read(config)
    jobs = [
        j
        for j in state["jobs"]
        if j["state"] == "succeeded" and Path(j["destination"]).parent == root
    ]
    protected = keep(jobs, state["policy"], now)
    if not jobs:
        return {"deleted": 0, "bytes": usage(root)}
    if not any(j["id"] not in protected for j in jobs):
        _collect(config, root)
        return {"deleted": 0, "bytes": usage(root)}
    # Authenticate the newest protected version NOW, before deleting any earlier one.
    from ..model_configuration import keychain

    latest = max(jobs, key=lambda j: j["completed"])
    password = keychain.get(latest.get("secret_account", state["account"])).hex()
    verification.verify(Path(latest["destination"]), password, full=False)
    removed = 0
    for job in jobs:
        if job["id"] in protected:
            continue
        path = Path(job["destination"])
        if path.resolve(strict=True) != path or str(UUID(path.name)) != path.name:
            raise InvalidInput("backup_repository_not_owned")
        expected = (
            {"snapshot.fbenc", "bundle.json"}
            if job.get("backup_format") == "encrypted-incremental/v2"
            else {"state.fbenc", "vaults.fbenc", "bundle.json"}
        )
        if {p.name for p in path.iterdir()} != expected:
            raise InvalidInput("backup_repository_unexpected_content")
        marker = completion_record(path)
        if marker["bundle_id"] != job.get("bundle_id"):
            raise InvalidInput("backup_repository_not_owned")
        secret = keychain.get(job.get("secret_account", state["account"])).hex()
        verification.verify(path, secret, full=False)
        # Persist deletion intent before touching ciphertext. A crash can never leave a
        # partially removed version advertised as a successful available backup.
        with store.edit(config) as current:
            if current["revision"] != state["revision"]:
                raise InvalidInput("backup_policy_changed")
            selected = next(j for j in current["jobs"] if j["id"] == job["id"])
            if selected["pinned"]:
                continue
            selected["state"] = "deleting"
        finish_delete(config, job["id"], root, now)
        removed += 1
    _collect(config, root)
    return {"deleted": removed, "bytes": usage(root)}


def finish_delete(config: Config, job_id: str, root: Path, now: float) -> None:
    with store.edit(config) as state:
        job = next(j for j in state["jobs"] if j["id"] == job_id)
        if job["state"] not in {"deleting", "cleanup_interrupted"} or job["pinned"]:
            raise InvalidInput("backup_repository_not_owned")
        target.checked(config, state["target"])
        if target.repository(config, state["target"], state["owner"]) != root:
            raise InvalidInput("backup_policy_changed")
        path = Path(job["destination"])
        if path.parent != root or str(UUID(path.name)) != path.name or path.is_symlink():
            raise InvalidInput("backup_repository_not_owned")
        names = (
            {"snapshot.fbenc", "bundle.json"}
            if job.get("backup_format") == "encrypted-incremental/v2"
            else {"state.fbenc", "vaults.fbenc", "bundle.json"}
        )
        if path.exists():
            if path.resolve(strict=True) != path or not {p.name for p in path.iterdir()} <= names:
                raise InvalidInput("backup_repository_unexpected_content")
            for name in names:
                file = path / name
                if file.exists() or file.is_symlink():
                    info = file.lstat()
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise InvalidInput("backup_repository_unexpected_content")
            # Marker is removed last so an interrupted cleanup retains its identity proof.
            for name in ("snapshot.fbenc", "state.fbenc", "vaults.fbenc", "bundle.json"):
                if name not in names:
                    continue
                file = path / name
                if file.exists():
                    file.unlink()
            path.rmdir()
            sync_directory(root)
        job.update(state="deleted", deleted_at=now)
