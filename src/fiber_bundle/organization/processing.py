"""Classification I/O outside the global writer lock; writes revalidate the frozen plan."""

import subprocess
from typing import Any

from .. import database, note_index, published_expert
from ..config import IntegrityError, InvalidInput
from ..knowledge_graph import policy as graph_policy
from . import classifier, manual_resolution, model_evidence, model_review, packages, semantic, store
from .contracts import ORGANIZER_VERSION


def unchanged(config: Any, db: Any, job: Any) -> bool:
    """A new command can resume the frozen plan, or create a fresh explicit plan."""
    from . import jobs

    if jobs.policy(config, job["vault_id"])["revision"] != job["policy"]["revision"]:
        return False
    entities, mocs, context = jobs.snapshot(
        config,
        db,
        job["client_id"],
        job["vault_id"],
        include=job.get("read_include"),
        exclude=job["policy"]["settings"]["exclude"],
        scope_packages=True,
    )
    if context != job["context"]:
        return False
    decisions = store.rows(config, "decisions", job["vault_id"])
    by_id = {e["id"]: e for e in entities}
    handled = manual_resolution.resolved(config, job)
    return all(
        item["entity"] in by_id
        and classifier.plan(
            by_id[item["entity"]], mocs, job["policy"]["settings"], decisions, context
        )["fingerprint"]
        == item["fingerprint"]
        for item in job["items"]
        if item["state"] in {"failed", "blocked"} and item["entity"] not in handled
    )


