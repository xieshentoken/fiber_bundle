"""Explicit MOC relations; bound physical Maps also project ordinary note properties."""

import html
import sqlite3
from dataclasses import replace
from typing import Any
from urllib.parse import quote

from . import auth, database, moc_store, note_changes, note_inventory, vaults
from .config import Config, IntegrityError, InvalidInput
from .moc_store import Record


def escape(value: str) -> str:
    value = html.escape(value, quote=False)
    for character in "\\`*_{}[]()#!|":
        value = value.replace(character, "\\" + character)
    return value


def preview(record: Record, inventory: note_inventory.Inventory) -> dict[str, Any]:
    notes = {
        n.identity or ("publication:" + n.publication_id if n.publication_id else ""): n
        for n in inventory.notes
        if n.identity is not None or n.publication_id is not None
    }
    members = []
    lines = ["# " + escape(record.title), "", "> MOC 预览；链接相对于 Vault 根目录。", ""]
    for identity, owners in sorted(record.members.items()):
        note = notes.get(identity)
        members.append(
            {
                "note_identity": identity,
                "owners": owners,
                "status": "visible" if note else "missing",
                "note_path": note.relative_path if note else None,
                "sha256": note.sha256 if note else None,
            }
        )
        owner_label = "、".join("人工" if owner == "manual" else "系统" for owner in owners)
        if note is None:
            lines.append(f"- 当前不可见：{identity}（{owner_label}；归属保留）")
        else:
            label = escape(note.relative_path.rsplit("/", 1)[-1])
            lines.append(f"- [{label}](<{quote(note.relative_path, safe='/')}>)（{owner_label}）")
    return {"members": members, "markdown": "\n".join(lines) + "\n"}


def checked_inventory(
    config: Config,
    db: sqlite3.Connection,
    client_id: str,
    vault_id: str,
    key: tuple[int, int],
) -> note_inventory.Inventory:
    vault = auth.grant(db, client_id, vault_id, "query")
    info = vaults.checked_vault(config, vault).stat()
    if (info.st_dev, info.st_ino) != key:
        raise IntegrityError("MOC vault identity changed")
    return note_inventory.read(config, db, vault)


def observation(inventory: note_inventory.Inventory) -> list[tuple[Any, ...]]:
    return sorted(
        (n.relative_path, n.sha256, n.identity, n.publication_id, n.file_key)
        for n in inventory.notes
    )


