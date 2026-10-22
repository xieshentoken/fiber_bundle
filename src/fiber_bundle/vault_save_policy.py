"""Draft save rules, metadata-only preflight and explicit directory creation journal."""

import json
import os
import sqlite3
import time
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import cloud_files, database, file_exchange, moc_store, vault_directories, vault_scope
from .config import Config, StorageError, contained, sync_directory
from .inputs import canonical
from .vault_save_contract import SaveSettings, SettingsError, settings_wire


def pending(db: sqlite3.Connection, identity: str) -> dict[str, Any] | None:
    row = db.execute(
        "SELECT id,state FROM vault_operations WHERE vault_id=? AND kind='save_policy' "
        "AND state IN ('prepared','creating','checking','reconcile') "
        "ORDER BY created_at DESC LIMIT 1",
        (identity,),
    ).fetchone()
    return dict(row) if row else None


def _scope_binding(
    expected: Any,
    current: dict[str, object],
    root: Path,
    *,
    catalog_exclude: tuple[str, ...] = (),
) -> bool:
    """Accept only the built-in protection added after a legacy policy was saved.

    A relocated root may carry that same protection explicitly in its catalog.
    No settings are rewritten and no permission is widened. User policy changes,
    grant changes and directory identity changes still invalidate the binding.
    """
    if expected == current:
        return True
    if not isinstance(expected, dict) or set(expected) != set(current):
        return False
    if any(expected[k] != current[k] for k in ("include", "policy_sha256")):
        return False
    before, after = expected["exclude"], current["exclude"]
    if not isinstance(before, list) or not isinstance(after, list):
        return False
    if not all(isinstance(p, str) for v in (before, after) for p in v):
        return False
    protection = "01-Projects/GF_supply_chain"
    if (
        protection in catalog_exclude
        and protection in before
        and protection not in after
        and set(before) == set(after) | {protection}
    ):
        return True
    implicit = {"01-Projects/GF_supply_chain"} if root.name == "L2" else set()
    if root in vault_scope.PROTECTED.parents:
        implicit.add(vault_scope.PROTECTED.relative_to(root).as_posix())
    return bool(implicit) and set(after) == set(before) | implicit


def get(config: Config, db: sqlite3.Connection, identity: str) -> dict[str, Any]:
    root, scope, vault = vault_directories.vault_root(config, db, identity)
    row = db.execute("SELECT * FROM vault_save_policies WHERE vault_id=?", (identity,)).fetchone()
    value = json.loads(row["value"]) if row else None
    try:
        current = value is None or (
            value["root_key"] == vault_directories.key(root)
            and _scope_binding(value["effective_scope"], scope.wire(), root)
            and all(
                vault_directories.key(vault_directories.parent_path(root, scope, path)) == key
                for path, key in value["directories"].items()
            )
            and all(
                _scope_binding(
                    fingerprint,
                    vault_scope.effective(root / parent, {"include": [""], "exclude": []}).wire(),
                    root / parent,
                    catalog_exclude=scope.exclude if parent == "" else (),
                )
                for parent, fingerprint in value["parent_scopes"].items()
            )
        )
    except (OSError, StorageError):
        current = False
    mocs = []
    with moc_store.connection(config) as side:
        mids = (
            [
                r[0]
                for r in side.execute(
                    "SELECT id FROM mocs WHERE vault_id=? ORDER BY id LIMIT 101", (identity,)
                )
            ]
            if side is not None
            else []
        )
    for mid in mids:
        metadata = moc_store.load(config, identity, mid)
        if metadata and [metadata.vault_dev, metadata.vault_ino] == vault_directories.key(root):
            mocs.append({"id": mid, "title": metadata.title})
    displayed = mocs[:100]
    known = {m["id"] for m in displayed}
    for mid in value["settings"]["moc_destinations"] if value else []:
        if mid not in known:
            displayed.append({"id": mid, "title": "未列出或已失效的 MOC（可清除映射）"})
    return {
        "vault_id": identity,
        "revision": row["revision"] if row else 0,
        "settings": settings_wire(SaveSettings.model_validate(value["settings"]))
        if value
        else settings_wire(SaveSettings()),
        "state": row["state"] if row else "unconfigured",
        "binding_current": current,
        "publication_ready": bool(row and row["state"] == "active" and current),
        "effective_mode": value["settings"]["layout_mode"]
        if row and value and row["state"] == "active" and current
        else "legacy_bundle",
        "read_only": bool(vault["read_only"]),
        "pending_operation": pending(db, identity),
        "mocs": displayed,
        "mocs_truncated": len(mocs) > 100,
    }


