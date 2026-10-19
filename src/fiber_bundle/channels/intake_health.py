"""Read-only admission and unresolved-batch state, independent of platform connection."""

import sqlite3
from typing import Any

from .. import automatic_policy, database, vault_save_policy
from ..config import Config
from . import intake_repository, store


def pending_review(config: Config, identity: str) -> int:
    with store.readonly(config) as db:
        if db is None or not intake_repository.existing(db):
            return 0
        batches = {
            row[0]
            for row in db.execute(
                "SELECT r.batch_id FROM channel_intake_records r WHERE r.channel_id=? "
                "AND coalesce(json_extract(r.value,'$.aborted'),0)=0 AND ("
                "r.state IN ('needs_review','archiving') OR (r.state='stored' AND EXISTS("
                "SELECT 1 FROM json_each(r.value,'$.links') l WHERE "
                "json_extract(l.value,'$.state') IN ('needs_review','link_only','metadata_only') "
                "AND coalesce(json_extract(l.value,'$.publication.state'),'')!='published')))",
                (identity,),
            )
        }
    with database.connect(config.state_root, readonly=True) as main:
        states: dict[str, set[str]] = {}
        for row in main.execute(
            "SELECT coalesce(json_extract(j.value,'$.intake.batch'),"
            "json_extract(j.value,'$.channel_batch.batch')),j.state FROM automatic_jobs j "
            "WHERE ("
            "json_extract(j.value,'$.intake.channel')=? OR "
            "json_extract(j.value,'$.channel_batch.channel')=?) AND NOT EXISTS("
            "SELECT 1 FROM automatic_jobs s WHERE s.client_id=j.client_id "
            "AND s.vault_id=j.vault_id AND s.kind=j.kind "
            "AND json_extract(s.value,'$.supersedes_job')=j.id)",
            (identity, identity),
        ):
            if row[0]:
                states.setdefault(row[0], set()).add(row[1])
        for batch, current in states.items():
            if current == {"cancelled"}:
                batches.discard(batch)
            elif current & {"needs_review", "reconcile"}:
                batches.add(batch)
    return len(batches)


def admission_reason(
    config: Config, main: sqlite3.Connection, channel: dict[str, Any], settings: dict[str, Any]
) -> str | None:
    target = settings.get("target_vault_id")
    if not settings["enabled"] or not target:
        return None
    policy = automatic_policy.get(main, target)["settings"]
    # Archive-only policies remain valid; do not activate automatic processing here.
    if (
        not automatic_policy.active(policy)
        or policy.get("client_id") != channel["value"]["client_id"]
    ):
        return None
    if not vault_save_policy.get(config, main, target)["publication_ready"]:
        return "automatic_policy_layout_changed"
    return None
