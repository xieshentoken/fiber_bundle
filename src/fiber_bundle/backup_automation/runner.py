"""Single durable backup executor. Failure never advances the successful baseline."""

import fcntl
import os
import re
import shutil
import time
from pathlib import Path
from uuid import uuid4

from .. import backup_bundle, backup_preflight
from ..backup_inventory import Limits
from ..config import Config, InvalidInput
from . import changes, retention, store, target, verification
from .progress import Reporter


def execute(config: Config) -> bool:
    value = store.read(config)
    if not any(
        j["state"] == "queued" and j.get("next_at", 0) <= time.time() for j in value["jobs"]
    ):
        return False
    fd = os.open(
        store.directory(config) / "runner.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        with store.edit(config) as value:
            job = next(
                (
                    j
                    for j in value["jobs"]
                    if j["state"] == "queued" and j.get("next_at", 0) <= time.time()
                ),
                None,
            )
            if job is None:
                return False
            if job["revision"] != value["revision"]:
                job.update(state="failed", error="backup_policy_changed")
                return True
            job.update(
                state="running",
                phase="checking",
                attempts=job["attempts"] + 1,
                started_at=time.time(),
            )
            job_id = job["id"]
        reporter = Reporter(config, job_id, job["attempts"])
        try:
            policy = value["policy"]
            limits = Limits(
                entries=policy["max_entries"],
                file_bytes=policy["max_file_mb"] * 1024**2,
                total_bytes=policy["max_snapshot_mb"] * 1024**2,
            )
            reporter({"phase": "capacity", "files_done": 0, "bytes_done": 0})
            estimate = backup_preflight.capacity(config, limits=limits, progress=reporter)
            with store.edit(config) as state:
                next(j for j in state["jobs"] if j["id"] == job_id)["preflight"] = estimate
            if job["observation"] is None:
                observation = changes.observe(config)
                with store.edit(config) as state:
                    next(j for j in state["jobs"] if j["id"] == job_id)["observation"] = observation
            from ..model_configuration import keychain

            with keychain.noninteractive():
                password = keychain.get(value["account"]).hex()
            root = target.repository(config, value["target"], value["owner"])
            destination = root / str(uuid4())
            # Bound free space before work; the complete archive may contain overhead.
            if shutil.disk_usage(root).free < 16 * 1024**2:
                raise InvalidInput("backup_disk_full")
            with store.edit(config) as state:
                active = next(j for j in state["jobs"] if j["id"] == job_id)
                active.update(phase="scanning", destination=str(destination))
                active.setdefault("attempt_destinations", []).append(str(destination))
            quota = policy["max_total_mb"] * 1024**2
            available = max(0, quota - retention.usage(root)) if quota else None
            incremental = value.get("storage_mode", "incremental") == "incremental"
            full_verification = True
            last_full = 0.0
            with keychain.noninteractive():
                if incremental:
                    from ..backup_incremental import snapshot

                    eligible = [
                        j
                        for j in value["jobs"]
                        if j["state"] == "succeeded"
                        and j.get("backup_format") == snapshot.SCHEMA
                        and j.get("secret_account") == value["account"]
                        and Path(j["destination"]).parent == root
                    ]
                    previous = max(eligible, key=lambda j: j["completed"], default=None)
                    last_full = max((j.get("full_verified_at", 0) for j in eligible), default=0)
                    full_verification = (
                        time.time() - last_full >= value.get("full_verify_days", 7) * 86400
                    )
                    result = snapshot.create(
                        config,
                        destination,
                        password,
                        base=Path(previous["destination"]) if previous else None,
                        limits=limits,
                        online=True,
                        capacity_bytes=available,
                        progress=reporter,
                    )
                else:
                    result = backup_bundle.create(
                        config,
                        destination,
                        password,
                        limits=limits,
                        online=True,
                        capacity_bytes=available,
                        progress=reporter,
                    )
            with store.edit(config) as state:
                next(j for j in state["jobs"] if j["id"] == job_id).update(
                    phase="verifying", verification="full" if full_verification else "incremental"
                )
            verification.verify(destination, password, full=full_verification, progress=reporter)
            size = sum(p.stat().st_size for p in destination.iterdir()) + result.get(
                "stats", {}
            ).get("new_ciphertext_bytes", 0)
            with store.edit(config) as state:
                job = next(j for j in state["jobs"] if j["id"] == job_id)
                job.update(
                    state="succeeded",
                    phase="complete",
                    completed=time.time(),
                    finished_at=time.time(),
                    bundle_id=result["bundle_id"],
                    authenticated=True,
                    bytes=size,
                    error=None,
                    backup_format=result["schema"],
                    namespace=result.get("namespace"),
                    stats=result.get("stats"),
                    verification="full" if full_verification else "incremental",
                    full_verified_at=time.time()
                    if full_verification
                    else last_full
                    if incremental
                    else time.time(),
                )
                state["baseline"] = {"at": time.time(), "observation": job["observation"]}
                captured = job["observation"]
                same = not state["observed"] or state["observed"]["sha256"] == captured["sha256"]
                state.update(
                    error=None,
                    dirty_since=None if same else state["dirty_since"],
                    growth=0 if same else max(0, state["growth"] - job.get("growth_at_start", 0)),
                )
                consumed = set(job.get("moc_tokens", []))
                state["pending_moc"] = [
                    t for t in state.get("pending_moc", []) if t not in consumed
                ]
                # Keep the successful baseline once, rather than duplicating it in every job.
                job["observation"] = {"sha256": captured["sha256"]}
            try:
                with keychain.noninteractive():
                    retention.clean(config, now=time.time(), runner_locked=True)
            except Exception:
                with store.edit(config) as state:
                    next(j for j in state["jobs"] if j["id"] == job_id)["cleanup_error"] = (
                        "backup_retention_needs_attention"
                    )
        except Exception as exc:
            code = str(exc)
            # Never log secret-bearing third-party exception messages or arbitrary paths.
            code = (
                code
                if re.fullmatch(r"(?:backup|model|restore)_[a-z_]{1,80}", code)
                else type(exc).__name__
            )
            with store.edit(config) as state:
                job = next(j for j in state["jobs"] if j["id"] == job_id)
                retry = job["reason"] != "manual" and job["attempts"] < 3
                job.update(
                    state="queued" if retry else "failed",
                    phase="waiting" if retry else "failed",
                    error=code,
                    next_at=time.time() + min(300, 30 * job["attempts"]),
                    finished_at=time.time(),
                    failure_details=getattr(exc, "details", None),
                )
                state["error"] = code
        return True
    finally:
        os.close(fd)


def recover(config: Config) -> None:
    if not store.directory(config).exists():
        return
    with store.edit(config) as value:
        for job in value["jobs"]:
            if job["state"] == "deleting":
                job.update(
                    state="cleanup_interrupted", cleanup_error="backup_retention_needs_attention"
                )
            if job["state"] == "running":
                # A marker alone is not authenticated acceptance. Leave incomplete evidence;
                # a user retry creates a new archive, never repeats a business operation.
                job.update(state="failed", phase="interrupted", error="backup_interrupted")
                value["error"] = "backup_interrupted"