def alias(root: Path, relative: str, name: str, scope: vault_scope.Scope) -> None:
    wanted = unicodedata.normalize("NFC", name).casefold()
    with (
        vault_directories.opened_parent(root, scope, relative) as (_, fd),
        os.scandir(fd) as entries,
    ):
        for count, entry in enumerate(entries):
            if count >= vault_directories.SCAN_LIMIT:
                raise SettingsError("save_policy_directory_limit")
            candidate = relative + "/" + entry.name if relative else entry.name
            if (
                scope.reachable(candidate)
                and entry.name != name
                and (unicodedata.normalize("NFC", entry.name).casefold() == wanted)
            ):
                raise SettingsError("save_policy_path_alias")


def observe(
    config: Config,
    db: sqlite3.Connection,
    identity: str,
    settings: SaveSettings,
    revision: int,
    *,
    own_operation: str | None = None,
) -> dict[str, Any]:
    root, scope, vault = vault_directories.vault_root(config, db, identity)
    current = get(config, db, identity)
    if current["revision"] != revision:
        raise SettingsError("save_policy_revision_conflict", 409)
    if current["pending_operation"] and current["pending_operation"]["id"] != own_operation:
        raise SettingsError("save_policy_reconciliation_required", 409)
    if vault["read_only"]:
        raise SettingsError("save_policy_read_only")
    from .vault_directory_roles import directories as role_directories

    paths = role_directories(settings)
    for mid in settings.moc_destinations:
        moc = moc_store.load(config, identity, mid)
        if moc is None or [moc.vault_dev, moc.vault_ino] != vault_directories.key(root):
            raise SettingsError("save_policy_moc_unavailable")
    directories: dict[str, list[int] | None] = {"": vault_directories.key(root)}
    parent_scopes: dict[str, dict[str, object]] = {}
    if settings.layout_mode == "obsidian_folders":
        for path in sorted(set(paths)):
            if not scope.allows(path):
                raise SettingsError("save_policy_scope_denied")
            parent = ""
            for component in path.split("/"):
                relative = parent + "/" + component if parent else component
                if relative in directories:
                    parent = relative
                    continue
                if not scope.reachable(relative):
                    raise SettingsError("save_policy_scope_denied")
                if directories[parent] is not None:
                    inherited = vault_scope.effective(
                        root / parent, {"include": [""], "exclude": []}
                    )
                    parent_scopes[parent] = inherited.wire()
                    if not inherited.allows(component):
                        raise SettingsError("save_policy_scope_denied")
                    alias(root, parent, component, scope)
                    candidate = contained(root, relative)
                    try:
                        cloud_files.require_available(candidate)
                        directories[relative] = vault_directories.key(candidate)
                        if not os.access(candidate, os.W_OK | os.X_OK):
                            raise SettingsError("save_policy_directory_not_writable")
                    except FileNotFoundError:
                        if not scope.allows(relative):
                            raise SettingsError("save_policy_scope_denied") from None
                        directories[relative] = None
                else:
                    if not scope.allows(relative):
                        raise SettingsError("save_policy_scope_denied")
                    directories[relative] = None
                parent = relative
        if len(directories) > 128:
            raise SettingsError("save_policy_directory_limit")
    return {
        "vault_id": identity,
        "revision": revision,
        "settings": settings_wire(settings),
        "root_key": vault_directories.key(root),
        "effective_scope": scope.wire(),
        "directories": directories,
        "parent_scopes": parent_scopes,
    }


def preview(
    config: Config,
    db: sqlite3.Connection,
    identity: str,
    settings: SaveSettings,
    revision: int,
    *,
    enable_publication: bool = False,
) -> dict[str, Any]:
    value = observe(config, db, identity, settings, revision)
    token = vault_directories.persist(
        db, "save_policy", {**value, "enable_publication": enable_publication}
    )
    note = settings.inbox_dir + "/示例文章.md"
    image = settings.image_dir + "/示例图片-01.png"
    return {
        "preview_id": token,
        "vault_id": identity,
        "expected_revision": revision,
        "settings": value["settings"],
        "create_directories": [p for p, key in value["directories"].items() if key is None],
        "paths": {
            "note": note,
            "archived_note": settings.archive_dir + "/示例文章.md",
            "image": image,
            "expert_package": (
                settings.book_dir if settings.policy_version == 2 else settings.inbox_dir
            )
            + "/示例书籍-Draft-标识/",
            **(
                {
                    "moc": settings.moc_dir + "/示例主题.md",
                    "original": settings.original_dir + "/示例原件-标识.pdf",
                }
                if settings.policy_version == 2
                else {}
            ),
        }
        if settings.layout_mode == "obsidian_folders"
        else {"note": "article-标识/note.md"},
        "publication_ready": enable_publication,
        "state": "activation_preview" if enable_publication else "draft_preview",
    }


