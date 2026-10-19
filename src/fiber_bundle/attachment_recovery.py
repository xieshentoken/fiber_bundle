"""Explicit original-projection reconciliation; never replace an edited or unclaimed copy."""

import json
from typing import Any

from . import attachment_projection, auth, operations, publication_file_io, vault_directories
from .config import Config, IntegrityError, InvalidInput
from .inputs import canonical


def listing(db: Any, vault: str) -> list[dict[str, Any]]:
    return [
        {"id": row["id"], "state": row["state"], **json.loads(row["payload"])}
        for row in db.execute(
            "SELECT * FROM vault_operations WHERE vault_id=? "
            "AND kind='attachment_projection' ORDER BY updated_at DESC LIMIT 100",
            (vault,),
        )
    ]


def resume(config: Config, db: Any, vault: str, identity: str) -> dict[str, Any]:
    row = db.execute(
        "SELECT * FROM vault_operations WHERE id=? AND vault_id=? AND kind='attachment_projection'",
        (identity, vault),
    ).fetchone()
    if row is None or row["state"] != "reconcile":
        raise InvalidInput("attachment_projection_not_reconcilable")
    value = json.loads(row["payload"])
    client = value["client_id"]
    auth.grant(db, client, vault, "add")
    auth.grant(db, client, vault, "original")
    from . import vault_save_policy

    root, _, _ = vault_directories.vault_root(config, db, vault)
    policy = vault_save_policy.get(config, db, vault)
    if (
        not policy["publication_ready"]
        or policy["revision"] != value["policy_revision"]
        or vault_directories.key(root) != value["root_key"]
        or attachment_projection.epoch(db, client, vault) != value["permission_epoch"]
    ):
        raise InvalidInput("attachment_projection_binding_changed")
    if value["claim"]:
        from .objects import digest

        expected = operations.original(
            config,
            db,
            client,
            vault,
            value["source_version_id"],
            value["asset_id"],
            attachment_projection.LIMIT,
        )
        raw, key = publication_file_io.read(root, value["path"])
        if key[:2] != value["claim"] or raw != expected[: len(raw)] or len(raw) > len(expected):
            raise IntegrityError("attachment_projection_copy_diverged")
        if raw != expected:
            publication_file_io.remove_owned(root, value["path"], key, digest(raw))
            value["claim"] = None
            db.execute(
                "UPDATE vault_operations SET payload=? WHERE id=?", (canonical(value), identity)
            )
    return attachment_projection.project(
        config, db, client, vault, value["source_version_id"], value["asset_id"]
    )
