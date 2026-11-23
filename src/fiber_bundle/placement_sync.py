"""Durable own-write observation, derived from the committed placement operation journal."""

import sqlite3
from pathlib import Path
from typing import Any

from . import auth, vault_directories
from .config import Config, IntegrityError
from .vault_sync import scope_guard, store


def committed(
    config: Config, db: sqlite3.Connection, operation: str, value: dict[str, Any], root: Path
) -> None:
    """A failed notification leaves metadata_committed/applying available for explicit recovery.

    The side-ledger receipt and pending paths commit together. A restart after
    enqueue but before the caller's completed marker therefore cannot duplicate
    notifications, reset quiet windows, or replay a file exchange.
    """
    if not value.get("metadata_committed"):
        return
    for permission in ("query", "add"):
        auth.grant(db, value["client_id"], value["vault_id"], permission)
    actual, allowed, _ = vault_directories.vault_root(config, db, value["vault_id"])
    expected = value.get("root_key") or value.get("fixed", {}).get("root_key")
    if actual != root or expected is not None and vault_directories.key(root) != expected:
        raise IntegrityError("placement_policy_or_vault_changed")
    paths = set(value["moves"]) | set(value["moves"].values())
    paths.update(u["path"] for u in value["updates"])
    paths.update(u["target"] for u in value["updates"])
    paths.update(p["path"] for p in value.get("moc_file_projections", []))
    paths.update(p for p in (value.get("source_root"), value.get("target_root")) if p)
    if any(not allowed.allows(path) for path in paths):
        raise IntegrityError("placement_scope_denied")
    if not paths:
        return
    observed = store.library(config, value["vault_id"])
    if observed is None:
        # An installation without an observer keeps its original index behavior.
        # Registering the observer later starts its existing full metadata baseline.
        return
    if observed["root"] != str(root):
        raise IntegrityError("vault_sync_root_changed")
    scope_guard.current(config, observed)
    flags = dict.fromkeys(paths, 1)
    for path in paths:
        parent = path.rpartition("/")[0]
        while True:
            # Our staging/exchanges also change ancestor directory metadata.
            # Root/parents are shallow observations, never a recursive root scan.
            flags.setdefault(parent, 0)
            if not parent:
                break
            parent = parent.rpartition("/")[0]
    events = sorted(flags.items())
    for offset in range(0, len(events), 2048):
        store.enqueue(
            config,
            value["vault_id"],
            events[offset : offset + 2048],
            observed["cursor"],
            notification_id="placement:" + operation + ":" + str(offset // 2048),
        )
