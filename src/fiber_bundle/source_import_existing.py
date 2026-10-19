"""Freeze a new engine request for an exact archived asset without relabeling an old task."""

import sqlite3
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from . import operations, source_imports, source_processing_policy
from .config import Config, InvalidInput
from .objects import digest


def create(
    config: Config,
    db: sqlite3.Connection,
    client: str,
    vault: str,
    receipt: str,
    asset: str,
    engine: str,
    origin: dict[str, Any],
    *,
    profile: str | None = None,
) -> dict[str, Any]:
    saved = db.execute(
        (
            "SELECT source_version_id FROM receipts WHERE job_id=? AND owner_c"
            "lient_id=? AND state='stored'"
        ),
        (receipt, client),
    ).fetchone()
    if saved is None:
        raise InvalidInput("import_archived_source_required")
    raw = operations.original(config, db, client, vault, saved[0], asset, 16 * 1024 * 1024)
    name, _ = operations.original_metadata(config, db, vault, saved[0], asset)
    policy = source_processing_policy.inspect(config, None)
    continuation = ":" + str(origin["retry_of"]) if origin.get("retry_of") else ""
    if profile:
        continuation += ":" + profile
    submission = str(
        uuid5(
            NAMESPACE_URL,
            f"fiber-engine-request:{vault}:{saved[0]}:{asset}:{engine}:"
            f"{policy['configuration_sha256']}{continuation}",
        )
    )
    existing = db.execute(
        "SELECT * FROM source_imports WHERE client_id=? AND vault_id=? AND submission_id=?",
        (client, vault, submission),
    ).fetchone()
    if existing is not None:
        return source_imports.projection(db, source_imports.get(config, db, client, existing["id"]))
    request = dict(
        client_id=client,
        vault_id=vault,
        submission_id=submission,
        filename=name,
        source_sha256=digest(raw),
        source_bytes=len(raw),
        action="process",
        engine=engine,
        profile=profile
        or (
            "ocr"
            if engine == "ordinary"
            and source_imports.FORMATS.get("." + name.lower().rsplit(".", 1)[-1], (None, None))[0]
            == "image"
            else "conversion"
            if engine == "ordinary" and name.lower().endswith(".doc")
            else "native"
        ),
        expert_profile="native-text",
        expert_backend=None,
        workpack_import_id=None,
        references={},
        overrides={},
        configuration_sha256=policy["configuration_sha256"],
        source_origin={"receipt_id": receipt, "version_id": saved[0], "asset_id": asset, **origin},
    )
    result = source_imports.create(config, db, request)
    if result["state"] == "uploading":
        # The new engine intent points to this receipt without changing original bytes or identity.
        db.execute(
            (
                "UPDATE source_imports SET state='archived',received_bytes=?,recei"
                "pt_id=?,asset_id=? WHERE id=? AND state='uploading'"
            ),
            (len(raw), receipt, asset, result["id"]),
        )
        result = source_imports.projection(db, source_imports.get(config, db, client, result["id"]))
    return result
