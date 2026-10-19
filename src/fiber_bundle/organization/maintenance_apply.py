"""Preview-bound execution, one entity at a time, with separate membership/archive stages."""

from typing import Any

from .. import moc_store
from ..config import IntegrityError
from ..objects import digest
from ..publication_file_io import read
from . import (
    maintenance,
    maintenance_archive,
    maintenance_moc_recovery,
    maintenance_mocs,
    manual_resolution,
    membership,
    note_moc_sync,
    packages,
    store,
)
from .maintenance_timing import measure


def persist(config: Any, job: Any) -> None:
    store.put(config, "jobs", job["id"], job["vault_id"], job)


def validate_decisions(config: Any, job: Any, item: Any) -> None:
    current = [
        d for d in store.rows(config, "decisions", job["vault_id"]) if d["entity"] == item["entity"]
    ]
    expected = item["decisions"]
    for row in current:
        if row not in expected and row.get("job") != job["id"]:
            raise IntegrityError("maintenance_manual_decision_changed")
    for row in expected:
        if row not in current and not any(
            d.get("job") == job["id"] and d["moc"] == row["moc"] for d in current
        ):
            raise IntegrityError("maintenance_manual_decision_changed")


def observe(config: Any, db: Any, job: Any, mid: str) -> dict[str, Any]:
    root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
    m = next(m for m in job["mocs"] if m["id"] == mid)
    raw = packages.scoped_read(root, allowed, m["path"])
    record = moc_store.load(config, job["vault_id"], mid)
    return {
        "sha256": digest(raw),
        "revision": record.revision if record else 0,
        "members": record.members if record else {},
    }


def step(config: Any, db: Any, job: Any, item: Any, mid: str, action: str) -> None:
    key = action + ":" + mid
    if key in item.get("steps", []):
        return
    validate_decisions(config, job, item)
    # Deep removals and ordinary human replacement are implemented in B3.
    if action == "unlink":
        from .maintenance_membership import remove

        remove(config, db, job, item, mid)
        return
    decision_key = membership.decision_key(job["vault_id"], item["entity"], mid)
    prior = store.get(config, "decisions", decision_key)
    if prior and prior["state"] == "retired":
        store.put(
            config,
            "decisions",
            decision_key,
            job["vault_id"],
            {"entity": item["entity"], "moc": mid, "state": "unlocked", "job": job["id"]},
        )
    current = observe(config, db, job, mid)
    expected = job["moc_expected"][mid]
    if current != expected and maintenance_moc_recovery.manual_change(
        config, db, job, mid, current, expected
    ):
        # This external manual repair is not part of this task's compensation.
        job["moc_expected"][mid] = expected = current
        persist(config, job)
    intent = item.get("intent")
    if current != expected:
        from .. import moc_file_journal

        plans = moc_file_journal.plans(config, job["vault_id"], mid)
        history = moc_file_journal.latest(plans)
        interrupted_projection = any(
            p.status == "pending"
            and p.value["revision"] == current["revision"]
            and p.value["base_id"] == (history.plan_id if history else None)
            and p.value["after_sha256"] == current["sha256"]
            for p in plans
        )
        if not (
            intent == key
            and maintenance_moc_recovery.own_link(current, expected, item["entity"])
            and (
                current["sha256"] == expected["sha256"]
                or (history and history.value["after_sha256"] == current["sha256"])
                or interrupted_projection
            )
        ):
            raise IntegrityError("maintenance_moc_changed")
    else:
        item["intent"] = key
        persist(config, job)
    # Idempotently finish projection and note properties even if metadata was
    # committed before interruption. A completed relation is not a completed step.
    moc = next(m for m in job["mocs"] if m["id"] == mid)
    membership.apply(
        config,
        db,
        job["client_id"],
        job["vault_id"],
        item["source"],
        moc,
        record_decision=False,
        missing_block_repair=maintenance_moc_recovery.repair_base(job, mid, current["sha256"]),
    )
    item.setdefault("steps", []).append(key)
    item.pop("intent", None)
    job["moc_expected"][mid] = observe(config, db, job, mid)
    persist(config, job)


