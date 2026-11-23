"""Operation-bound MOC inputs; unrelated note bodies never enter a file projection.

The metadata catalogue resolves human wikilink ambiguity. Only the target, current
members and matching human-link candidates are read through the normal inventory
identity checks. The intent is frozen in new journals and re-resolved on apply.
"""

import json
import os
import sqlite3
import stat
from dataclasses import replace
from pathlib import Path, PurePosixPath
from typing import Any

from . import (
    cloud_files,
    moc_markdown,
    moc_store,
    note_changes,
    note_identity_store,
    note_inventory,
    publication_locations,
    vault_scope,
    vaults,
)
from .config import Config, IntegrityError
from .vault_sync import store as sync_store
from .vault_sync.scanner import signature

MAX_PATHS = 10000


def validate(value: Any) -> dict[str, Any]:
    fields = {"schema", "target", "members", "manual_targets"}
    if (
        not isinstance(value, dict)
        or set(value) not in (fields, fields | {"retained_members"})
        or value["schema"] != "moc-write-scope/v1"
        or not isinstance(value["target"], str)
        or not value["target"].lower().endswith(".md")
    ):
        raise IntegrityError("moc_write_scope_invalid")
    note_changes.validate(
        [{"identity": "path:" + value["target"], "note_path": value["target"], "sha256": "0" * 64}]
    )
    for key in ("members", "manual_targets"):
        values = value[key]
        if (
            not isinstance(values, list)
            or len(values) > 1000
            or any(not isinstance(v, str) or len(v) > 2048 or "\0" in v for v in values)
            or values != sorted(set(values))
        ):
            raise IntegrityError("moc_write_scope_invalid")
    for identity in value["members"]:
        moc_store.stable_identity(identity)
    if "retained_members" in value:
        from . import moc_retained_members

        retained = moc_retained_members.proofs(value["retained_members"], target=value["target"])
        if not set(retained) <= set(value["members"]):
            raise IntegrityError("moc_retained_member_invalid")
    return value


def intent(relative: str, record: Any, data: bytes, mid: str) -> dict[str, Any]:
    before, _, after = moc_markdown.split(data, mid)
    return validate(
        {
            "schema": "moc-write-scope/v1",
            "target": relative,
            "members": sorted(record.members if record else []),
            "manual_targets": sorted(moc_markdown.manual_targets(before + after)),
        }
    )


def prepare(
    config: Config,
    db: sqlite3.Connection,
    vault: sqlite3.Row,
    relative: str,
    record: Any,
    data: bytes,
    mid: str,
) -> dict[str, Any]:
    from . import moc_retained_members

    value = intent(relative, record, data, mid)
    root = vaults.checked_vault(config, vault)
    retained = moc_retained_members.capture(
        config, vault["id"], root, vault_scope.for_vault(root, vault), relative, record, data, mid
    )
    if retained:
        value["retained_members"] = retained
    return validate(value)


def _readable(scope: vault_scope.Scope, path: str) -> bool:
    return bool(
        path
        and not path.startswith("/")
        and "\\" not in path
        and all(p not in {"", ".", ".."} and not p.startswith(".") for p in path.split("/"))
        and scope.allows(path)
    )


