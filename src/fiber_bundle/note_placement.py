"""Bounded same-Vault archive preflight and explicit MOC association decisions."""

import base64
import json
import sqlite3
import time
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import (
    auth,
    moc_store,
    note_changes,
    note_identity_store,
    note_inventory,
    publication_layout,
    publication_links,
    publication_locations,
    vault_directories,
    vault_save_policy,
    vault_scope,
)
from .config import Config, IntegrityError, InvalidInput, contained
from .inputs import canonical
from .objects import digest


def guard(db: sqlite3.Connection, vault: str) -> None:
    if db.execute(
        "SELECT 1 FROM vault_operations WHERE vault_id=? AND kind IN "
        "('note_placement','layout_migration','publication_rebind') AND state IN "
        "('applying','reconcile')",
        (vault,),
    ).fetchone():
        raise IntegrityError("placement_reconciliation_required")


def context(
    config: Config, db: sqlite3.Connection, client: str, vault: str
) -> tuple[Path, Any, dict[str, Any]]:
    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    root, scope, _ = vault_directories.vault_root(config, db, vault)
    policy = vault_save_policy.get(config, db, vault)
    if not policy["publication_ready"] or policy["settings"]["layout_mode"] != "obsidian_folders":
        raise InvalidInput("placement_active_policy_required")
    return root, scope, policy


def records(config: Config, vault: str) -> dict[str, Any]:
    with moc_store.connection(config) as side:
        ids = (
            [
                r[0]
                for r in side.execute(
                    "SELECT id FROM mocs WHERE vault_id=? ORDER BY id LIMIT 1001", (vault,)
                )
            ]
            if side
            else []
        )
    if len(ids) > 1000:
        raise IntegrityError("placement_moc_limit")
    return {mid: moc_store.load(config, vault, mid) for mid in ids}


def locations(db: sqlite3.Connection, vault: str) -> list[dict[str, Any]]:
    result = []
    for r in db.execute(
        "SELECT kind,publication_id FROM publication_locations WHERE vault_id=? "
        "ORDER BY kind,publication_id",
        (vault,),
    ):
        row = publication_locations.get(db, r[0], r[1])
        if row is not None:
            result.append(row)
    return result


def listing(config: Config, db: sqlite3.Connection, client: str, vault: str) -> dict[str, Any]:
    from .automatic_placement import listing as automatic_listing
    from .publication_rebind import result as rebind_result
    from .vault_save_contract import archive_mode

    for permission in ("query", "add"):
        auth.grant(db, client, vault, permission)
    root, _, _ = vault_directories.vault_root(config, db, vault)
    policy = vault_save_policy.get(config, db, vault)
    pending = db.execute(
        "SELECT 1 FROM vault_operations WHERE vault_id=? AND kind IN "
        "('note_placement','layout_migration','publication_rebind','moc_registration') "
        "AND state IN "
        "('applying','reconcile')",
        (vault,),
    ).fetchone()
    rebind_required = False
    inventory = None
    if not pending and policy["publication_ready"]:
        try:
            inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
        except IntegrityError as exc:
            if str(exc) not in {"layout_rebind_required", "layout_tracked_note_replaced"}:
                raise
            rebind_required = True
    current_locations = locations(db, vault)
    by_path = {
        p: r
        for r in current_locations
        if r and r["state"] == "published"
        for p in r["plan"]["outputs"]
    }
    eligible = {
        r["plan"]
        .get("identity_overrides", {})
        .get(n, publication_locations.note_id(r["kind"], r["publication_id"], n))
        for r in locations(db, vault)
        if r and r["state"] == "published"
        for n in r["plan"]["mapping"]
        if n.endswith(".md")
    }
    return {
        "vault_id": vault,
        "available": inventory is not None,
        "blocked": bool(pending),
        "rebind_required": rebind_required,
        "locations": [
            {
                k: r[k]
                for k in (
                    "kind",
                    "publication_id",
                    "entry_note_path",
                    "package_root",
                    "revision",
                    "state",
                )
            }
            for r in locations(db, vault)
            if r["state"] == "missing" or publication_locations.committed(db, r)
        ],
        "policy_revision": policy["revision"],
        "archive_on_moc": policy["settings"]["archive_on_moc"],
        "archive_mode": archive_mode(policy["settings"]),
        "automatic_archives": automatic_listing(db, client, vault),
        "notes": [
            {
                "note_identity": e.identity,
                "note_path": e.note_path,
                "sha256": e.sha256,
                "kind": by_path[e.note_path]["kind"],
                "package_root": by_path[e.note_path]["package_root"],
            }
            for e in note_changes.entries(inventory.notes if inventory else [])
            if e.identity in eligible
            and (
                by_path[e.note_path]["kind"] != "expert"
                or e.note_path == by_path[e.note_path]["entry_note_path"]
            )
        ],
        "mocs": [
            {"id": mid, "title": r.title, "revision": r.revision}
            for mid, r in records(config, vault).items()
            if r and (r.vault_dev, r.vault_ino) == tuple(vault_directories.key(root))
        ],
        "operations": [
            {
                "id": r["id"],
                "state": r["state"],
                "kind": r["kind"],
                "preview": (
                    rebind_result(r["id"], r["state"], json.loads(r["payload"]))
                    if r["kind"] == "publication_rebind"
                    else result(r["id"], r["state"], json.loads(r["payload"]))
                ),
            }
            for r in db.execute(
                "SELECT * FROM vault_operations WHERE vault_id=? AND kind IN "
                "('note_placement','layout_migration','publication_rebind') "
                "ORDER BY created_at DESC LIMIT 100",
                (vault,),
            )
            if json.loads(r["payload"]).get("client_id") == client
        ],
    }