def run(config: Any, db: Any, job: Any) -> Any:
    maintenance.validate(config, db, job, sources=False)
    handled = manual_resolution.resolved(config, job)
    active = [i for i in job["items"] if i["entity"] not in handled]
    if handled and all(i["state"] == "done" for i in active):
        # Manual completion can close the last failed item after an earlier unit
        # already sealed its journal. Do not reopen that journal over external
        # manual changes merely to derive task completion.
        job.update(state="completed", completed=len(job["items"]))
        persist(config, job)
        return job
    note_moc_sync.recover_all(config, db, job["client_id"], job["vault_id"])
    job["state"] = "running"
    persist(config, job)
    from . import maintenance_undo

    item = next((i for i in active if i["state"] == "pending"), None)
    with measure(job, "undo_capture"):
        maintenance_undo.begin(config, db, job, item)
    try:
        # An interrupted archive owns its own exchange/reconciliation evidence;
        # finish that stage before inspecting Maps for the next membership.
        if not item or item.get("phase") not in {"members_done", "archived"}:
            maintenance_moc_recovery.preflight(config, db, job)
            persist(config, job)
        maintenance_mocs.create(config, db, job)
        for moc in job["new_mocs"]:
            if moc["id"] not in job["moc_expected"]:
                job["moc_expected"][moc["id"]] = observe(config, db, job, moc["id"])
        if item:
            validate_decisions(config, job, item)
            if item.get("phase") not in {"members_done", "archived"}:
                root, _ = packages.context(
                    config, db, job["client_id"], job["vault_id"], write=True
                )
                if item["kind"] == "note":
                    actual = digest(read(root, item["path"])[0])
                    if actual != item["sha256"] and not note_moc_sync.accepts(
                        config, job["vault_id"], item["entity"], item["sha256"], actual
                    ):
                        raise IntegrityError("maintenance_note_changed")
                if item["kind"] == "expert":
                    packages.load(
                        config,
                        db,
                        job["client_id"],
                        item["entity"].removeprefix("expert:"),
                        item["sha256"],
                        text_only=True,
                    )
                for mid in item["add"]:
                    step(config, db, job, item, mid, "link")
                for mid in item["remove"]:
                    step(config, db, job, item, mid, "unlink")
                if item["kind"] == "note":
                    raw, physical = read(root, item["path"])
                    item["sha256"] = digest(raw)
                    item["source"].update(sha256=digest(raw), file_key=physical)
                item["phase"] = "members_done"
                persist(config, job)
            if item["target"] and item["phase"] != "archived":
                with measure(job, "archive_links_and_files"):
                    maintenance_archive.apply(config, db, job, item)
                # Archive may update controlled MOC links and their file journals.
                for mid in job["moc_expected"]:
                    job["moc_expected"][mid] = observe(config, db, job, mid)
            item.update(state="done", changed=len(item["add"]) + len(item["remove"]))
        job["completed"] = sum(i["state"] == "done" or i["entity"] in handled for i in job["items"])
        if all(i["state"] == "done" for i in active):
            job["state"] = "completed"
            if (
                any(i.get("changed") or i.get("archive_operation") for i in job["items"])
                or job["new_mocs"]
            ):
                job["index_state"] = "pending"
    except Exception as error:
        job.update(
            state="partial",
            error=str(error)[:180]
            if isinstance(error, IntegrityError)
            else type(error).__name__ + ": " + str(error)[:120],
        )
        if "item" in locals() and item:
            item.update(state="failed", error=job["error"])
        if any(i.get("steps") or i.get("archive_operation") for i in job["items"]):
            job["index_state"] = "pending"
    try:
        with measure(job, "undo_seal"):
            maintenance_undo.seal(config, db, job)
    except Exception as error:
        job.update(state="partial", error=str(error)[:180])
    persist(config, job)
    return job
