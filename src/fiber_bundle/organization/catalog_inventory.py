"""Read-only, explicitly incomplete catalogs for previews; never restore source bodies."""

import json
import os
import sqlite3
import stat
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import cloud_files, note_inventory, vault_scope, vaults
from ..config import Config, IntegrityError
from ..vault_sync import scanner, store
from . import text_features


class Unavailable(IntegrityError):
    def __init__(
        self,
        code: str,
        issues: dict[str, str],
        changes: dict[str, list[str]] | None = None,
    ) -> None:
        super().__init__(code)
        self.issues = [
            {"path": p, "reason": r}
            | ({"changed_fields": changes[p]} if changes and p in changes else {})
            for p, r in sorted(issues.items())
        ][:100]


def coverage(errors: dict[str, str]) -> dict[str, Any]:
    return {
        "complete": not errors,
        "unavailable_count": len(errors),
        "unavailable": [{"path": p, "reason": r} for p, r in sorted(errors.items())][:100],
    }


def _entry(path: str, directory: bool) -> bool:
    return (
        directory
        or path.lower().endswith(".md")
        or path.rpartition("/")[2]
        in {".fiber-publication.json", ".fiber-analysis.json", ".knowledge-organizer-policy.json"}
    )


def _directories(
    root: Path,
    files: dict[str, Any],
    scope: vault_scope.Scope,
    excluded: Callable[[str], bool],
    checked: dict[str, str],
) -> None:
    # A new/deleted entry must not slip into a frozen preview before its event is observed.
    # Directory entry metadata also changes for excluded editor files and siblings.
    # Prove the relevant name set, while retaining identity/permissions/cloud checks.
    for path, item in files.items():
        if item["directory"]:
            cloud_files.require_local(root / path)
            current = scanner.signature((root / path).lstat())
            if current == item["signature"] or current == checked.get(path):
                continue
            before, after = json.loads(item["signature"]), json.loads(current)
            if any(before[i] != after[i] for i in (0, 1, 2, 7, 8)):
                fields = [
                    name
                    for index, name in enumerate(
                        [
                            "device",
                            "inode",
                            "mode",
                            "links",
                            "size",
                            "mtime",
                            "ctime",
                            "birthtime",
                            "flags",
                        ]
                    )
                    if before[index] != after[index]
                ]
                raise Unavailable(
                    "organization_catalog_changed",
                    {path: "organization_directory_metadata_changed"},
                    {path: fields},
                )
            expected = {
                p
                for p, row in files.items()
                if p and p.rpartition("/")[0] == path and _entry(p, row["directory"])
            }
            names: set[str] = set()
            with os.scandir(root / path) as entries:
                for count, entry in enumerate(entries, 1):
                    if count > 10000:
                        raise Unavailable(
                            "organization_catalog_changed",
                            {path: "organization_directory_entry_limit"},
                        )
                    child = (path + "/" if path else "") + entry.name
                    # Match observer exclusions before touching any child metadata.
                    if (
                        store.accepted(scope, child)
                        and not excluded(child)
                        and _entry(child, entry.is_dir(follow_symlinks=False))
                    ):
                        names.add(child)
            if names != expected:
                raise Unavailable(
                    "organization_catalog_changed",
                    {path: "organization_directory_names_changed"},
                )
            if scanner.signature((root / path).lstat()) != current:
                raise Unavailable(
                    "organization_catalog_changed",
                    {path: "organization_directory_changed_during_read"},
                )
            checked[path] = current