def prepare(config: Any, identity: str) -> None:
    from . import jobs

    with database.locked(config), database.connect(config.state_root) as db:
        job = store.get(config, "jobs", identity)
        if not job or job["state"] not in {"queued", "running"}:
            return
        if job["organizer_version"] != ORGANIZER_VERSION:
            job.update(state="failed", error="organization_legacy_plan_replan_required")
            store.put(config, "jobs", identity, job["vault_id"], job)
            return
        handled = manual_resolution.resolved(config, job)
        # Strong approved rules/tags/manual links need no model call.
        pending = next(
            (
                i
                for i in job["items"]
                if i["state"] == "pending"
                and i["entity"] not in handled
                and not i["selected"]
                and not i.get("evaluated")
            ),
            None,
        )
        if pending is None:
            return
        entities, mocs, context = jobs.snapshot(
            config,
            db,
            job["client_id"],
            job["vault_id"],
            include=job.get("read_include"),
            exclude=job["policy"]["settings"]["exclude"],
            scope_packages=True,
        )
        entity = next((e for e in entities if e["id"] == pending["entity"]), None)
        if entity is None:
            raise IntegrityError("organization_entity_changed")
        initial = classifier.plan(
            entity,
            mocs,
            job["policy"]["settings"],
            store.rows(config, "decisions", job["vault_id"]),
            context,
        )
        if initial["fingerprint"] != pending["fingerprint"]:
            raise IntegrityError("organization_plan_changed")
        pending["phase"] = "semantic"
        job["state"] = "running"
        store.put(config, "jobs", identity, job["vault_id"], job)
        graph = graph_policy.current(config)
        graph_digest = graph.digest if graph else None
        model_digest = None
        try:
            model_digest = model_review.route(config, db, job["vault_id"]).digest
        except InvalidInput:
            pass

    def check() -> None:
        with database.locked(config), database.connect(config.state_root) as db:
            current = store.get(config, "jobs", identity)
            if not current or current["state"] not in {"queued", "running"}:
                raise InvalidInput("organization_cancelled")
            if entity["id"] in manual_resolution.resolved(config, current):
                raise InvalidInput("organization_manual_completed")
            root, allowed = packages.context(
                config, db, job["client_id"], job["vault_id"], write=True
            )
            if not allowed.allows(entity["path"]):
                raise IntegrityError("organization_authorization_changed")
            if entity["kind"] == "note":
                if (
                    note_index.read_note(root, entity["path"], 1024 * 1024).sha256
                    != entity["sha256"]
                ):
                    raise IntegrityError("organization_entity_changed")
            else:
                module = published_expert.text(
                    config,
                    db,
                    job["client_id"],
                    entity["id"].removeprefix("expert:"),
                    entity["version"],
                )
                if module["version"] != entity["version"]:
                    raise IntegrityError("organization_entity_changed")
            epoch = db.execute(
                "SELECT revision FROM graph_permission_epochs WHERE client_id=? AND vault_id=?",
                (job["client_id"], job["vault_id"]),
            ).fetchone()
            if (epoch[0] if epoch else 0) != job["permission_epoch"]:
                raise IntegrityError("organization_authorization_changed")
            if jobs.policy(config, job["vault_id"])["revision"] != job["policy"]["revision"]:
                raise IntegrityError("organization_policy_changed")
            graph_now = graph_policy.current(config)
            if (graph_now.digest if graph_now else None) != graph_digest:
                raise IntegrityError("organization_semantic_policy_changed")
            try:
                now = model_review.route(config, db, job["vault_id"]).digest
            except InvalidInput:
                now = None
            if now != model_digest:
                raise IntegrityError("organization_model_policy_changed")

    try:
        check()
        try:
            recalled = semantic.recall(config, job, entity, mocs, check)
        except (OSError, subprocess.SubprocessError, InvalidInput) as error:
            # Semantic infrastructure failure may use the configured text model;
            # it is never interpreted as a positive match.
            recalled = {"state": "unavailable", "error": type(error).__name__}
        pending["semantic"] = recalled
        with database.locked(config):
            active = store.get(config, "jobs", identity)
            if active["state"] not in {"queued", "running"}:
                raise InvalidInput("organization_cancelled")
            next(i for i in active["items"] if i["entity"] == pending["entity"])["phase"] = "model"
            store.put(config, "jobs", identity, job["vault_id"], active)
        result = model_review.resolve(config, job, entity, mocs, pending, check)
        check()
        with database.locked(config), database.connect(config.state_root) as db:
            current = store.get(config, "jobs", identity)
            entities_now, mocs_now, context_now = jobs.snapshot(
                config,
                db,
                job["client_id"],
                job["vault_id"],
                include=job.get("read_include"),
                exclude=job["policy"]["settings"]["exclude"],
                scope_packages=True,
            )
            e = next((e for e in entities_now if e["id"] == entity["id"]), None)
            plan = (
                classifier.plan(
                    e,
                    mocs_now,
                    job["policy"]["settings"],
                    store.rows(config, "decisions", job["vault_id"]),
                    context_now,
                )
                if e
                else None
            )
            if not plan or plan["fingerprint"] != pending["fingerprint"]:
                raise IntegrityError("organization_plan_changed")
            item = next(i for i in current["items"] if i["entity"] == pending["entity"])
            item.update(result, semantic=recalled, evaluated=True, phase="ready")
            item.pop("error", None)
            item.pop("error_detail", None)
            current.pop("error_detail", None)
            current["model_called"] |= result["model_called"]
            store.put(config, "jobs", identity, job["vault_id"], current)
    except Exception as error:
        with database.locked(config):
            current = store.get(config, "jobs", identity)
            if current and pending["entity"] in manual_resolution.resolved(config, current):
                return
            if current and current["state"] in {"queued", "running"}:
                item = next(i for i in current["items"] if i["entity"] == pending["entity"])
                code = (
                    str(error)
                    if isinstance(error, (InvalidInput, IntegrityError))
                    else type(error).__name__
                )
                item.update(
                    state="blocked",
                    status="blocked",
                    error=code[:180],
                    semantic=pending.get("semantic"),
                )
                current["error"] = code[:180]
                item.pop("error_detail", None)
                current.pop("error_detail", None)
                if isinstance(error, model_evidence.EvidenceInvalid):
                    item["error_detail"] = current["error_detail"] = error.reason
                store.put(config, "jobs", identity, job["vault_id"], current)
