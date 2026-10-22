"""Durable incremental plans and bounded scheduling; no extraction or model side effects."""

import sqlite3
import time
from typing import Any
from uuid import uuid4

from .. import auth, database, vault_save_policy
from ..config import Config, IntegrityError, InvalidInput
from ..inputs import canonical
from ..objects import digest
from ..vault_scope import inside
from . import catalog, classifier, membership, packages, processing, store
from .contracts import ORGANIZER_VERSION, Policy


def policy(config: Config, vault: str) -> Any:
    return store.get(config, "policies", vault) or {
        "vault_id": vault,
        "revision": 0,
        "settings": Policy().model_dump(),
        "client_id": None,
        "last_scan": 0,
    }


def save_policy(
    config: Config, db: sqlite3.Connection, client: str, vault: str, settings: Any, expected: Any
) -> Any:
    packages.context(config, db, client, vault, write=True)
    prior = policy(config, vault)
    if prior["revision"] != expected:
        raise IntegrityError("organization_policy_conflict")
    value = {
        "vault_id": vault,
        "revision": expected + 1,
        "settings": Policy.model_validate(settings).model_dump(),
        "client_id": client,
        "last_scan": 0,
    }
    store.put(config, "policies", vault, vault, value)
    return value


def _scope(config: Config, db: sqlite3.Connection, vault: str, target: str) -> Any:
    settings = vault_save_policy.get(config, db, vault)["settings"]
    if target == "Inbox":
        return lambda e: inside(e["path"], settings.get("inbox_dir", "Inbox"))
    if target in {"待分类", "全库"}:
        return lambda e: True
    return lambda e: e["id"] == target or e["path"] == target


def snapshot(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    *,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    scope_packages: bool = False,
) -> Any:
    entities, mocs, context = catalog.snapshot(
        config, db, client, vault, include=include, exclude=exclude, scope_packages=scope_packages
    )
    prior = store.get_many(config, "entities", [vault + ":" + e["id"] for e in entities])
    for e in entities:
        previous = prior.get(vault + ":" + e["id"])
        e["status"] = previous.get("status", "new") if previous else "new"
    return entities, mocs, context


def _classified(entity: Any, mocs: Any, decisions: Any) -> bool:
    relevant = [d for d in decisions if d["entity"] == entity["id"]]
    assigned = set(catalog.manual_memberships(entity, mocs)) | {
        m["id"] for m in mocs if m["members"].get(entity["id"])
    }
    assigned |= {d["moc"] for d in relevant if d["state"] == "pinned"}
    assigned -= {d["moc"] for d in relevant if d["state"] == "rejected"}
    return bool(assigned & {m["id"] for m in mocs})


def submit(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    target: str = "Inbox",
    *,
    preview: bool = False,
    key: Any = None,
    source: str = "manual",
    force: bool = False,
) -> Any:
    packages.context(config, db, client, vault, write=True)
    from . import note_moc_sync

    note_moc_sync.recover_all(config, db, client, vault)
    if key:
        existing = store.get(config, "jobs", key)
        if existing:
            if existing["client_id"] != client or existing["vault_id"] != vault:
                raise auth.Denied(404)
            return existing
    p = policy(config, vault)
    desired = (
        [vault_save_policy.get(config, db, vault)["settings"].get("inbox_dir", "Inbox")]
        if target == "Inbox"
        else [target]
        if "/" in target and not target.startswith(("note:", "expert:", "publication:"))
        else p["settings"]["include"]
    )
    read_include = []
    for path in desired:
        for base in p["settings"]["include"]:
            if inside(path, base):
                read_include.append(path)
            elif inside(base, path):
                read_include.append(base)
    entities, mocs, context = snapshot(
        config,
        db,
        client,
        vault,
        include=read_include,
        exclude=p["settings"]["exclude"],
        scope_packages=True,
    )
    decisions = store.rows(config, "decisions", vault)
    selected = []
    matches = _scope(config, db, vault, target)
    for e in entities:
        if not matches(e) or (source == "automatic" and not classifier.eligible(e, p["settings"])):
            continue
        if (target == "待分类" or source == "automatic" and e["kind"] == "expert") and _classified(
            e, mocs, decisions
        ):
            continue
        # Status is an output. Exclude it from the incremental source fingerprint.
        item = classifier.plan(e, mocs, p["settings"], decisions, context)
        old = store.get(config, "entities", vault + ":" + e["id"])
        if not force and old and old["fingerprint"] == item["fingerprint"]:
            continue
        item["state"] = "pending"
        selected.append(item)
    identity = key or str(uuid4())
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    job = {
        "permission_epoch": epoch[0] if epoch else 0,
        "read_include": read_include,
        "id": identity,
        "vault_id": vault,
        "client_id": client,
        "target": target,
        "source": source,
        "state": "preview" if preview else "queued",
        "policy": p,
        "context": context,
        "items": selected,
        "completed": 0,
        "created_at": time.time(),
        "organizer_version": ORGANIZER_VERSION,
        "model_called": False,
        "error": None,
    }
    if target == "全库":
        from .engine import taxonomy

        job["taxonomy"] = taxonomy(entities, mocs, selected)
    job["plan_sha256"] = digest(
        canonical(
            {"items": selected, "context": context, "policy": p, "client": client, "vault": vault}
        ).encode()
    )
    store.put(config, "jobs", identity, vault, job)
    return job


