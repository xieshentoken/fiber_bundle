"""Copy immutable original versions into the selected target; no origin permission aliasing."""

import json
from typing import Any

from . import database
from . import vault_merge_journal as journal
from .config import Config, IntegrityError
from .store import Store


def copy(config: Config, identity: str, version: dict[str, Any], source_vault: str) -> None:
    from .vault_merge_execution import authorize

    with database.locked(config), database.connect(config.state_root) as db:
        value, state = journal.load(db, identity)
        authorize(config, db, identity, value)
        if version["version_id"] in value.get("originals", {}):
            return
        selection = value["plan"]["selection"]
        client, target = selection["client_id"], selection["target_vault_id"]
        target_name = db.execute("SELECT name FROM vaults WHERE id=?", (target,)).fetchone()[0]
    from . import object_streams
    from .config import contained
    from .inputs import canonical

    descriptors, keys = [], {}
    event = version["raw_event"]
    assets = [
        {
            "sha256": event["sha256"],
            "byte_count": event["byte_count"],
            "relative_path": event["relative_path"],
            "role": "event",
            "original_name": "event.bin",
            "media_type": "application/octet-stream",
        },
        *version["assets"],
    ]
    # Each original streams to immutable target objects without the shared storage
    # lease. The parent operation lock and visibility barrier remain active.
    for asset in assets:
        source = contained(config.state_root, asset["relative_path"])
        relative = object_streams.assemble(
            config.state_root,
            target,
            [(source, asset["sha256"], asset["byte_count"])],
            asset["sha256"],
            asset["byte_count"],
        )
        path = contained(config.state_root, relative)
        before = path.lstat()
        object_streams.verify(path, asset["sha256"], asset["byte_count"])
        info = path.lstat()
        key = [info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns]
        if key != [
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ]:
            raise IntegrityError("vault_merge_original_changed_during_verification")
        keys[relative] = key
        descriptors.append(
            {k: asset[k] for k in ("sha256", "byte_count", "role", "original_name", "media_type")}
        )
    manifest = canonical(
        {
            "vault": target_name,
            "source_key": "merge:" + identity + ":" + version["version_id"],
            "session_scope": "vault-merge",
            "session_id": identity,
            "retention": "knowledge",
            "assets": descriptors,
        }
    )
    with database.locked(config), database.connect(config.state_root) as db:
        value, state = journal.load(db, identity)
        authorize(config, db, identity, value)
        copies = value.setdefault("originals", {})
        saved = Store(config).commit_prepared_locked(
            db,
            ingress_scope="vault-merge",
            event_id=identity + ":" + version["version_id"],
            manifest_json=manifest,
            object_keys=keys,
            owner_client_id=client,
        )
        if saved.state != "stored":
            raise IntegrityError("vault_merge_original_copy_incomplete")
        copied = [
            dict(r)
            for r in db.execute(
                "SELECT va.asset_id,va.ordinal,a.sha256 FROM version_assets va "
                "JOIN assets a ON a.id=va.asset_id WHERE va.version_id=? ORDER BY va.ordinal",
                (saved.source_version_id,),
            )
        ]
        if [a["sha256"] for a in copied] != [a["sha256"] for a in version["assets"]]:
            raise IntegrityError("vault_merge_original_copy_binding_changed")
        copies[version["version_id"]] = {
            "source_vault_id": source_vault,
            "target_vault_id": target,
            "target_version_id": saved.source_version_id,
            "assets": {
                a["asset_id"]: b["asset_id"] for a, b in zip(version["assets"], copied, strict=True)
            },
        }
        journal.save(db, identity, value, state)


def mapping(db: Any, version: str, target: str) -> dict[str, Any] | None:
    """Compose only completed immutable source-copy edges; never alias origin permissions."""
    edges = []
    for row in db.execute(
        "SELECT payload FROM vault_operations WHERE kind='vault_merge' AND"
        " state='complete' ORDER BY updated_at DESC"
    ):
        edges.extend(json.loads(row[0]).get("originals", {}).items())
    pending: list[tuple[str, dict[str, Any] | None]] = [(version, None)]
    visited = set()
    while pending:
        current, prior = pending.pop()
        if current in visited:
            continue
        visited.add(current)
        if len(visited) > 1000:
            raise IntegrityError("vault_merge_original_mapping_limit")
        for source, edge in edges:
            if source != current:
                continue
            composed = dict(edge)
            if prior:
                composed["source_vault_id"] = prior["source_vault_id"]
                composed["assets"] = {
                    original: edge["assets"][copied] for original, copied in prior["assets"].items()
                }
            if edge["target_vault_id"] == target:
                return composed
            pending.append((edge["target_version_id"], composed))
    return None