def run(
    config: Config,
    authorization: str,
    vault_id: str,
    action: str,
    moc_id: str,
    *,
    title: str | None = None,
    revision: int | None = None,
    note_identity: str | None = None,
    owner: str | None = None,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    note_changes.identifier(moc_id)
    if action not in {"create", "show", "link", "unlink-system"}:
        raise InvalidInput("invalid MOC action")
    if action == "show":
        if any(v is not None for v in (title, revision, note_identity, owner, expected_sha256)):
            raise InvalidInput("show does not accept mutation arguments")
    elif type(revision) is not int or revision < (0 if action == "create" else 1):
        raise InvalidInput("MOC mutation requires current revision")
    if action == "create":
        if (
            revision != 0
            or title is None
            or any(v is not None for v in (note_identity, owner, expected_sha256))
        ):
            raise InvalidInput("create requires title and revision zero")
    elif title is not None:
        raise InvalidInput("only create accepts title")
    if action in {"link", "unlink-system"}:
        if note_identity is None:
            raise InvalidInput("MOC association requires identity")
        moc_store.stable_identity(note_identity)
        if action == "link":
            if (
                owner not in {"manual", "system"}
                or expected_sha256 is None
                or not (note_changes.DIGEST.fullmatch(expected_sha256))
            ):
                raise InvalidInput("link requires ownership and current hash")
        elif owner is not None or expected_sha256 is not None:
            raise InvalidInput("unlink-system does not accept ownership or hash")
    with database.locked(config):
        db = database.connect(config.state_root)
        try:
            client_id = auth.client(db, authorization)["id"]
            writing = action != "show"
            if writing:
                auth.grant(db, client_id, vault_id, "add")
                from .organization.note_moc_sync import recover_all

                recover_all(config, db, client_id, vault_id)
            vault = auth.grant(db, client_id, vault_id, "query")
            info = vaults.checked_vault(config, vault).stat()
            key = info.st_dev, info.st_ino
            current = moc_store.load(config, vault_id, moc_id)
            if current is not None and (current.vault_dev, current.vault_ino) != key:
                raise IntegrityError("MOC vault identity changed")
            if action == "create":
                assert title is not None
                value = Record(title, *key, 1, {})
                moc_store.validate(value)
                if current is not None and current != value:
                    raise IntegrityError("MOC already exists with different content")
            else:
                if current is None:
                    raise InvalidInput("MOC is not registered in this Vault")
                if writing and current.revision != revision:
                    raise IntegrityError("MOC revision conflict")
                value = current
            inventory = checked_inventory(config, db, client_id, vault_id, key)
            if action in {"link", "unlink-system"}:
                assert note_identity is not None
                members = {identity: owners.copy() for identity, owners in value.members.items()}
                if action == "link":
                    notes = {e.identity: e for e in note_changes.entries(inventory.notes)}
                    note = notes.get(note_identity)
                    if note is None or note.sha256 != expected_sha256:
                        raise IntegrityError("MOC note absent or current hash conflicts")
                    assert owner is not None
                    members[note_identity] = sorted(set(members.get(note_identity, [])) | {owner})
                elif note_identity in members:
                    owners = [o for o in members[note_identity] if o != "system"]
                    if owners:
                        members[note_identity] = owners
                    else:
                        del members[note_identity]
                if members != value.members:
                    value = replace(value, members=members, revision=value.revision + 1)
            moc_store.validate(value)
            result = preview(value, inventory)
            # Preview and mutation use the same rechecked paths, hashes and physical identities.
            again = checked_inventory(config, db, client_id, vault_id, key)
            if observation(again) != observation(inventory):
                raise IntegrityError("MOC notes changed during observation")
            if writing:
                auth.grant(db, client_id, vault_id, "add")
            auth.grant(db, client_id, vault_id, "query")
            final = vaults.checked_vault(config, vault).stat()
            if (final.st_dev, final.st_ino) != key:
                raise IntegrityError("MOC vault identity changed")
            changed = writing and current != value
            if changed:
                moc_store.save(
                    config,
                    vault_id,
                    moc_id,
                    client_id,
                    value,
                    action,
                    0 if current is None else current.revision,
                )
            archives = []
            note_written = moc_written = False
            if action in {"link", "unlink-system"} and note_identity:
                from . import moc_registration
                from .organization import membership, note_moc_backfill, note_moc_sync

                bindings = {
                    mid: path for path, mid in moc_registration.bindings(config, vault_id).items()
                }
                if moc_id in bindings:
                    note_moc_sync.recover_all(config, db, client_id, vault_id)
                    moc_written = membership.project(
                        config, db, client_id, vault_id, {"id": moc_id, "path": bindings[moc_id]}
                    )
                    note_written = note_moc_backfill.sync_members(
                        config, db, client_id, vault_id, {note_identity}
                    )
                    if note_written:
                        result = preview(
                            value, checked_inventory(config, db, client_id, vault_id, key)
                        )
            if action == "link" and changed and note_identity:
                from .note_placement import prepare_after_moc

                archives = prepare_after_moc(config, db, client_id, vault_id, [note_identity])
            return {
                "moc_id": moc_id,
                "vault_id": vault_id,
                "title": value.title,
                "revision": value.revision,
                "metadata_changed": changed,
                "note_write_performed": note_written,
                "moc_file_write_performed": moc_written,
                **result,
                **({"archive_previews": archives} if archives else {}),
            }
        finally:
            db.close()