def save(
    config: Config, db: sqlite3.Connection, identity: str, preview_id: str, revision: int
) -> dict[str, Any]:
    value = vault_directories.load(db, preview_id, "save_policy")
    if value["vault_id"] != identity or value["revision"] != revision:
        raise SettingsError("save_policy_preview_changed", 409)
    enable = value.pop("enable_publication", False)
    settings = SaveSettings.model_validate(value["settings"])
    if observe(config, db, identity, settings, revision) != value:
        raise SettingsError("save_policy_preview_changed", 409)
    root, _, _ = vault_directories.vault_root(config, db, identity)
    operation = str(uuid4())
    intent = {"preview": value, "created": {}}
    now = time.time()
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (operation, identity, "save_policy", canonical(intent), now, now),
    )
    try:
        for relative, expected in value["directories"].items():
            if expected is not None:
                continue
            db.execute("UPDATE vault_operations SET state='creating' WHERE id=?", (operation,))
            # Pin the opened parent; reject component replacements and never follow symlinks.
            fd = file_exchange.parent_fd(root, relative)
            try:
                parent = relative.rsplit("/", 1)[0] if "/" in relative else ""
                actual = os.fstat(fd)
                expected_parent = value["directories"][parent] or intent["created"].get(parent)
                if [actual.st_dev, actual.st_ino] != expected_parent or vault_directories.key(
                    root
                ) != value["root_key"]:
                    raise SettingsError("save_policy_preview_changed", 409)
                os.mkdir(relative.rsplit("/", 1)[-1], mode=0o700, dir_fd=fd)
                os.fsync(fd)
                created = vault_directories.key(contained(root, relative))
                intent["created"][relative] = created
                db.execute(
                    "UPDATE vault_operations SET payload=?,updated_at=? WHERE id=?",
                    (canonical(intent), time.time(), operation),
                )
            finally:
                os.close(fd)
        # Recheck current roots, policy and every selected directory after filesystem writes.
        db.execute("UPDATE vault_operations SET state='checking' WHERE id=?", (operation,))
        observed = observe(config, db, identity, settings, revision, own_operation=operation)
        expected_dirs = {
            p: key or intent["created"].get(p) for p, key in value["directories"].items()
        }
        # Newly created parents gain policy fingerprints; preexisting ones must match preflight.
        if any(
            observed["parent_scopes"].get(p) != s for p, s in value["parent_scopes"].items()
        ) or {**observed, "parent_scopes": value["parent_scopes"]} != {
            **value,
            "directories": expected_dirs,
        }:
            raise SettingsError("save_policy_preview_changed", 409)
        sync_directory(root)
        with database.transaction(db):
            db.execute(
                "INSERT INTO vault_save_policies VALUES (?,?,?,?,?) "
                "ON CONFLICT(vault_id) DO UPDATE SET revision=excluded.revision,"
                "value=excluded.value,state=excluded.state,updated_at=excluded.updated_at",
                (
                    identity,
                    revision + 1,
                    canonical(observed),
                    "active" if enable else "draft",
                    time.time(),
                ),
            )
            db.execute("DELETE FROM vault_directory_previews WHERE id=?", (preview_id,))
            db.execute(
                "UPDATE vault_operations SET state='completed',updated_at=? WHERE id=?",
                (time.time(), operation),
            )
    except BaseException:
        db.execute(
            "UPDATE vault_operations SET state='reconcile',updated_at=? WHERE id=?",
            (time.time(), operation),
        )
        raise
    return {**get(config, db, identity), "operation_id": operation}


def abandon(
    config: Config, db: sqlite3.Connection, identity: str, operation: str
) -> dict[str, Any]:
    vault_directories.vault_root(config, db, identity)
    row = db.execute(
        "SELECT state FROM vault_operations WHERE id=? AND vault_id=? AND kind='save_policy'",
        (operation, identity),
    ).fetchone()
    if row is None or row[0] not in {"prepared", "creating", "checking", "reconcile"}:
        raise SettingsError("save_policy_operation_unavailable", 409)
    with database.transaction(db):
        db.execute(
            "UPDATE vault_operations SET state='cancelled',updated_at=? WHERE id=?",
            (time.time(), operation),
        )
        db.execute(
            "DELETE FROM vault_directory_previews WHERE kind='save_policy' "
            "AND json_extract(payload,'$.vault_id')=?",
            (identity,),
        )
    return {"state": "cancelled", "directories_preserved": True}