def _catalogue(
    config: Config, root: Path, vault: str, scope: vault_scope.Scope, hidden_roots: set[str]
) -> set[str]:
    with sync_store.connection(config) as side:
        library = (
            side.execute("SELECT * FROM libraries WHERE id=?", (vault,)).fetchone()
            if side is not None
            else None
        )
        if library is not None:
            assert side is not None
            if (
                not library["baseline"]
                or library["root"] != str(root)
                or sync_store.scope_of(dict(library)) != scope
            ):
                raise IntegrityError("moc_write_scope_catalogue_pending")
            rows = side.execute(
                "SELECT f.path,f.directory,f.signature FROM files f WHERE f.vault=? "
                "AND (f.directory=1 OR lower(f.path) LIKE '%.md') "
                "AND NOT EXISTS (SELECT 1 FROM json_each(?) h WHERE f.path=h.value "
                "OR substr(f.path,1,length(h.value)+1)=h.value || '/') ORDER BY f.path LIMIT ?",
                (vault, json.dumps(sorted(hidden_roots)), MAX_PATHS + 1),
            ).fetchall()
            if len(rows) > MAX_PATHS:
                raise IntegrityError("moc_write_scope_metadata_limit")
            files = {r["path"]: r for r in rows if sync_store.accepted(scope, r["path"])}
            _check_names(root, files, scope, hidden_roots)
            current = sync_store.library(config, vault)
            if current is None or any(
                current[key] != library[key]
                for key in ("root", "binding", "scope", "generation", "baseline")
            ):
                raise IntegrityError("moc_write_scope_catalogue_changed")
            return {
                path
                for path, item in files.items()
                if not item["directory"] and path.lower().endswith(".md") and _readable(scope, path)
            }
    # Legacy CLI Vaults without an observer retain bounded name discovery. No
    # source body is opened here, and denials are applied before child metadata.
    result: set[str] = set()
    count = 0

    def walk(parent: str, depth: int) -> None:
        nonlocal count
        if depth > 32:
            raise IntegrityError("moc_write_scope_metadata_limit")
        with os.scandir(root / parent) as entries:
            for entry in entries:
                path = parent + "/" + entry.name if parent else entry.name
                if (
                    entry.name.startswith(".")
                    or any(vault_scope.inside(path, parent) for parent in hidden_roots)
                    or not scope.reachable(path)
                ):
                    continue
                count += 1
                if count > MAX_PATHS:
                    raise IntegrityError("moc_write_scope_metadata_limit")
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    from .expert_package_boundary import detected

                    if not detected(root, path, scope):
                        walk(path, depth + 1)
                elif entry.name.lower().endswith(".md") and scope.allows(path):
                    result.add(path)

    walk("", 0)
    return result


def _check_names(
    root: Path, files: dict[str, Any], scope: vault_scope.Scope, hidden_roots: set[str]
) -> None:
    """Prove current name ambiguity without reading unrelated note bodies.

    A MOC's own atomic exchange changes its parent timestamps. Compare the
    physical directory and its exact allowed names instead of accepting an old
    observer name set or rewriting that observer's evidence.
    """
    if "" not in files or not files[""]["directory"]:
        raise IntegrityError("moc_write_scope_catalogue_pending")
    children: dict[str, set[str]] = {}
    for path in files:
        if path:
            children.setdefault(path.rpartition("/")[0], set()).add(path)
    count = 0
    for path, item in files.items():
        if not item["directory"]:
            continue
        directory = root / path
        cloud_files.require_local(directory)
        before = signature(directory.lstat())
        if before == item["signature"]:
            continue
        expected_identity = json.loads(item["signature"])
        current_identity = json.loads(before)
        if any(expected_identity[i] != current_identity[i] for i in (0, 1, 2, 7, 8)):
            raise IntegrityError("moc_write_scope_catalogue_changed")
        names = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                child = (path + "/" if path else "") + entry.name
                # Names are filtered before any child metadata is touched.
                if any(vault_scope.inside(child, p) for p in hidden_roots):
                    continue
                if not sync_store.accepted(scope, child):
                    continue
                count += 1
                if count > MAX_PATHS:
                    raise IntegrityError("moc_write_scope_metadata_limit")
                if child.lower().endswith(".md") or entry.is_dir(follow_symlinks=False):
                    names.add(child)
        if names != children.get(path, set()) or signature(directory.lstat()) != before:
            raise IntegrityError("moc_write_scope_catalogue_changed")


