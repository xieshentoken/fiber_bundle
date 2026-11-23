"""Recheck direct policy files before work; never enumerate to discover policy changes."""

from typing import Any

from .. import database, vault_catalog, vault_scope
from ..config import Config, IntegrityError
from ..inputs import canonical
from . import store


def current(config: Config, row: dict[str, Any]) -> vault_scope.Scope:
    previous = store.scope_of(row)
    if not (config.state_root / "metadata.sqlite3").exists():
        return previous
    with database.connect(config.state_root, readonly=True) as main:
        vault = main.execute("SELECT * FROM vaults WHERE id=?", (row["id"],)).fetchone()
        if vault is None or vault["status"] != "active" or vault["attachment_state"] != "attached":
            raise IntegrityError("vault_sync_scope_changed")
        root = vault_catalog.physical_path(config, vault)
        if str(root) != row["root"]:
            raise IntegrityError("vault_sync_root_changed")
        scope = vault_scope.for_vault(root, vault)
    if scope == previous:
        return scope
    with store.connection(config, write=True) as side:
        assert side is not None
        for table in ("files", "pending", "directory_scans", "issues"):
            for item in side.execute(
                "SELECT path FROM " + table + " WHERE vault=?", (row["id"],)
            ).fetchall():
                if not store.accepted(scope, item[0]):
                    side.execute(
                        "DELETE FROM " + table + " WHERE vault=? AND path=?", (row["id"], item[0])
                    )
        side.execute(
            "UPDATE libraries SET scope=?,generation=generation+1,state='index_pending' WHERE id=?",
            (canonical(scope.wire()), row["id"]),
        )
    # A policy edit is a rare explicit scope change: reconcile only authorized names.
    if (scope.include, scope.exclude) != (previous.include, previous.exclude):
        store.enqueue(config, row["id"], [("", 1)], row["cursor"])
    return scope
