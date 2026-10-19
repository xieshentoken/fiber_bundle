"""Bounded directory metadata discovery. No note reads, recursive index or writes."""

import hashlib
import json
import os
import sqlite3
import stat
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from . import auth, cloud_files, file_exchange, vault_catalog, vault_scope
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical
from .vault_save_contract import SettingsError

PAGE_SIZE = 100
SCAN_LIMIT = 5000
SCAN_SECONDS = 0.5
PREVIEW_SECONDS = 300


def key(path: Path) -> list[int]:
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise SettingsError("vault_directories_directory_changed", 409)
    return [info.st_dev, info.st_ino]


def vault_root(
    config: Config, db: sqlite3.Connection, identity: str
) -> tuple[Path, vault_scope.Scope, sqlite3.Row]:
    from .vault_merge_guard import check as merge_check

    merge_check(db, identity)
    row = db.execute("SELECT * FROM vaults WHERE id=?", (identity,)).fetchone()
    if row is None or row["status"] != "active" or row["attachment_state"] != "attached":
        raise auth.Denied(404)
    if row["root_kind"] == "legacy":
        # Preserve the existing bootstrap/generated-root identity contract without a tree scan.
        configured = dict(config.vaults)
        if row["name"] in configured:
            if row["relative_path"] != configured[row["name"]] or row["requested_by_client_id"]:
                raise IntegrityError("configured vault identity changed")
        else:
            from .vaults import CREATE_ROOT

            try:
                generated = str(UUID(row["id"]))
            except (ValueError, TypeError, AttributeError) as exc:
                raise IntegrityError("invalid vault identity") from exc
            if (
                row["relative_path"] != f"{CREATE_ROOT}/created-{generated}"
                or not row["requested_by_client_id"]
            ):
                raise IntegrityError("invalid generated vault path")
    path = vault_catalog.physical_path(config, row)
    # canonical_root checks root only, unlike the legacy whole-tree availability check.
    root, scope = vault_catalog.canonical_root(config, str(path), json.loads(row["scope_json"]))
    actual = key(root)
    if row["directory_ino"] is not None and actual != [row["directory_dev"], row["directory_ino"]]:
        raise SettingsError("vault_directories_directory_changed", 409)
    return root, scope, row