def plan_links(
    root: Path,
    scope: Any,
    inventory: Any,
    moves: dict[str, str],
    *,
    affected_only: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    candidates = []
    for path, info in vault_scope.entries(root, scope):
        import stat

        relative = path.relative_to(root).as_posix()
        if stat.S_ISREG(info.st_mode) and scope.allows(relative):
            candidates.append(relative)
    candidates = sorted(set(candidates) | set(moves))
    updates: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    for note in inventory.notes:
        target = moves.get(note.relative_path, note.relative_path)
        if (
            affected_only
            and target == note.relative_path
            and not publication_links.may_reference_moved(note.text, note.relative_path, moves)
        ):
            continue
        try:
            rewritten, missing = publication_links.relocate(
                note.text,
                root,
                note.relative_path,
                target,
                candidates,
                moves,
                affected_only=affected_only,
            )
        except IntegrityError:
            rewritten = note.text
            missing = ["unsupported_link_syntax"]
        for missing_path in missing:
            # A conservative stop: excluded/ambiguous local references are never guessed.
            unresolved.append({"note_path": note.relative_path, "target": missing_path})
        if rewritten != note.text:
            # Complete expert packages keep their internal bytes; an inbound external link
            # needing repair blocks the move instead of mutating a canonical Draft.
            if "/.fiber-expert-canonical/" not in note.relative_path and any(
                (root / parent / ".fiber-expert-canonical").is_dir()
                for parent in [
                    p.as_posix() for p in Path(note.relative_path).parents if p.as_posix() != "."
                ]
            ):
                unresolved.append(
                    {"note_path": note.relative_path, "target": "book_package_rewrite_not_allowed"}
                )
                continue
            # UTF-8 BOM is part of source evidence. Preserve it when present.
            from .publication_file_io import read

            original, key = read(root, note.relative_path)
            if original.startswith(b"\xef\xbb\xbf"):
                rewritten = "\ufeff" + rewritten
            updates.append(
                {
                    "path": note.relative_path,
                    "target": target,
                    "before_sha256": digest(original),
                    "file_key": key,
                    "before": base64.b64encode(original).decode(),
                    "after": base64.b64encode(rewritten.encode()).decode(),
                    "after_sha256": digest(rewritten.encode()),
                }
            )
    return updates, unresolved


def preview(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    identity: str,
    expected: str,
    *,
    action: str = "archive",
    moc_id: str | None = None,
    moc_revision: int | None = None,
    destination: str | None = None,
    inventory: note_inventory.Inventory | None = None,
) -> dict[str, Any]:
    guard(db, vault)
    from .moc_registration import guard as registration_guard

    registration_guard(db, vault)
    root, scope, policy = context(config, db, client, vault)
    if inventory is None:
        inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
    elif inventory.root != root:
        raise IntegrityError("placement_inventory_vault_changed")
    note = next(
        (
            n
            for n in inventory.notes
            if (n.identity or ("publication:" + n.publication_id if n.publication_id else ""))
            == identity
        ),
        None,
    )
    if note is None or note.sha256 != expected:
        raise IntegrityError("placement_note_changed")
    selected = None
    for row in locations(db, vault):
        if row and row["state"] == "published" and note.relative_path in row["plan"]["outputs"]:
            selected = row
            break
    if selected is None:
        raise InvalidInput("placement_managed_note_required")
    if action not in {
        "archive",
        "link_archive",
        "associate_only",
        "expert_archive",
        "expert_link_archive",
    }:
        raise InvalidInput("placement_action_invalid")
    expert_action = action in {"expert_archive", "expert_link_archive"}
    if selected["kind"] == "expert" and action != "associate_only":
        if (
            not expert_action
            or policy["settings"].get("policy_version") != 2
            or not selected["package_root"]
        ):
            raise InvalidInput("placement_expert_explicit_archive_required")
    elif expert_action:
        raise InvalidInput("placement_expert_package_required")
    mocs = records(config, vault)
    association = None
    if action in {"link_archive", "associate_only", "expert_link_archive"}:
        if moc_id is None:
            raise InvalidInput("placement_moc_required")
        current = mocs.get(moc_id)
        if (
            current is None
            or current.revision != moc_revision
            or (current.vault_dev, current.vault_ino) != tuple(vault_directories.key(root))
        ):
            raise IntegrityError("placement_moc_changed")
        members = {key: list(owners) for key, owners in current.members.items()}
        members[identity] = sorted(set(members.get(identity, [])) | {"manual"})
        after = replace(
            current, members=members, revision=current.revision + (members != current.members)
        )
        association = {"moc_id": moc_id, "before": asdict(current), "after": asdict(after)}
    identities = {
        selected["plan"]
        .get("identity_overrides", {})
        .get(
            logical,
            publication_locations.note_id(selected["kind"], selected["publication_id"], logical),
        )
        for logical in selected["plan"]["mapping"]
        if logical.endswith(".md")
    }
    relevant = {mid for mid, r in mocs.items() if r and identities.intersection(r.members)}
    if moc_id:
        relevant.add(moc_id)
    choices = sorted(
        {
            policy["settings"]["moc_destinations"].get(mid, policy["settings"]["archive_dir"])
            for mid in relevant
        }
        or {policy["settings"]["archive_dir"]}
    )
    if expert_action:
        choices = [policy["settings"]["book_dir"]]
    if destination is not None and destination not in choices:
        raise InvalidInput("placement_destination_not_mapped")
    chosen = destination or (choices[0] if len(choices) == 1 else None)
    moves = {}
    updates: list[dict[str, Any]] = []
    unresolved: list[dict[str, str]] = []
    target_root = None
    if action != "associate_only" and chosen:
        source_root = selected["package_root"] or selected["entry_note_path"]
        target_root = chosen + "/" + Path(source_root).name
        if target_root == source_root:
            raise InvalidInput("placement_already_at_destination")
        if not scope.allows(target_root):
            raise InvalidInput("placement_scope_denied")
        publication_layout.available(
            root,
            chosen,
            Path(source_root).stem,
            Path(source_root).suffix,
            selected["publication_id"],
            set(),
        )
        # Archiving preserves the filename: conflicts require a new explicit decision.
        if contained(root, target_root).exists() or contained(root, target_root).is_symlink():
            raise IntegrityError("placement_destination_exists")
        vault_save_policy.alias(root, chosen, Path(source_root).name, scope)
        for path in selected["plan"]["outputs"]:
            if path == source_root or path.startswith(source_root + "/"):
                moves[path] = target_root + path[len(source_root) :]
        updates, unresolved = plan_links(root, scope, inventory, moves, affected_only=True)
        if expert_action and any(u["path"].startswith(source_root + "/") for u in updates):
            raise IntegrityError("placement_expert_internal_link_change")
    from .moc_placement_projection import prepare as prepare_moc_files

    projections = prepare_moc_files(config, vault, inventory, mocs, association, moves, updates)
    source_files = {}
    if selected["package_root"]:
        from .publication_file_io import tree

        prefix = selected["package_root"] + "/"
        if set(tree(root, selected["package_root"], scope)) != {
            p.removeprefix(prefix) for p in selected["plan"]["outputs"] if p.startswith(prefix)
        }:
            raise IntegrityError("placement_package_members_changed")
    from .publication_file_io import read

    for source in moves:
        raw, key = read(root, source)
        source_files[source] = {"sha256": digest(raw), "file_key": key}
    token = str(uuid4())
    now = time.time()
    value = {
        "client_id": client,
        "vault_id": vault,
        "root_key": vault_directories.key(root),
        "scope": scope.wire(),
        "policy_revision": policy["revision"],
        "policy_binding": json.loads(
            db.execute(
                "SELECT value FROM vault_save_policies WHERE vault_id=?", (vault,)
            ).fetchone()[0]
        ),
        "kind": selected["kind"],
        "publication_id": selected["publication_id"],
        "location_revision": selected["revision"],
        "source_root": selected["package_root"] or selected["entry_note_path"],
        "target_root": target_root,
        "moves": moves,
        "updates": updates,
        "moc_file_projections": projections,
        "source_files": source_files,
        "unresolved_links": unresolved,
        "moc_choices": choices,
        "association": association,
        "association_applied": False,
        "exchanges": {},
        "move_applied": False,
        "moc_snapshot": {mid: asdict(r) for mid, r in mocs.items() if r},
        "registrations": [asdict(r) for r in note_identity_store.records(config, vault)],
        "action": action,
        "expires_at": now + 600,
        "note_snapshot": [
            (n.relative_path, n.sha256, n.identity, n.publication_id, list(n.file_key or ()))
            for n in inventory.notes
        ],
    }
    db.execute(
        "INSERT INTO vault_operations VALUES(?,?,?,'prepared',?,?,?)",
        (token, vault, "note_placement", canonical(value), now, now),
    )
    return result(token, "prepared", value)


def result(identity: str, state: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "operation_id": identity,
        "state": state,
        "action": value["action"],
        "source_path": value["source_root"],
        "target_path": value["target_root"],
        "destination_choices": value["moc_choices"],
        "file_mapping": value["moves"],
        "rewritten_notes": [r["path"] for r in value["updates"]],
        "expert_package": value["kind"] == "expert",
        "package_files": len(value["source_files"]),
        "package_bytes_preserved": value["kind"] == "expert",
        "knowledge_verified": False if value["kind"] == "expert" else None,
        "unresolved_links": value["unresolved_links"],
        "association_state": "associated"
        if value["association_applied"]
        else "pending"
        if value["association"]
        else "unchanged",
        "ready": not value["unresolved_links"]
        and (value["action"] == "associate_only" or value["target_root"] is not None),
    }


def prepare_after_moc(
    config: Config, db: sqlite3.Connection, client: str, vault: str, identities: list[str]
) -> list[dict[str, Any]]:
    """Only committed associations prepare previews or enqueue explicitly enabled automation."""
    from .config import StorageError

    policy = vault_save_policy.get(config, db, vault)
    from .vault_save_contract import archive_mode

    mode = archive_mode(policy["settings"])
    if not policy["publication_ready"] or mode == "off":
        return []
    inventory = note_inventory.read(config, db, auth.grant(db, client, vault, "query"))
    notes = {n.identity: n for n in inventory.notes}
    result = []
    for identity in sorted(set(identities))[:100]:
        note = notes.get(identity)
        if note is None:
            continue
        try:
            if mode == "automatic":
                from .automatic_placement import enqueue

                packet = enqueue(config, db, client, vault, identity)
            else:
                packet = preview(config, db, client, vault, identity, note.sha256)
            result.append(packet)
        except (StorageError, OSError, sqlite3.Error):
            result.append(
                {
                    "note_identity": identity,
                    "state": "associated_archive_pending",
                    "code": "placement_preview_requires_review",
                }
            )
    return result
