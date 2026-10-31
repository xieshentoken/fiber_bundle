"""Read-only, explicitly incomplete catalogs for previews; never restore source bodies."""

import json
import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import cloud_files, note_inventory, vault_scope, vaults
from ..config import Config, IntegrityError
from ..vault_sync import scanner, store
from . import text_features


class Unavailable(IntegrityError):
    def __init__(self, code: str, issues: dict[str, str]) -> None:
        super().__init__(code)
        self.issues = [{"path": p, "reason": r} for p, r in sorted(issues.items())][:100]


def coverage(errors: dict[str, str]) -> dict[str, Any]:
    return {
        "complete": not errors,
        "unavailable_count": len(errors),
        "unavailable": [{"path": p, "reason": r} for p, r in sorted(errors.items())][:100],
    }


def _directories(
    root: Path,
    files: dict[str, Any],
    scope: vault_scope.Scope,
    excluded: Callable[[str], bool],
    checked: dict[str, str],
) -> None:
    # A new/deleted entry must not slip into a frozen preview before its event is observed.
    # Only known authorized directories are checked. A ctime-only drift permits
    # one bounded name-set check; never ignore it or scan the entire Vault.
    for path, item in files.items():
        if item["directory"]:
            cloud_files.require_local(root / path)
            current = scanner.signature((root / path).lstat())
            if current == item["signature"] or current == checked.get(path):
                continue
            before, after = json.loads(item["signature"]), json.loads(current)
            if checked or before[:6] + before[7:] != after[:6] + after[7:]:
                raise Unavailable("organization_catalog_changed", {})
            expected = {p for p in files if p and p.rpartition("/")[0] == path}
            names: set[str] = set()
            with os.scandir(root / path) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > 10000:
                        raise Unavailable("organization_catalog_changed", {})
                    child = (path + "/" if path else "") + entry.name
                    # Match observer exclusions before touching any child metadata.
                    if store.accepted(scope, child) and not excluded(child):
                        names.add(child)
            if names != expected or scanner.signature((root / path).lstat()) != current:
                raise Unavailable("organization_catalog_changed", {})
            checked[path] = current


def read(
    config: Config, db: sqlite3.Connection, vault: sqlite3.Row, settings: dict[str, Any]
) -> tuple[note_inventory.Inventory, dict[str, Any]]:
    root = vaults.checked_vault(config, vault)
    scope = vault_scope.for_vault(root, vault)
    errors: dict[str, str] = {}
    known = None
    files: dict[str, Any] = {}
    checked: dict[str, str] = {}
    status = store.status(config, vault["id"])
    library = store.library(config, vault["id"])

    def excluded(path: str) -> bool:
        return text_features.excluded(path, settings)

    if status is not None:
        if (
            not library
            or library["root"] != str(root)
            or not status["baseline"]
            or status["active_pending"]
            or time.time() - status["heartbeat"] >= 45
        ):
            raise Unavailable("organization_catalog_pending", {})
        with store.connection(config) as side:
            assert side is not None
            files = {
                r["path"]: dict(r)
                for r in side.execute("SELECT * FROM files WHERE vault=?", (vault["id"],))
                if scope.reachable(r["path"]) and not excluded(r["path"])
            }
            errors = {
                r["path"]: r["reason"]
                for r in side.execute(
                    "SELECT path,reason FROM issues WHERE vault=?", (vault["id"],)
                )
                if scope.reachable(r["path"]) and not excluded(r["path"])
            }
        # An unavailable directory has unknown children; do not infer an empty catalog.
        if any(p not in files or files[p]["directory"] for p in errors):
            raise Unavailable("organization_catalog_pending", errors)
        known = {p: (json.loads(r["signature"])[2], r["size"]) for p, r in files.items()}
        _directories(root, files, scope, excluded, checked)
    inventory = note_inventory.read(
        config, db, vault, exclude_path=excluded, known_entries=known, note_errors=errors
    )
    if status is not None:
        _directories(root, files, scope, excluded, checked)
        after = store.status(config, vault["id"])
        if (
            after is None
            or after["generation"] != status["generation"]
            or after["active_pending"]
            or time.time() - after["heartbeat"] >= 45
        ):
            raise Unavailable("organization_catalog_changed", {})
    moc_dir = settings.get("moc_dir", "Map")
    if any(vault_scope.inside(p, moc_dir) for p in errors):
        raise Unavailable("organization_moc_unavailable", errors)
    return inventory, coverage(errors)