def get(config: Config, db: sqlite3.Connection, client: str, vault: str, identity: str) -> Any:
    packages.context(config, db, client, vault, write=True)
    value = store.get(config, "jobs", identity)
    if value is None or value["vault_id"] != vault or value["client_id"] != client:
        raise auth.Denied(404)
    return value


def action(
    config: Config, db: sqlite3.Connection, client: str, vault: str, identity: str, operation: str
) -> Any:
    job = get(config, db, client, vault, identity)
    if job.get("maintenance"):
        from .maintenance import action as maintenance_action

        return maintenance_action(config, db, job, operation, None)
    if operation == "cancel":
        job["state"] = "cancelled"
    elif operation == "confirm":
        if job["state"] != "preview":
            raise InvalidInput("organization_plan_expired")
        epoch = db.execute(
            "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
            (client, vault),
        ).fetchone()
        if (epoch[0] if epoch else 0) != job["permission_epoch"]:
            raise IntegrityError("organization_authorization_changed")
        if policy(config, vault)["revision"] != job["policy"]["revision"]:
            raise IntegrityError("organization_policy_changed")
        entities, mocs, context = snapshot(
            config,
            db,
            client,
            vault,
            include=job.get("read_include"),
            exclude=job["policy"]["settings"]["exclude"],
            scope_packages=True,
        )
        decisions = store.rows(config, "decisions", vault)
        from . import manual_resolution

        handled = manual_resolution.resolved(config, job)
        for item in job["items"]:
            if item["entity"] in handled:
                continue
            e = next((e for e in entities if e["id"] == item["entity"]), None)
            current = (
                classifier.plan(
                    e or {},
                    mocs,
                    job["policy"]["settings"],
                    decisions,
                    context,
                )
                if e
                else None
            )
            if not current or current["fingerprint"] != item["fingerprint"]:
                raise IntegrityError("organization_plan_changed")
        job["state"] = "queued"
    elif operation == "resume":
        if job.get("index_state") == "failed":
            job["index_state"] = "pending"
            job.pop("index_error", None)
            store.put(config, "jobs", job["id"], vault, job)
            return job
        if job["state"] not in {"failed", "partial", "cancelled", "blocked"}:
            raise InvalidInput("organization_job_not_resumable")
        for i in job["items"]:
            if i["state"] in {"failed", "blocked"}:
                i["state"] = "pending"
                i.pop("error", None)
                i.pop("phase", None)
        job["state"] = "queued"
        job["error"] = None
        job["completed"] = sum(i["state"] != "pending" for i in job["items"])
    else:
        raise InvalidInput("organization_action_invalid")
    store.put(config, "jobs", job["id"], vault, job)
    return job