def persist(db: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> str:
    db.execute("DELETE FROM vault_directory_previews WHERE expires_at<?", (time.time(),))
    if db.execute("SELECT count(*) FROM vault_directory_previews").fetchone()[0] >= 200:
        raise SettingsError("vault_directories_preview_limit")
    identity = str(uuid4())
    db.execute(
        "INSERT INTO vault_directory_previews VALUES (?,?,?,?)",
        (identity, kind, canonical(payload), time.time() + PREVIEW_SECONDS),
    )
    return identity


def load(db: sqlite3.Connection, identity: str, kind: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT payload FROM vault_directory_previews WHERE id=? AND kind=? AND expires_at>?",
        (identity, kind, time.time()),
    ).fetchone()
    if row is None:
        raise SettingsError("vault_directories_preview_expired", 409)
    value: dict[str, Any] = json.loads(row[0])
    return value


def parent_path(
    root: Path, scope: vault_scope.Scope, relative: str, *, internal_root: str | None = None
) -> Path:
    try:
        normalized = vault_scope.prefix(relative)
    except InvalidInput as exc:
        raise SettingsError("vault_directories_invalid_parent") from exc
    if normalized != relative or len(relative.encode()) > 1024:
        raise SettingsError("vault_directories_invalid_parent")
    parts = relative.split("/") if relative else []
    if internal_root is not None and (
        vault_scope.prefix(internal_root) != internal_root
        or not internal_root.endswith("/.fiber-expert-canonical")
    ):
        raise SettingsError("vault_directories_invalid_parent")
    if not scope.reachable(relative) or any(
        (
            p.startswith(".")
            and not (internal_root and vault_scope.inside("/".join(parts[: i + 1]), internal_root))
        )
        or p.casefold() in {"trash", "$recycle.bin"}
        or p.endswith(".icloud")
        for i, p in enumerate(parts)
    ):
        raise SettingsError("vault_directories_scope_denied")
    # Check each prefix before filesystem access, including an explicitly submitted parent.
    current = root
    for part in relative.split("/") if relative else []:
        inherited = vault_scope.effective(current, {"include": [""], "exclude": []})
        if not inherited.reachable(part):
            raise SettingsError("vault_directories_scope_denied")
        current = current / part
        if not scope.reachable(current.relative_to(root).as_posix()):
            raise SettingsError("vault_directories_scope_denied")
        cloud_files.require_available(current)
        key(current)
    return current


@contextmanager
def opened_parent(
    root: Path, scope: vault_scope.Scope, relative: str, *, internal_root: str | None = None
) -> Iterator[tuple[Path, int]]:
    """Pin every traversed component; a replacement cannot redirect a directory listing."""
    parent = parent_path(root, scope, relative, internal_root=internal_root)
    root_key, parent_key = key(root), key(parent)
    fd = file_exchange.parent_fd(root, (relative + "/" if relative else "") + "child")
    try:
        info = os.fstat(fd)
        if [info.st_dev, info.st_ino] != parent_key:
            raise SettingsError("vault_directories_directory_changed", 409)
        yield parent, fd
        if (
            key(root) != root_key
            or key(parent_path(root, scope, relative, internal_root=internal_root)) != parent_key
        ):
            raise SettingsError("vault_directories_directory_changed", 409)
    finally:
        os.close(fd)


def scan(
    root: Path,
    scope: vault_scope.Scope,
    *,
    relative_parent: str = "",
    cursor: str | None = None,
    read_only: bool = False,
) -> dict[str, Any]:
    nodes: list[dict[str, Any]] = []
    truncated = False
    continuation = None
    with opened_parent(root, scope, relative_parent) as (parent, fd), os.scandir(fd) as entries:
        before = os.fstat(fd)
        inherited = vault_scope.effective(parent, {"include": [""], "exclude": []})
        snapshot = hashlib.sha256(
            canonical(
                [
                    key(root),
                    key(parent),
                    before.st_mtime_ns,
                    before.st_ctime_ns,
                    scope.wire(),
                    inherited.wire(),
                    read_only,
                ]
            ).encode()
        ).hexdigest()
        offset = 0
        if cursor:
            try:
                saved, position = cursor.split(":")
                offset = int(position)
            except (ValueError, TypeError) as exc:
                raise SettingsError("vault_directories_invalid_cursor") from exc
            if saved != snapshot or not 0 <= offset <= 2**31 - 1:
                raise SettingsError("vault_directories_stale_cursor", 409)
        started = None
        for number, entry in enumerate(entries):
            if number < offset:
                continue
            if started is None:
                started = time.monotonic()
            limited = number - offset >= SCAN_LIMIT or (
                number > offset and time.monotonic() - started > SCAN_SECONDS
            )
            if len(nodes) >= PAGE_SIZE or limited:
                continuation = f"{snapshot}:{number}"
                truncated = limited
                break
            relative = (relative_parent + "/" if relative_parent else "") + entry.name
            if (
                not scope.reachable(relative)
                or not inherited.reachable(entry.name)
                or entry.name.startswith(".")
                or entry.name.casefold() in {"trash", "$recycle.bin"}
                or entry.name.endswith(".icloud")
            ):
                continue
            if not entry.is_dir(follow_symlinks=False):
                continue
            path = root / relative
            try:
                cloud_files.require_available(path)
                identity = key(path)
            except (FileNotFoundError, PermissionError):
                continue
            except IntegrityError as exc:
                if str(exc) != "cloud_item_not_downloaded":
                    raise
                continue
            allowed = scope.allows(relative) and inherited.allows(entry.name)
            writable = not read_only and allowed and os.access(path, os.W_OK | os.X_OK)
            nodes.append(
                {
                    "relative_path": relative,
                    "name": entry.name,
                    "directory_key": identity,
                    "children": "unexpanded",
                    "selectable": writable,
                    "reason": None if writable else "read_only" if read_only else "scope_or_access",
                }
            )
        after = os.fstat(fd)
    if (before.st_dev, before.st_ino, before.st_mtime_ns, before.st_ctime_ns) != (
        after.st_dev,
        after.st_ino,
        after.st_mtime_ns,
        after.st_ctime_ns,
    ):
        raise SettingsError("vault_directories_directory_changed", 409)
    nodes.sort(key=lambda n: n["relative_path"])
    return {
        "relative_parent": relative_parent,
        "snapshot": snapshot,
        "directories": nodes,
        "next_cursor": continuation,
        "truncated": truncated,
    }


def preview(
    config: Config,
    db: sqlite3.Connection,
    *,
    root: str | None,
    scope: dict[str, Any],
    preview_id: str | None,
    relative_parent: str,
    cursor: str | None,
) -> dict[str, Any]:
    if preview_id:
        if root is not None:
            raise SettingsError("vault_directories_invalid_parent")
        saved = load(db, preview_id, "discovery")
        path, effective = vault_catalog.canonical_root(config, saved["root"], saved["scope"])
        if key(path) != saved["root_key"] or effective.wire() != saved["effective_scope"]:
            raise SettingsError("vault_directories_directory_changed", 409)
    else:
        if root is None or relative_parent or cursor:
            raise SettingsError("vault_directories_invalid_parent")
        path, effective = vault_catalog.canonical_root(config, root, scope)
    result = scan(path, effective, relative_parent=relative_parent, cursor=cursor)
    if not preview_id:
        preview_id = persist(
            db,
            "discovery",
            {
                "root": str(path),
                "scope": scope,
                "root_key": key(path),
                "effective_scope": effective.wire(),
            },
        )
    return {**result, "directory_preview_id": preview_id}


def listing(
    config: Config, db: sqlite3.Connection, identity: str, parent: str, cursor: str | None
) -> dict[str, Any]:
    root, scope, row = vault_root(config, db, identity)
    return {
        "vault_id": identity,
        **scan(
            root, scope, relative_parent=parent, cursor=cursor, read_only=bool(row["read_only"])
        ),
    }
