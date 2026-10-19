"""Recheck publication scope and independently owned MOC deltas before cleanup commits."""

import sqlite3
from pathlib import Path
from typing import Any

from . import auth, batch_moc_contributions, note_migration_store, vaults, weixin_publication
from . import publication_organization_store as organization
from .config import Config, IntegrityError
from .weixin_events import Binding


def check(
    config: Config,
    journal: sqlite3.Connection,
    db: sqlite3.Connection,
    binding: Binding,
    batch: str,
    item: dict[str, Any],
    *,
    links_only: bool = False,
) -> Path:
    auth.grant(db, binding.client_id, binding.vault_id, "add")
    row = auth.grant(db, binding.client_id, binding.vault_id, "query")
    root = vaults.checked_vault(config, row)
    info = root.stat()
    if [info.st_dev, info.st_ino] != item["vault_key"]:
        raise IntegrityError("revocation Vault replaced")
    note_migration_store.guard(config, binding.vault_id)
    pub = db.execute(
        "SELECT * FROM article_publications WHERE id=?", (item["publication_id"],)
    ).fetchone()
    runs = weixin_publication.planned_locked(journal, binding, batch)
    consumers = {
        r[0]
        for r in db.execute(
            "SELECT run_id FROM article_publication_runs WHERE publication_id=?",
            (item["publication_id"],),
        )
    }
    if (
        pub is None
        or pub["vault_id"] != binding.vault_id
        or pub["state"] != "published"
        or not consumers.intersection(runs)
        or (not links_only and consumers - set(runs))
    ):
        raise IntegrityError("revocation publication became shared or moved")
    for change in item.get("moc_changes", []):
        if organization.claimed_elsewhere(
            config, binding.vault_id, item["publication_id"], change["moc_id"], runs
        ):
            raise IntegrityError("revocation MOC contribution became shared")
    batch_moc_contributions.check(config, binding.vault_id, item, links_only=links_only)
    return root
