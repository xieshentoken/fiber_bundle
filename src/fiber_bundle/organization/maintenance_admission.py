"""Same-vault admission and explicit, non-destructive completion of partial work."""

import copy
import time
from typing import Any

from ..config import IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import packages, store

TERMINAL = {"completed", "cancelled", "closed"}


def release_background_waits(config: Any) -> None:
    """Resume only previously queued background intent; never resume partial maintenance."""
    from .. import database

    waiting = [
        j
        for j in store.rows(config, "jobs")
        if not j.get("maintenance") and j["state"] == "waiting_task"
    ]
    for job in waiting:
        if blocker(config, job):
            continue
        with database.locked(config):
            current = store.get(config, "jobs", job["id"])
            if current["state"] == "waiting_task" and not blocker(config, current):
                current.update(state="queued", error=None)
                current.pop("blocking_task", None)
                store.put(config, "jobs", current["id"], current["vault_id"], current)


def snapshot(config: Any, vault: str) -> list[dict[str, Any]]:
    """Read compact admission metadata, never deserialize compensation file snapshots."""
    with store.connection(config) as db:
        if db is None:
            return []
        names = {r[0] for r in db.execute("SELECT name FROM sqlite_master")}
        if "jobs" not in names:
            return []
        journal = (
            "EXISTS(SELECT 1 FROM maintenance_undo u WHERE u.id=j.id)"
            if "maintenance_undo" in names
            else "0"
        )
        return [
            dict(r)
            for r in db.execute(
                "SELECT j.id, json_extract(value,'$.state') state, "
                "json_extract(value,'$.confirmed') confirmed, "
                "json_extract(value,'$.maintenance') maintenance, "
                "json_extract(value,'$.created_at') created_at, "
                "json_extract(value,'$.completed') completed, "
                "json_array_length(value,'$.items') total, " + journal + " journal "
                "FROM jobs j WHERE vault=?",
                (vault,),
            )
        ]


def blocker(config: Any, job: Any, *, admission: Any = None) -> Any:
    if job["state"] in TERMINAL:
        return None
    rows = snapshot(config, job["vault_id"]) if admission is None else admission
    own_journal = any(r["id"] == job["id"] and r["journal"] for r in rows)
    own_reservation = job.get("confirmed") and job["state"] in {"queued", "running"}
    candidates = []
    for other in rows:
        if other["id"] == job["id"] or not other.get("maintenance"):
            continue
        if not other.get("confirmed") or other["state"] in TERMINAL:
            continue
        journal = other["journal"]
        reserved = other["state"] in {"queued", "running", "cancelling"}
        if not journal and (
            own_journal
            or (
                own_reservation
                and (job.get("created_at", 0), job["id"])
                < (other.get("created_at", 0), other["id"])
            )
        ):
            reserved = False
        if journal or reserved:
            candidates.append((not bool(journal), other.get("created_at", 0), other["id"], other))
    if not candidates:
        return None
    other = min(candidates, key=lambda row: row[:3])[3]
    return {k: other.get(k) for k in ("id", "state", "completed", "created_at")} | {
        "total": other["total"],
    }


def hold(config: Any, job: Any, *, preview: bool = False) -> bool:
    other = blocker(config, job)
    if other is None:
        job.pop("blocking_task", None)
        return False
    job.update(blocking_task=other, error=None)
    if not preview:
        job["state"] = "waiting_task"
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    return True


def close_evidence(config: Any, db: Any, job: Any) -> Any:
    """Read-only proof. Never reconcile a pending file exchange while closing."""
    from . import maintenance_undo, maintenance_undo_seal

    if job.get("stage") == "rollback" or job["state"] in {"running", "queued", "cancelling"}:
        raise InvalidInput("maintenance_close_pause_required")
    if any(i.get("intent") for i in job["items"]):
        raise IntegrityError("maintenance_close_recovery_required")
    journal = store.get(config, "maintenance_undo", job["id"])
    operations = set(journal.get("operations", [])) if journal else set()
    operations.update(i["archive_operation"] for i in job["items"] if i.get("archive_operation"))
    rows = []
    for identity in sorted(operations):
        row = db.execute(
            "SELECT state,payload FROM vault_operations WHERE id=? AND vault_id=?",
            (identity, job["vault_id"]),
        ).fetchone()
        if row is None or row["state"] != "completed":
            raise IntegrityError("maintenance_close_recovery_required")
        rows.append([identity, row["state"], digest(row["payload"].encode())])
    if journal and journal["state"] == "applying":
        journal = copy.deepcopy(journal)
        known = {p["id"] for p in journal["plans"]}
        owned = [
            p
            for p in maintenance_undo.plans(config, job)
            if p.plan_id not in journal["unit_plans"]
            or ("unit_paths" not in journal and p.plan_id in known)
        ]
        if any(p.status == "pending" for p in owned):
            raise IntegrityError("maintenance_close_recovery_required")
        root, _ = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
        maintenance_undo_seal.observe(config, db, job, journal, root, owned)
        # Observe verifies outcome bytes; close does not prepare an undo or modify files.
    elif journal and journal["state"] != "ready":
        raise IntegrityError("maintenance_close_recovery_required")
    token = digest(canonical([job, journal, rows]).encode())
    return {
        "plan_sha256": token,
        "completed": job.get("completed", 0),
        "remaining": sum(i["state"] != "done" for i in job["items"]),
        "archived": sum(i.get("phase") == "archived" for i in job["items"]),
    }


def close(config: Any, db: Any, job: Any, expected: str | None) -> Any:
    if job["state"] == "closed":
        if expected == job.get("close_confirmation"):
            return job
        raise InvalidInput("maintenance_close_confirmation_required")
    evidence = close_evidence(config, db, job)
    if expected != evidence["plan_sha256"]:
        raise InvalidInput("maintenance_close_confirmation_required")
    job.update(
        state="closed",
        closed_at=time.time(),
        close_confirmation=expected,
        close_result=evidence,
        generation=job.get("generation", 0) + 1,
        error=None,
    )
    job.pop("blocking_task", None)
    # Historical journals and pending items remain available for audit, never replayed.
    store.put(config, "jobs", job["id"], job["vault_id"], job)
    return job
