"""Read-only organization overview. Resolve membership once per visible entity."""

import sqlite3
from typing import Any

from ..config import Config
from . import (
    catalog,
    jobs,
    maintenance,
    maintenance_admission,
    manual_resolution,
    package_discovery,
    packages,
    progress_observation,
    store,
)
from .manual_relations import ManualRelations


def progress(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    *,
    validate_previews: bool = False,
) -> Any:
    packages.context(config, db, client, vault)
    work = [j for j in store.rows(config, "jobs", vault) if j["client_id"] == client]
    index = progress_observation.observe(config, vault)
    admission = maintenance_admission.snapshot(config, vault)
    visible = sorted(work, key=lambda j: -j["created_at"])[:50]
    from .maintenance_membership_validation import Observation

    maintenance_jobs = [j for j in visible if j.get("maintenance")]
    membership = Observation(config, db, maintenance_jobs) if maintenance_jobs else None
    return {
        "jobs": [
            (
                progress_observation.project(
                    maintenance.public(
                        j,
                        config=config,
                        db=db,
                        admission=admission,
                        membership_observation=membership,
                        validate_preview=validate_previews,
                    ),
                    index,
                )
                if j.get("maintenance")
                else manual_resolution.project(config, j)
            )
            for j in visible
        ],
        "package_scan": package_discovery.visible(config, db, client, vault),
        "model_called": False,
    }


def browse(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    offset: int = 0,
    query: str = "",
    exceptions: bool = True,
    moc_id: str | None = None,
) -> Any:
    entities, mocs, context = jobs.snapshot(config, db, client, vault, partial=True)
    decisions = store.rows(config, "decisions", vault)
    manual_index = ManualRelations(mocs)
    prior_rows = store.get_many(config, "entities", [vault + ":" + e["id"] for e in entities])
    results = []
    for e in entities:
        prior = prior_rows.get(vault + ":" + e["id"])
        manual = set(manual_index.memberships(e))
        members = [m["id"] for m in mocs if e["id"] in m["members"] or m["id"] in manual]
        rejected = {
            d["moc"]
            for d in decisions
            if d["entity"] == e["id"] and d["state"] in {"rejected", "retired"}
        }
        members = [mid for mid in members if mid not in rejected]
        status = (
            "linked"
            if members
            else prior["status"]
            if prior and prior["status"] != "linked"
            else "new"
        )
        if moc_id and moc_id not in members:
            continue
        if exceptions and status == "linked":
            continue
        if (
            query
            and query.casefold() not in (e["title"] + " " + e["path"] + " " + e["id"]).casefold()
        ):
            continue
        results.append(
            {
                **catalog.public_entity(e),
                "status": status,
                "moc_ids": members,
                "organization": prior,
            }
        )
    return {
        **progress(config, db, client, vault, validate_previews=True),
        "policy": jobs.policy(config, vault),
        "items": results[offset : offset + 100],
        "total": len(results),
        "coverage": context["coverage"],
        "next_offset": offset + 100 if offset + 100 < len(results) else None,
        "mocs": [{k: v for k, v in m.items() if k != "text"} for m in mocs],
        "packages": packages.listing(config, db, client, vault),
        "model_called": False,
    }