def read(
    config: Config,
    db: sqlite3.Connection,
    vault: sqlite3.Row,
    value: dict[str, Any],
    *,
    client: str,
    omissions: dict[str, str] | None = None,
    **kwargs: Any,
) -> note_inventory.Inventory:
    from . import partial_notes

    value = validate(value)
    root = vaults.checked_vault(config, vault)
    scope = vault_scope.for_vault(root, vault)
    retained_paths: set[str] = set()
    if "retained_members" in value:
        from . import moc_retained_members

        retained_paths = moc_retained_members.check(
            config, vault["id"], root, scope, value["target"], value["retained_members"]
        )
    records = note_identity_store.records(config, vault["id"])
    locations = publication_locations.project_inventory(db, vault["id"])
    paths = {r.identity: r.relative_path for r in records}
    paths.update({m["identity"]: p for p, m in locations.managed.items()})
    # Expert members are interface references rather than Markdown bodies. Still
    # recheck their current bounded text protocol and bind its version to the
    # journal; merely finding an existing membership is never fresh evidence.
    from . import published_expert
    from .organization import store

    expert_versions = {}
    for identity in value["members"]:
        if not identity.startswith("expert:"):
            continue
        identifier = identity.removeprefix("expert:")
        package = store.get(config, "packages", identifier)
        if package:
            if package.get("vault_id") != vault["id"]:
                raise IntegrityError("moc_write_scope_member_unavailable")
            path = package["path"] + "/manifest.json"
        else:
            row = db.execute(
                "SELECT COALESCE(l.vault_id,i.vault_id),"
                "COALESCE(l.entry_note_path,p.relative_path || '/note.md') "
                "FROM source_expert_publications p JOIN source_imports i ON i.id=p.task_id "
                "LEFT JOIN publication_locations l ON l.kind='expert' "
                "AND l.publication_id=p.task_id WHERE p.task_id=? AND p.state='published'",
                (identifier,),
            ).fetchone()
            if row is None or row[0] != vault["id"]:
                raise IntegrityError("moc_write_scope_member_unavailable")
            path = row[1]
        if not _readable(scope, path):
            raise IntegrityError("moc_write_scope_source_denied")
        module = published_expert.text(config, db, client, identifier)
        expert_versions[identity] = module["version"]
    required = {paths[i] for i in value["members"] if i in paths and not i.startswith("expert:")}
    unresolved = {i for i in value["members"] if not i.startswith("expert:") and i not in paths}
    if unresolved:
        raise IntegrityError("moc_write_scope_member_unavailable")
    needed = {value["target"], *required}
    catalogue = None
    hidden_roots = set()
    if value["manual_targets"]:
        from .attachment_projection import hidden_roots as attachment_roots
        from .organization.packages import roots as package_roots

        hidden_roots = (
            package_roots(config, vault["id"])
            | locations.hidden_roots
            | attachment_roots(db, vault["id"], location_roots=locations.attachment_roots)
        )
        catalogue = _catalogue(config, root, vault["id"], scope, hidden_roots)
        candidates = catalogue | set(paths.values())
        for path in candidates:
            if _readable(scope, path) and any(
                path.removesuffix(".md") == target
                or ("/" not in target and PurePosixPath(path).stem == target)
                for target in value["manual_targets"]
            ):
                needed.add(path)
    if any(not _readable(scope, path) for path in needed):
        raise IntegrityError("moc_write_scope_source_denied")
    ancestors = {""}
    for path in needed:
        ancestors.update(str(p) for p in PurePosixPath(path).parents if str(p) != ".")
    known: dict[str, tuple[int, int]] = {}
    for path in sorted(needed | ancestors):
        if path and not scope.reachable(path):
            raise IntegrityError("moc_write_scope_source_denied")
        try:
            info = (root / path).lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode):
            raise IntegrityError("moc_write_scope_source_denied")
        known[path] = info.st_mode, info.st_size
    inventory = partial_notes.read(
        config,
        db,
        vault,
        omissions,
        known_entries=known,
        exclude_path=lambda path: path not in needed and path not in ancestors,
        selected_paths=needed,
        **kwargs,
    )
    if catalogue is not None and (
        _catalogue(config, root, vault["id"], scope, hidden_roots) != catalogue
        or vault_scope.for_vault(root, vault) != scope
    ):
        raise IntegrityError("moc_write_scope_catalogue_changed")
    visible = {n.relative_path for n in inventory.notes}
    if required - visible - set(omissions or {}) - retained_paths:
        raise IntegrityError("moc_write_scope_member_unavailable")
    return replace(
        inventory,
        coverage={
            **(inventory.coverage or {}),
            "read_scope": value,
            "expert_versions": expert_versions,
            **({"retained_members": value["retained_members"]} if retained_paths else {}),
        },
    )
