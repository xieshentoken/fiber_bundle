"""Authorization, immutable legacy snapshots and domain ledger commands."""

import json
import time
from typing import Any

from .. import auth, note_inventory, vaults
from ..config import Config, InvalidInput
from ..inputs import canonical
from ..objects import digest
from . import legacy, store
from .contracts import SavePolicy


def authorize(config: Config, db: Any, client: str, vault: str, *, write: bool = False) -> Any:
    row = auth.grant(db, client, vault, "query")
    if write:
        auth.grant(db, client, vault, "add")
    vaults.checked_vault(config, row)
    return row


def observation(path: Any) -> list[int]:
    info = path.lstat()
    return [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]


def preview(config: Config, db: Any, client: str, vault: str) -> dict[str, Any]:
    row = authorize(config, db, client, vault)
    inventory = note_inventory.read(config, db, row, registered=False)
    notes = [
        {
            "path": n.relative_path,
            "title": n.title,
            "text": n.text,
            "sha256": n.sha256,
            "observation": observation(inventory.root / n.relative_path),
        }
        for n in inventory.notes
    ]
    result = legacy.project(notes)
    return result


def import_legacy(
    config: Config, db: Any, client: str, vault: str, request_id: str, expected: str
) -> dict[str, Any]:
    authorize(config, db, client, vault, write=True)
    value = preview(config, db, client, vault)
    if value["snapshot_sha256"] != expected:
        raise InvalidInput("supplier_legacy_changed")
    with store.connection(config, write=True) as side:
        assert side is not None
        prior = side.execute(
            "SELECT digest,result FROM operations WHERE vault=? AND id=?", (vault, request_id)
        ).fetchone()
        if prior:
            if prior[0] != expected:
                raise InvalidInput("supplier_request_conflict")
            return dict(json.loads(prior[1]))
        store.put(
            side, vault, "snapshot", {"id": expected, "kind": "legacy", "notes": value["notes"]}
        )
        incoming_sources = {s["id"] for s in value["records"]["source"]}
        for kind in ("source", "supplier"):
            incoming = {r["id"] for r in value["records"][kind]}
            for row in side.execute(
                "SELECT value FROM records WHERE vault=? AND kind=?", (vault, kind)
            ).fetchall():
                old = json.loads(row[0])
                if old.get("legacy") and old["id"] not in incoming:
                    store.put(side, vault, kind, {**old, "active": False})
        for kind in ["contact", "product", "quote", "issue"]:
            incoming_ids = {r["id"] for r in value["records"][kind]}
            for row in side.execute(
                "SELECT value FROM records WHERE vault=? AND kind=?", (vault, kind)
            ).fetchall():
                old = json.loads(row[0])
                if (
                    old.get("legacy")
                    and old.get("source_id") in incoming_sources
                    and old["id"] not in incoming_ids
                ):
                    store.put(side, vault, kind, {**old, "active": False})
        for kind, rows in value["records"].items():
            for item in rows:
                existing = side.execute(
                    "SELECT value FROM records WHERE vault=? AND kind=? AND id=?",
                    (vault, kind, item["id"]),
                ).fetchone()
                if existing:
                    old = json.loads(existing[0])
                    if old != item:
                        store.put(
                            side,
                            vault,
                            "history",
                            {
                                "id": kind + ":" + item["id"] + ":" + digest(existing[0].encode()),
                                "kind": kind,
                                "record": old,
                            },
                        )
                    if old.get("user_revision", 0):
                        item = {
                            **item,
                            "overrides": old.get("overrides", {}),
                            "user_revision": old["user_revision"],
                            "correction_ids": old.get("correction_ids", []),
                        }
                store.put(side, vault, kind, item)
        result = {
            "state": "imported",
            "counts": value["counts"],
            "snapshot_sha256": expected,
            "original_state": "missing",
            "vault_files_written": False,
        }
        side.execute(
            "INSERT INTO operations VALUES(?,?,?,?)",
            (vault, request_id, expected, canonical(result)),
        )
    return result


def save_policy(config: Config, db: Any, data: SavePolicy) -> dict[str, Any]:
    authorize(config, db, data.client_id, data.vault_id, write=True)
    current = store.profile(config, data.vault_id)
    if current["revision"] != data.expected_revision:
        raise InvalidInput("supplier_policy_changed")
    result = {"revision": current["revision"] + 1, "settings": data.settings.model_dump()}
    with store.connection(config, write=True) as side:
        assert side is not None
        if data.settings.enabled and not current["settings"]["enabled"]:
            store.put(
                side,
                data.vault_id,
                "activation",
                {"id": "current", "after": time.time(), "client_id": data.client_id},
            )
        side.execute(
            "INSERT INTO profiles VALUES(?,?,?) ON CONFLICT(vault) DO UPDATE SET "
            "revision=excluded.revision,value=excluded.value",
            (data.vault_id, result["revision"], canonical(result["settings"])),
        )
    return result


def validate_sources(config: Config, db: Any, client: str, vault: str) -> None:
    root = vaults.checked_vault(config, authorize(config, db, client, vault))
    from ..vault_scope import for_vault

    scope = for_vault(root, auth.grant(db, client, vault, "query"))
    for source in store.rows(config, vault, "source") + store.rows(config, vault, "supplier"):
        if not source.get("active", True):
            continue
        relative = source.get("path")
        if relative:
            from ..config import contained

            if not scope.allows(relative):
                raise InvalidInput("supplier_scope_changed")
            path = contained(root, relative)
            try:
                current = observation(path)
            except OSError as error:
                raise InvalidInput("supplier_sources_changed") from error
            if path.is_symlink() or current != source["observation"]:
                raise InvalidInput("supplier_sources_changed")
