"""Deep replacement retires classification contributions, never human prose or explicit locks."""

from typing import Any

from .. import moc_file_journal, moc_registration, moc_store
from ..config import IntegrityError
from . import membership, store


def remove(config: Any, db: Any, job: Any, item: Any, mid: str) -> None:
    from .maintenance_apply import observe, persist

    key = "unlink:" + mid
    if mid in item["locked"] or mid in item["rejected"]:
        raise IntegrityError("maintenance_locked_membership")
    decision_key = membership.decision_key(job["vault_id"], item["entity"], mid)
    decision = store.get(config, "decisions", decision_key)
    if decision and decision["state"] in {"pinned", "rejected"}:
        raise IntegrityError("maintenance_locked_membership")
    moc = next(m for m in job["mocs"] if m["id"] == mid)
    expected = job["moc_expected"][mid]
    current = observe(config, db, job, mid)
    intent = item.get("intent") == key
    if current != expected and not intent:
        raise IntegrityError("maintenance_moc_changed")
    item["intent"] = key
    persist(config, job)
    if moc_store.load(config, job["vault_id"], mid) is None or item.get("retire_adoption"):
        operation = item.get("retire_adoption")
        if operation is None:
            packet = moc_registration.preview(
                config, db, job["client_id"], job["vault_id"], moc["path"], expected["sha256"]
            )
            operation = packet["operation_id"]
            item["retire_adoption"] = operation
            persist(config, job)
        record = moc_store.load(config, job["vault_id"], mid)
        moc_registration.apply(
            config,
            db,
            job["client_id"],
            job["vault_id"],
            operation,
            "reconcile" if record else "apply",
        )
        job["moc_expected"][mid] = observe(config, db, job, mid)
        item.pop("retire_adoption", None)
        persist(config, job)
        expected = job["moc_expected"][mid]
        current = expected
    desired = {k: list(v) for k, v in expected["members"].items() if k != item["entity"]}
    if current != expected:
        latest = moc_file_journal.latest(moc_file_journal.plans(config, job["vault_id"], mid))
        if not (
            intent
            and current["members"] == desired
            and latest
            and current["sha256"] in {expected["sha256"], latest.value["after_sha256"]}
        ):
            raise IntegrityError("maintenance_moc_changed")
    # Retire the inherited classification before a potentially interrupted MOC
    # exchange, so legacy automatic tasks cannot reintroduce it in that gap.
    store.put(
        config,
        "decisions",
        decision_key,
        job["vault_id"],
        {"entity": item["entity"], "moc": mid, "state": "retired", "job": job["id"]},
    )
    membership.apply(
        config,
        db,
        job["client_id"],
        job["vault_id"],
        item["source"],
        moc,
        owner="manual",
        action="unlink",
        record_decision=False,
    )
    store.put(
        config,
        "decisions",
        decision_key,
        job["vault_id"],
        {"entity": item["entity"], "moc": mid, "state": "retired", "job": job["id"]},
    )
    job["moc_expected"][mid] = observe(config, db, job, mid)
    item.setdefault("steps", []).append(key)
    item.pop("intent", None)
    persist(config, job)