def run(config: Config, db: sqlite3.Connection, job: Any, *, batch: int = 3) -> Any:
    if job.get("maintenance"):
        from .maintenance import run as maintenance_run

        return maintenance_run(config, db, job)
    if job["state"] not in {"queued", "running"}:
        return job
    if job.get("organizer_version") != ORGANIZER_VERSION:
        raise IntegrityError("organization_legacy_plan_replan_required")
    client, vault = job["client_id"], job["vault_id"]
    packages.context(config, db, client, vault, write=True)
    from . import note_moc_sync

    note_moc_sync.recover_all(config, db, client, vault)
    epoch = db.execute(
        "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
        (client, vault),
    ).fetchone()
    if (epoch[0] if epoch else 0) != job["permission_epoch"]:
        raise IntegrityError("organization_authorization_changed")
    if (
        job["source"] == "automatic"
        and policy(config, vault)["revision"] != job["policy"]["revision"]
    ):
        raise IntegrityError("organization_policy_changed")
    job["state"] = "running"
    store.put(config, "jobs", job["id"], vault, job)
    entities, mocs, context = snapshot(
        config,
        db,
        client,
        vault,
        include=job.get("read_include"),
        exclude=job["policy"]["settings"]["exclude"],
        scope_packages=True,
    )
    entities = {e["id"]: e for e in entities}
    mocs = {m["id"]: m for m in mocs}
    from . import manual_resolution

    handled = manual_resolution.resolved(config, job)
    for item in [i for i in job["items"] if i["state"] == "pending"][:batch]:
        if item["entity"] in handled:
            item.update(state="done", manual_completed=True, changed=0)
            job["completed"] = sum(i["state"] != "pending" for i in job["items"])
            continue
        if not item["selected"] and not item.get("evaluated") and not item.get("rejected"):
            continue
        try:
            e = entities.get(item["entity"])
            if e is None or item["sha256"] not in {
                e["sha256"],
                e.get("_classification_source", {}).get("sha256"),
            }:
                raise IntegrityError("organization_entity_changed")
            decisions = store.rows(config, "decisions", vault)
            current = classifier.plan(
                e,
                list(mocs.values()),
                job["policy"]["settings"],
                decisions,
                context,
            )
            if current["fingerprint"] != item["fingerprint"]:
                raise IntegrityError("organization_plan_changed")
            changed = 0
            suggest = (
                job["source"] == "automatic" and job["policy"]["settings"]["mode"] == "suggest"
            )
            if not suggest and item["selected"]:
                job["index_state"] = "pending"
                store.put(config, "jobs", job["id"], vault, job)
            if not suggest:
                for mid in item["selected"]:
                    changed += membership.apply(
                        config,
                        db,
                        client,
                        vault,
                        e,
                        mocs[mid],
                        owner="manual" if mid in item["manual"] else "system",
                        record_decision=False,
                    )
            item.update(state="suggested" if suggest else "done", changed=changed)
            store.put(
                config,
                "entities",
                vault + ":" + e["id"],
                vault,
                {**item, "client_id": client, "last_job": job["id"]},
            )
        except Exception as error:
            item.update(
                state="failed",
                error=str(error)[:180]
                if isinstance(error, (InvalidInput, IntegrityError, auth.Denied))
                else type(error).__name__,
            )
        job["completed"] = sum(i["state"] != "pending" for i in job["items"])
        store.put(config, "jobs", job["id"], vault, job)
    if all(i["state"] != "pending" for i in job["items"]):
        job["state"] = (
            "partial"
            if any(i["state"] in {"failed", "blocked"} for i in job["items"])
            else "completed"
        )
    if any(i.get("changed") for i in job["items"]) and job["state"] in {"completed", "partial"}:
        job["index_state"] = "pending"
    store.put(config, "jobs", job["id"], vault, job)
    return job


def request_index(config: Config, client: str, vault: str) -> None:
    identity = "index:" + vault + ":" + client
    job = {
        "id": identity,
        "vault_id": vault,
        "client_id": client,
        "target": "index",
        "source": "manual",
        "state": "completed",
        "items": [],
        "completed": 0,
        "created_at": time.time(),
        "index_state": "pending",
        "model_called": False,
        "error": None,
    }
    store.put(config, "jobs", identity, vault, job)


def notify(
    config: Config, vault: str, event: str, target: str = "Inbox", event_id: Any = None
) -> None:
    p = policy(config, vault)
    if p["settings"]["mode"] == "off" or event not in p["settings"]["events"]:
        return
    identity = digest(canonical([vault, event, event_id or target]).encode())
    prior = store.get(config, "triggers", identity)
    if prior and event_id is not None:
        return
    store.put(
        config,
        "triggers",
        identity,
        vault,
        {
            "id": identity,
            "vault_id": vault,
            "event": event,
            "target": target,
            "state": "pending",
            "policy_revision": p["revision"],
        },
    )