def _observed(
    config: Config,
    vault: str,
    scope: vault_scope.Scope,
    excluded: Callable[[str], bool],
    root: Path,
) -> tuple[dict[str, Any], dict[str, str], list[str]]:
    def relevant(path: str) -> bool:
        return scope.reachable(path) and not excluded(path)

    with store.connection(config) as side:
        assert side is not None
        files = {
            r["path"]: dict(r)
            for r in side.execute("SELECT * FROM files WHERE vault=?", (vault,))
            if relevant(r["path"])
        }
        errors = {
            r["path"]: r["reason"]
            for r in side.execute("SELECT path,reason FROM issues WHERE vault=?", (vault,))
            if relevant(r["path"])
        }
        all_events = {
            r[0]: r[1]
            for r in side.execute("SELECT path,flags FROM pending WHERE vault=?", (vault,))
        }
        events = {path: flags for path, flags in all_events.items() if relevant(path)}
    pending = []
    changed_parents = {child.rpartition("/")[0] for child in all_events if child}
    for path, flags in events.items():
        if not flags and files.get(path, {}).get("directory") and path in changed_parents:
            continue  # The direct child event carries the evidence; names are checked live.
        if not flags & 1 and not _entry(path, False):
            try:
                cloud_files.require_local((root / path).parent)
                if stat.S_ISREG((root / path).lstat().st_mode):
                    continue  # A named ordinary attachment cannot alter Markdown membership.
            except FileNotFoundError:
                if path in files and not files[path]["directory"]:
                    continue
        pending.append(path)
    return files, errors, pending


def _file_token(files: dict[str, Any]) -> dict[str, Any]:
    # An ancestor's unrelated child can change its size/timestamps/link count.
    # Relevant names and stable directory identity are separately checked live.
    return {
        path: [json.loads(row["signature"])[i] for i in (0, 1, 2, 7, 8)]
        if row["directory"]
        else row["signature"]
        for path, row in files.items()
        if _entry(path, row["directory"])
    }


def read(
    config: Config,
    db: sqlite3.Connection,
    vault: sqlite3.Row,
    settings: dict[str, Any],
    *,
    on_skip: Callable[[str, str], None] | None = None,
) -> tuple[note_inventory.Inventory, dict[str, Any]]:
    from ..operation_dependencies import check

    check(config, db, vault["id"])
    root = vaults.checked_vault(config, vault)
    scope = vault_scope.for_vault(root, vault)
    authority_sql = (
        "SELECT status,attachment_state,relative_path,absolute_root,root_kind,"
        "directory_dev,directory_ino,scope_json,read_only FROM vaults WHERE id=?"
    )
    authority = tuple(db.execute(authority_sql, (vault["id"],)).fetchone() or ())
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
            or time.time() - status["heartbeat"] >= 45
        ):
            raise Unavailable(
                "organization_catalog_pending", {"": "organization_catalog_observation_pending"}
            )
        files, errors, pending = _observed(config, vault["id"], scope, excluded, root)
        if "" not in files or not files[""]["directory"]:
            raise Unavailable(
                "organization_catalog_pending", {"": "organization_catalog_observation_pending"}
            )
        if pending:
            raise Unavailable(
                "organization_catalog_pending",
                {path: "organization_catalog_observation_pending" for path in pending},
            )
        # An unavailable directory has unknown children; do not infer an empty catalog.
        if any(p not in files or files[p]["directory"] for p in errors):
            raise Unavailable("organization_catalog_pending", errors)
        known = {p: (json.loads(r["signature"])[2], r["size"]) for p, r in files.items()}
        _directories(root, files, scope, excluded, checked)
    observed_errors = dict(errors)
    inventory = note_inventory.read(
        config,
        db,
        vault,
        exclude_path=excluded,
        known_entries=known,
        note_errors=errors,
        on_skip=on_skip,
    )
    if status is not None:
        assert library is not None
        _directories(root, files, scope, excluded, checked)
        after = store.status(config, vault["id"])
        after_library = store.library(config, vault["id"])
        after_files, after_errors, pending = _observed(config, vault["id"], scope, excluded, root)
        if (
            after is None
            or after_library is None
            or any(after_library[k] != library[k] for k in ("binding", "root", "scope"))
            or not after["baseline"]
            or pending
            or _file_token(files) != _file_token(after_files)
            or observed_errors != after_errors
            or time.time() - after["heartbeat"] >= 45
        ):
            raise Unavailable(
                "organization_catalog_changed", {"": "organization_catalog_observation_changed"}
            )
    if (
        tuple(db.execute(authority_sql, (vault["id"],)).fetchone() or ()) != authority
        or vault_scope.for_vault(root, vault) != scope
    ):
        raise Unavailable(
            "organization_catalog_changed", {"": "organization_catalog_observation_changed"}
        )
    moc_dir = settings.get("moc_dir", "Map")
    if any(vault_scope.inside(p, moc_dir) for p in errors):
        raise Unavailable("organization_moc_unavailable", errors)
    return inventory, coverage(errors)
