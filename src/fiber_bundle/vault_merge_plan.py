"""Explicit whole-Vault merge preflight and immutable freeze; no source/target file writes."""

import shutil
import time
import unicodedata
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import Field

from . import (
    database,
    publication_file_io,
    publication_layout,
    publication_links,
    vault_merge_inventory,
    vault_save_policy,
    vaults,
)
from .config import Config, InvalidInput
from .inputs import canonical
from .objects import digest
from .payloads import Identifier, Payload


class Grant(Payload):
    client_id: Identifier
    query: bool = True
    original: bool = False
    add: bool = False


class Selection(Payload):
    client_id: Identifier
    source_vault_ids: list[Identifier] = Field(min_length=1, max_length=8)
    target_vault_id: Identifier
    historical_files: dict[str, list[str]] = Field(default_factory=dict)
    target_grants: list[Grant] = Field(min_length=1, max_length=100)
    switch_channel_ids: list[Identifier] = Field(default_factory=list, max_length=50)
    switch_default: bool = True


def fold(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def preflight(config: Config, db: Any, selection: Selection) -> dict[str, Any]:
    sources = selection.source_vault_ids
    target = selection.target_vault_id
    if len(sources) != len(set(sources)) or target in sources:
        raise InvalidInput("vault_merge_distinct_vaults_required")
    owner = next((g for g in selection.target_grants if g.client_id == selection.client_id), None)
    if not owner or not (owner.query and owner.original and owner.add):
        raise InvalidInput("vault_merge_owner_target_permissions_required")
    if len({g.client_id for g in selection.target_grants}) != len(selection.target_grants):
        raise InvalidInput("vault_merge_duplicate_grant")
    for grant in selection.target_grants:
        if not db.execute(
            "SELECT 1 FROM clients WHERE id=? AND active=1", (grant.client_id,)
        ).fetchone():
            raise InvalidInput("vault_merge_client_unavailable")
    policy = vault_save_policy.get(config, db, target)
    if not policy["publication_ready"] or policy["settings"].get("policy_version") != 2:
        raise InvalidInput("vault_merge_target_unified_layout_required")
    contexts = [
        vault_merge_inventory.describe(config, db, selection.client_id, v)
        for v in [*sources, target]
    ]
    target_info = contexts[-1]
    root = Path(target_info["context"]["root"])
    all_roots = [Path(c["context"]["root"]) for c in contexts]
    if any(
        a == b or a in b.parents or b in a.parents
        for i, a in enumerate(all_roots)
        for b in all_roots[i + 1 :]
    ):
        raise InvalidInput("vault_merge_roots_overlap")
    settings = policy["settings"]
    occupied = {fold(f["path"]) for f in target_info["files"]}
    target_ids = {n["identity"] for n in target_info["notes"]}
    target_mocs = {m["id"] for m in target_info["mocs"]}
    mappings = []
    conflicts = []
    exclusions: list[dict[str, Any]] = []
    identities = set(target_ids)
    for source in contexts[:-1]:
        vid = source["context"]["vault_id"]
        source_root = Path(source["context"]["root"])
        by_path = {f["path"]: f for f in source["files"]}
        notes = {n["path"]: n for n in source["notes"]}
        moc_paths = {m["file_path"] for m in source["mocs"] if m["file_path"]}
        members = {identity for m in source["mocs"] for identity in m["record"]["members"]}
        chosen = set(selection.historical_files.get(vid, []))
        if chosen - set(by_path):
            raise InvalidInput("vault_merge_historical_selection_unavailable")
        managed = {path: pub for pub in source["publications"] for path in pub["mapping"].values()}
        chosen.update(managed)
        for analysis in source["analyses"]:
            chosen.update(analysis["paths"])
        chosen.update(moc_paths)
        for m in source["mocs"]:
            if m["id"] in target_mocs:
                conflicts.append({"vault_id": vid, "reason": "duplicate_moc_id", "id": m["id"]})
            target_mocs.add(m["id"])
        packages: dict[str, str] = {}
        for path in sorted(chosen):
            entry = by_path[path]
            pub = managed.get(path)
            note = notes.get(path)
            if note and note["identity"] in identities:
                conflicts.append({"vault_id": vid, "path": path, "reason": "duplicate_note_id"})
            if note:
                identities.add(note["identity"])
            suffix = Path(path).suffix.lower()
            if pub and pub["kind"] == "expert":
                key = pub["id"]
                heading = publication_layout.name(Path(pub["package"]).name, key)
                package = packages.setdefault(
                    key, settings["book_dir"] + "/" + heading.removesuffix("-Draft") + "-Draft"
                )
                destination = package + "/" + path.removeprefix(pub["package"] + "/")
                role = "book"
            elif (
                pub
                and pub["package"]
                and len(pub["body_files"]) > 1
                and path == pub["mapping"].get("publication-notes.json")
            ):
                entry_note = notes.get(pub["entry"])
                if entry_note is None:
                    raise InvalidInput("vault_merge_multipart_entry_missing")
                parent = (
                    settings["archive_dir"]
                    if any(
                        n["identity"] in members
                        for n in source["notes"]
                        if n["path"] in pub["mapping"].values()
                    )
                    else settings["inbox_dir"]
                )
                package = packages.setdefault(
                    pub["id"],
                    parent
                    + "/"
                    + publication_layout.name(entry_note["title"], pub["id"])
                    + "-资料包",
                )
                destination = package + "/publication-notes.json"
                role = "manifest"
            elif path in moc_paths:
                destination = settings["moc_dir"] + "/" + Path(path).name
                role = "moc"
            elif note:
                role = (
                    "classified"
                    if note["identity"] in members
                    or (
                        pub
                        and any(
                            n["identity"] in members
                            for n in source["notes"]
                            if n["path"] in pub["mapping"].values()
                        )
                    )
                    else "inbox"
                )
                parent = settings["archive_dir"] if role == "classified" else settings["inbox_dir"]
                if pub and pub["package"] and len(pub["body_files"]) > 1:
                    package = packages.setdefault(
                        pub["id"],
                        parent
                        + "/"
                        + publication_layout.name(notes.get(pub["entry"], note)["title"], pub["id"])
                        + "-资料包",
                    )
                    logical = next(k for k, v in pub["mapping"].items() if v == path)
                    destination = package + "/" + logical
                else:
                    logical = (
                        next(k for k, v in pub["mapping"].items() if v == path) if pub else None
                    )
                    heading = publication_layout.name(note["title"], Path(path).stem)
                    if pub and pub.get("supplement_roles", {}).get(logical) == "analysis":
                        heading = (
                            publication_layout.name(
                                notes[pub["entry"]]["title"], Path(pub["entry"]).stem
                            )
                            + "-分析"
                        )
                    destination = parent + "/" + heading + ".md"
            else:
                role = "image" if suffix in publication_layout.IMAGES else "original"
                parent = settings["image_dir"] if role == "image" else settings["original_dir"]
                destination = (
                    parent
                    + "/"
                    + publication_layout.name(Path(path).stem, "asset")
                    + "-"
                    + digest((vid + ":" + path).encode())[:10]
                    + suffix
                )
            vaults.require_scope(
                config,
                db.execute("SELECT * FROM vaults WHERE id=?", (target,)).fetchone(),
                destination,
            )
            if fold(destination) in occupied:
                conflicts.append(
                    {
                        "vault_id": vid,
                        "path": path,
                        "target": destination,
                        "reason": "target_name_conflict",
                    }
                )
            occupied.add(fold(destination))
            mappings.append(
                {
                    "source_vault_id": vid,
                    "source_path": path,
                    "target_path": destination,
                    "role": role,
                    "source": entry,
                    "note": note,
                    "analysis": note.get("analysis") if note else None,
                    "publication": {"kind": pub["kind"], "id": pub["id"]} if pub else None,
                }
            )
        moves = {
            f["source_path"]: f["target_path"] for f in mappings if f["source_vault_id"] == vid
        }
        for item in [f for f in mappings if f["source_vault_id"] == vid]:
            path = item["source_path"]
            entry = item["source"]
            item["projected_sha256"] = entry["sha256"]
            item["projected_bytes"] = entry["bytes"]
            if path.endswith(".md") and item["role"] != "book":
                data, _ = publication_file_io.read(source_root, path)
                rewritten, unresolved = publication_links.relocate(
                    data.decode("utf-8-sig"),
                    source_root,
                    path,
                    item["target_path"],
                    list(by_path),
                    moves,
                )
                for reference in unresolved:
                    conflicts.append(
                        {
                            "vault_id": vid,
                            "path": path,
                            "reference": reference,
                            "reason": "unresolved_local_link",
                        }
                    )
                # A resolved link to an unselected source would silently target the wrong Vault.
                _, not_selected = publication_links.relocate(
                    data.decode("utf-8-sig"),
                    source_root,
                    path,
                    item["target_path"],
                    list(moves),
                    moves,
                )
                for reference in not_selected:
                    if reference not in unresolved:
                        conflicts.append(
                            {
                                "vault_id": vid,
                                "path": path,
                                "reference": reference,
                                "reason": "linked_file_not_selected",
                            }
                        )
                item["projected_sha256"] = digest(rewritten.encode())
                item["projected_bytes"] = len(rewritten.encode())
        exclusions.extend(
            {"vault_id": vid, "path": p, "reason": "historical_not_selected"}
            for p in sorted(set(by_path) - chosen)
        )
    # Visible original copies are frozen too, not added ad hoc after the backup/confirmation.
    for source in contexts[:-1]:
        vid = source["context"]["vault_id"]
        for version in source["sources"]:
            for asset in version["assets"]:
                suffix = Path(asset["original_name"]).suffix.lower()
                role = (
                    "image"
                    if suffix in publication_layout.IMAGES
                    and asset["media_type"].startswith("image/")
                    else "original"
                )
                parent = settings["image_dir"] if role == "image" else settings["original_dir"]
                destination = (
                    parent
                    + "/"
                    + publication_layout.name(Path(asset["original_name"]).stem, "original")
                    + "-"
                    + digest((vid + version["version_id"] + asset["asset_id"]).encode())[:12]
                    + suffix
                )
                if fold(destination) in occupied:
                    conflicts.append(
                        {
                            "vault_id": vid,
                            "path": asset["original_name"],
                            "target": destination,
                            "reason": "target_original_name_conflict",
                        }
                    )
                occupied.add(fold(destination))
                vaults.require_scope(
                    config,
                    db.execute("SELECT * FROM vaults WHERE id=?", (target,)).fetchone(),
                    destination,
                )
                file = (config.state_root / asset["relative_path"]).stat()
                mappings.append(
                    {
                        "source_vault_id": vid,
                        "source_path": "@original/"
                        + version["version_id"]
                        + "/"
                        + asset["asset_id"],
                        "target_path": destination,
                        "role": role,
                        "storage": "object",
                        "source": {
                            "path": asset["relative_path"],
                            "bytes": asset["byte_count"],
                            "sha256": asset["sha256"],
                            "dev": file.st_dev,
                            "ino": file.st_ino,
                            "mtime_ns": file.st_mtime_ns,
                        },
                        "note": None,
                        "publication": None,
                        "projected_sha256": asset["sha256"],
                        "projected_bytes": asset["byte_count"],
                        "source_version_id": version["version_id"],
                        "source_asset_id": asset["asset_id"],
                    }
                )
    for source in contexts[:-1]:
        selected_ids = {
            f["note"]["identity"]
            for f in mappings
            if f["source_vault_id"] == source["context"]["vault_id"] and f["note"]
        }
        for moc in source["mocs"]:
            for member in set(moc["record"]["members"]) - selected_ids:
                conflicts.append(
                    {
                        "vault_id": source["context"]["vault_id"],
                        "reason": "moc_member_not_selected",
                        "moc_id": moc["id"],
                        "note_id": member,
                    }
                )
    total = sum(m["projected_bytes"] for m in mappings)
    original_bytes = sum(
        a["byte_count"] for c in contexts[:-1] for v in c["sources"] for a in v["assets"]
    )
    if total + original_bytes > vault_merge_inventory.MAX_BYTES:
        raise InvalidInput("vault_merge_total_copy_limit")
    if shutil.disk_usage(root).free < 2 * (total + original_bytes) + 16 * 1024 * 1024:
        conflicts.append({"reason": "target_disk_space_insufficient"})
    from .vault_merge_switch import entrances

    entry_snapshot = entrances(config, db, sources, target, selection.switch_channel_ids)
    value = {
        "schema": "fiber-vault-merge-plan/v1",
        "selection": selection.model_dump(),
        "inventories": contexts,
        "target_policy": policy,
        "entrances": entry_snapshot,
        "files": mappings,
        "excluded": exclusions,
        "conflicts": conflicts,
        "copy_bytes": total + original_bytes,
        "cross_filesystem": any(
            c["context"]["root_key"][0] != target_info["context"]["root_key"][0]
            for c in contexts[:-1]
        ),
        "source_files_retained": True,
        "ready": not conflicts,
    }
    return {**value, "plan_sha256": digest(canonical(value).encode())}


def freeze(config: Config, db: Any, selection: Selection, expected: str) -> dict[str, Any]:
    value = preflight(config, db, selection)
    if value["plan_sha256"] != expected:
        raise InvalidInput("vault_merge_preview_changed")
    if not value["ready"]:
        raise InvalidInput("vault_merge_conflicts_require_resolution")
    with database.locked(config):
        for inventory in value["inventories"]:
            frozen = inventory["context"]
            if (
                vault_merge_inventory.context(config, db, selection.client_id, frozen["vault_id"])
                != frozen
            ):
                raise InvalidInput("vault_merge_preview_scope_changed")
            root = Path(frozen["root"])
            for item in inventory["files"]:
                observed = (root / item["path"]).stat(follow_symlinks=False)
                if (observed.st_dev, observed.st_ino, observed.st_size, observed.st_mtime_ns) != (
                    item["dev"],
                    item["ino"],
                    item["bytes"],
                    item["mtime_ns"],
                ):
                    raise InvalidInput("vault_merge_preview_file_changed")
        if vault_save_policy.get(config, db, selection.target_vault_id) != value["target_policy"]:
            raise InvalidInput("vault_merge_preview_policy_changed")
        return _record(db, selection, value, expected)


def _record(db: Any, selection: Selection, value: dict[str, Any], expected: str) -> dict[str, Any]:
    identity = str(uuid4())
    now = time.time()
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,'vault_merge','prepared',?,?,?)",
        (
            identity,
            selection.target_vault_id,
            canonical({"plan": value, "progress": {}, "backup": None}),
            now,
            now,
        ),
    )
    return {"id": identity, "state": "prepared", "plan_sha256": expected}