def tick(config: Config) -> None:
    from .execution_control import reconcile

    reconcile(config)
    # Read-only idle probe avoids creating a ledger or taking a global writer lease.
    pending = [
        j for j in store.rows(config, "jobs") if j["state"] in {"queued", "running", "cancelling"}
    ]
    triggers = [t for t in store.rows(config, "triggers") if t["state"] == "pending"]
    due = [
        p
        for p in store.rows(config, "policies")
        if p["settings"]["mode"] != "off"
        and p["settings"]["periodic"]
        and time.time() - p["last_scan"] >= p["settings"]["interval_seconds"]
    ]
    index_jobs = [
        j
        for j in store.rows(config, "jobs")
        if j.get("index_state") == "pending" and j["state"] in {"completed", "partial", "cancelled"}
    ]
    from ..vault_relocation.guard import paused

    pending = [j for j in pending if not paused(config, j["vault_id"])]
    triggers = [j for j in triggers if not paused(config, j["vault_id"])]
    due = [j for j in due if not paused(config, j["vault_id"])]
    index_jobs = [j for j in index_jobs if not paused(config, j["vault_id"])]
    if index_jobs:
        job = index_jobs[0]
        from ..note_search_build import build

        try:
            build(config, job["client_id"], job["vault_id"])
            job["index_state"] = "ready"
        except Exception as error:
            if paused(config, job["vault_id"]):
                return
            job["index_state"] = "failed"
            job["index_error"] = type(error).__name__ + ": " + str(error)[:120]
        with database.locked(config):
            store.put(config, "jobs", job["id"], job["vault_id"], job)
        return
    if not (pending or triggers or due):
        return
    if pending:
        try:
            if pending[0].get("maintenance"):
                from .maintenance_analysis import prepare as maintenance_prepare

                maintenance_prepare(config, pending[0]["id"])
            else:
                processing.prepare(config, pending[0]["id"])
        except Exception as error:
            if paused(config, pending[0]["vault_id"]):
                return
            with database.locked(config):
                current = store.get(config, "jobs", pending[0]["id"])
                if current["state"] not in {"queued", "running"} or current.get(
                    "generation", 0
                ) != pending[0].get("generation", 0):
                    return
                current.update(state="failed", error=type(error).__name__ + ": " + str(error)[:120])
                store.put(config, "jobs", current["id"], current["vault_id"], current)
        if pending[0].get("maintenance") and pending[0]["stage"] == "planning":
            return
    if pending and pending[0].get("maintenance") and pending[0]["stage"] != "planning":
        from .execution import run as execute

        execute(config, pending[0]["id"])
        return
    with database.locked(config), database.connect(config.state_root) as db:
        if pending:
            job = store.get(config, "jobs", pending[0]["id"])
            from ..vault_relocation.guard import operation

            if operation(db, job["vault_id"]):
                return
            try:
                run(config, db, job)
            except Exception as error:
                job.update(state="failed", error=type(error).__name__ + ": " + str(error)[:120])
                store.put(config, "jobs", job["id"], job["vault_id"], job)
            return
        if triggers:
            event = triggers[0]
            p = policy(config, event["vault_id"])
            if p["settings"]["mode"] != "off" and event["event"] in p["settings"]["events"]:
                admitted = submit(
                    config,
                    db,
                    p["client_id"],
                    p["vault_id"],
                    event["target"],
                    source="automatic",
                    key="event:" + event["id"],
                )
                if admitted["items"] and len(admitted["items"]) < p["settings"]["minimum_pending"]:
                    admitted.update(
                        state="completed",
                        items=[],
                        reason="未满足积压阈值；下次事件或定期检查时重新判断。",
                    )
                    store.put(config, "jobs", admitted["id"], p["vault_id"], admitted)
            event["state"] = "consumed"
            store.put(config, "triggers", event["id"], event["vault_id"], event)
            return
        p = due[0]
        settings = p["settings"]
        # Full historical traversal is a separately enabled policy choice.
        target = "全库" if settings["full_review"] else "Inbox"
        job = submit(config, db, p["client_id"], p["vault_id"], target, source="automatic")
        if len(job["items"]) < settings["minimum_pending"]:
            job["state"] = "completed"
            job["items"] = []
            store.put(config, "jobs", job["id"], p["vault_id"], job)
        p["last_scan"] = time.time()
        store.put(config, "policies", p["vault_id"], p["vault_id"], p)
