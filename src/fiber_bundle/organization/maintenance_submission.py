"""Read-only task construction and serialized, source-checked preview admission."""

import time
from typing import Any
from uuid import uuid4

from ..config import IntegrityError, InvalidInput
from . import maintenance_plan as plans
from . import packages, store


def prepare(
    config: Any,
    db: Any,
    client: str,
    vault: str,
    mode: str,
    new_mocs: Any = None,
    *,
    recovery: Any = None,
    unavailable_action: str = "moc_only",
) -> Any:
    if unavailable_action not in {"moc_only", "defer_links"}:
        raise InvalidInput("maintenance_unavailable_action_invalid")
    es, ms, fixed = plans.snapshot(
        config, db, client, vault, mode, capture_candidates=mode == "quick"
    )
    # Display evidence is frozen with the submitted report, outside source CAS
    # and execution fingerprints that evolve as admitted notes are archived.
    candidate_coverage = fixed.pop("candidate_coverage", None)
    fixed["unavailable_action"] = unavailable_action
    fresh = plans.virtual_mocs(vault, fixed["save_policy"]["settings"], ms, new_mocs or [])
    context = plans.plan_context(config, vault, ms + fresh)
    root, allowed = packages.context(config, db, client, vault, write=True)
    for m in fresh:
        if not allowed.allows(m["path"]) or (root / m["path"]).exists():
            raise IntegrityError("maintenance_moc_target_exists_or_denied")
    job = {
        "id": str(uuid4()),
        "maintenance": plans.VERSION,
        "mode": mode,
        "client_id": client,
        "vault_id": vault,
        "target": "Inbox" if mode == "quick" else "全库",
        "source": "manual",
        "state": "queued",
        "stage": "planning",
        "created_at": time.time(),
        "fixed": fixed,
        "items": [
            plans.item(config, vault, e, ms + fresh, fixed, mode, context=context) for e in es
        ],
        "new_mocs": fresh,
        "mocs": ms + fresh,
        "completed": 0,
        "model_called": False,
        "error": None,
        "permission_epoch": fixed["permission_epoch"],
        "policy": fixed["policy"],
        "confirmed": False,
        **({"candidate_coverage": candidate_coverage} if candidate_coverage else {}),
    }
    from ..inputs import canonical
    from ..objects import digest

    if recovery:
        job["cache_recovery"] = recovery
        job["items"] = [i for i in job["items"] if i["entity"] in recovery["entities"]]
        for item in job["items"]:
            item.update(evaluated=False, phase="analysis", selected=[])
            plans.difference(item, mode, fixed["save_policy"]["settings"])

    job["request_key"] = digest(
        canonical(
            [
                mode,
                client,
                vault,
                fixed,
                [[i["entity"], i["fingerprint"]] for i in job["items"]],
                fresh,
                *([candidate_coverage] if candidate_coverage else []),
                *([recovery] if recovery else []),
            ]
        ).encode()
    )
    if mode == "quick" and not job["items"] and not fresh:
        # A scoped observation with no admitted sources is a readable report;
        # it needs no queue/analysis round and has no confirmation action.
        from .maintenance import make_preview

        make_preview(config, db, job, es, ms)
    return job


def commit(config: Any, db: Any, job: Any, *, verify: bool = False) -> Any:
    from .maintenance import validate

    if verify:
        # The expensive inference ran unlocked. This source snapshot performs no
        # inference/model calls and checks each cached file signature afresh.
        validate(config, db, job)
        root, allowed = packages.context(config, db, job["client_id"], job["vault_id"], write=True)
        for moc in job["new_mocs"]:
            if (
                not allowed.allows(moc["path"])
                or (root / moc["path"]).exists()
                or (root / moc["path"]).is_symlink()
            ):
                raise IntegrityError("maintenance_moc_target_exists_or_denied")
    vault = job["vault_id"]
    for prior in store.rows(config, "jobs", vault):
        if prior.get("request_key") == job["request_key"] and prior["state"] in {
            "queued",
            "running",
            "preview",
            "blocked",
            "paused",
        }:
            try:
                validate(config, db, prior)
            except IntegrityError:
                continue
            return prior
    store.put(config, "jobs", job["id"], vault, job)
    return job
