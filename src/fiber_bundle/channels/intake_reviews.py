"""Literal derived-content review and exclusive publication, without model execution rows."""

import json
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

from .. import (
    auth,
    database,
    objects,
    publication_files,
    publication_layout,
    publication_locations,
    vaults,
)
from ..config import Config, InvalidInput, contained
from ..inputs import canonical
from ..store import Store
from . import intake_repository as repository
from . import store
from .intake_processing import record


def load(
    config: Config, main: Any, side: Any, identity: str, batch: str, index: int
) -> tuple[dict[str, Any], Any, dict[str, Any], dict[str, bytes]]:
    channel, _ = repository.channel(config, identity)
    if side is None:
        raise auth.Denied(404)
    row = side.execute(
        "SELECT * FROM channel_intake_records WHERE channel_id=? AND batch_id=?", (identity, batch)
    ).fetchone()
    if row is None or row["state"] != "stored":
        raise auth.Denied(404)
    value = json.loads(row["value"])
    if not 0 <= index < len(value["links"]):
        raise auth.Denied(404)
    link = value["links"][index]
    receipt = link.get("receipt")
    if not receipt:
        raise InvalidInput("intake_draft_unavailable")
    saved = main.execute(
        "SELECT * FROM receipts WHERE job_id=? AND owner_client_id=?",
        (receipt["job_id"], channel["value"]["client_id"]),
    ).fetchone()
    if saved is None or saved["source_version_id"] != receipt["source_version_id"]:
        raise auth.Denied(404)
    scope = auth.grant(main, channel["value"]["client_id"], value["target_vault_id"], "original")
    vaults.checked_vault(config, scope)
    Store(config).verify_receipt_locked(main, receipt["job_id"])
    files = {}
    for name, descriptor in link["files"].items():
        asset = main.execute(
            "SELECT a.* FROM assets a JOIN version_assets va ON va.asset_id=a.id "
            "WHERE va.version_id=? AND va.original_name=? AND a.vault_id=? AND a.sha256=?",
            (
                receipt["source_version_id"],
                descriptor["original_name"],
                value["target_vault_id"],
                descriptor["sha256"],
            ),
        ).fetchone()
        if asset is None or asset["byte_count"] != descriptor["bytes"]:
            raise InvalidInput("intake_draft_changed")
        files[name] = objects.read_object(
            config.state_root, asset["relative_path"], asset["sha256"], asset["byte_count"]
        )
    return channel, row, value, files


def packet(
    config: Config, identity: str, batch: str, index: int, *, leased: bool = False
) -> dict[str, Any]:
    with repository.lease(config, leased), store.readonly(config) as side:
        main = database.connect(config.state_root)
        try:
            _, _, value, files = load(config, main, side, identity, batch, index)
            link = value["links"][index]
            return {
                "markdown": files["note.md"].decode(),
                "files": list(files),
                "digest": objects.digest(canonical(link["files"]).encode()),
                "review": link.get("review"),
                "system_review": link.get("system_review"),
                "publication": {
                    **link["publication"],
                    **publication_locations.wire(main, "intake", link["publication"]["id"]),
                }
                if link.get("publication")
                else None,
                "metadata": link["metadata"],
            }
        finally:
            main.close()


def review(
    config: Config,
    identity: str,
    batch: str,
    index: int,
    expected: str,
    reason: str,
    *,
    leased: bool = False,
) -> dict[str, Any]:
    with repository.lease(config, leased), store.opened(config) as side, database.transaction(side):
        main = database.connect(config.state_root)
        try:
            _, row, value, _ = load(config, main, side, identity, batch, index)
            link = value["links"][index]
            if (
                link.get("publication")
                or objects.digest(canonical(link["files"]).encode()) != expected
            ):
                raise InvalidInput("intake_draft_changed")
            link["review"] = {"id": str(uuid4()), "digest": expected, "reason": reason.strip()}
            record(side, identity, batch, row["revision"], row["state"], row["reason"], value)
            return dict(link["review"])
        finally:
            main.close()


def publish(
    config: Config,
    identity: str,
    batch: str,
    index: int,
    review_id: str,
    *,
    leased: bool = False,
    preview_only: bool = False,
    system_review_id: str | None = None,
    automatic_task: str | None = None,
) -> dict[str, Any]:
    with repository.lease(config, leased), store.opened(config) as side:
        main = database.connect(config.state_root)
        try:
            channel, row, value, files = load(config, main, side, identity, batch, index)
            link = value["links"][index]
            if system_review_id and automatic_task:
                from ..intake_abort_store import guard as abort_guard

                abort_guard(config, {"channel": identity, "batch": batch, "index": index})
                from ..automatic_intake import source_digest
                from ..automatic_review import credential

                system = credential(
                    config,
                    main,
                    system_review_id,
                    "intake",
                    automatic_task,
                    source_digest(link),
                    objects.digest(canonical(link["files"]).encode()),
                )
                review_id = system_review_id
                link["system_review"] = {
                    "id": review_id,
                    "actor": system["actor"],
                    "credential_sha256": system["credential_sha256"],
                }
            elif (
                not link.get("review")
                or link["review"]["id"] != review_id
                or link["review"]["digest"] != objects.digest(canonical(link["files"]).encode())
            ):
                raise InvalidInput("intake_review_required")
            target = value["target_vault_id"]
            if (link.get("publication") or {}).get("state") != "published":
                from ..intake_abort_store import guard as abort_guard

                abort_guard(config, {"channel": identity, "batch": batch, "index": index})
            scope = auth.grant(main, channel["value"]["client_id"], target, "add")
            vault = vaults.checked_vault(config, scope)
            from ..note_migration_store import guard

            guard(config, target)
            publication = link.get("publication")
            if publication is None:
                identifier = str(
                    uuid5(
                        NAMESPACE_URL, "fiber-intake:" + identity + ":" + batch + ":" + str(index)
                    )
                )
                publication_layout.prepare(
                    config, main, "intake", identifier, target, files, refresh_preview=preview_only
                )
                planned = publication_locations.get(main, "intake", identifier)
                if planned and planned["state"] == "prepared" and not planned["plan"]["claims"]:
                    planned["plan"]["intake_source"] = {
                        "channel_id": identity,
                        "batch_id": batch,
                        "link_index": index,
                        "source_version_id": link["receipt"]["source_version_id"],
                        "source_url": link.get("url"),
                        "review_id": review_id,
                        "review_actor": system["actor"] if system_review_id else "local_admin",
                        "review_type": "system" if system_review_id else "human",
                        "analysis_sha256": link.get("files", {})
                        .get("analysis.md", {})
                        .get("sha256"),
                        "processing_gaps": link.get("processing_gaps", []),
                        "processing_kind": link.get("metadata", {}).get("kind"),
                        "video_skill_sha256": link.get("metadata", {}).get("video_skill_sha256"),
                        "video_job_id": link.get("metadata", {}).get("video_job_id"),
                    }
                    main.execute(
                        "UPDATE publication_locations SET value=? WHERE kind='intake' "
                        "AND publication_id=?",
                        (canonical(planned["plan"]), identifier),
                    )
                publication = {
                    "id": identifier,
                    "state": "preparing",
                    "relative_path": "intake-" + identifier,
                    "staging_path": ".fiber-stage-" + identifier,
                    "vault_dev": vault.stat().st_dev,
                    "vault_ino": vault.stat().st_ino,
                }
                if preview_only:
                    return publication_layout.preview_result(
                        main,
                        "intake",
                        identifier,
                        publication["relative_path"],
                        vault_id=target,
                        root=vault,
                    )
                link["publication"] = publication
                with database.transaction(side):
                    record(
                        side, identity, batch, row["revision"], row["state"], row["reason"], value
                    )
            if (vault.stat().st_dev, vault.stat().st_ino) != (
                publication["vault_dev"],
                publication["vault_ino"],
            ):
                raise InvalidInput("intake_publication_vault_changed")
            if preview_only:
                return publication_layout.preview_result(
                    main, "intake", publication["id"], publication["relative_path"]
                )
            layout_result = publication_layout.publish(
                config,
                main,
                "intake",
                publication["id"],
                channel["value"]["client_id"],
                files,
                defer_visibility=True,
            )
            if layout_result is not None:
                publication.update(layout_result, state="published")
                # Keep intake invisible until its review sidecar transaction has committed.
                link["state"] = "published"
                with database.transaction(side):
                    record(
                        side, identity, batch, row["revision"], row["state"], row["reason"], value
                    )
                main.execute(
                    "UPDATE publication_locations SET state='published' WHERE kind='intake' "
                    "AND publication_id=?",
                    (publication["id"],),
                )
                return dict(publication)
            relative, staging = publication["relative_path"], publication["staging_path"]
            vaults.require_scope(config, scope, relative)
            vaults.require_scope(config, scope, staging)
            # An explicit retry verifies committed bytes; never overwrite a human edit.
            if publication["state"] == "published":
                publication_files.verify(vault, relative, files)
                return dict(publication)
            if publication["state"] == "preparing":
                if contained(vault, relative).exists() or contained(vault, staging).exists():
                    raise InvalidInput("intake_publication_requires_reconciliation")
                publication_files.stage(vault, staging, files)
                publication["state"] = "ready"
                with database.transaction(side):
                    record(
                        side, identity, batch, row["revision"], row["state"], row["reason"], value
                    )
            if contained(vault, relative).exists():
                if contained(vault, staging).exists():
                    raise InvalidInput("intake_publication_requires_reconciliation")
            else:
                publication_files.verify(vault, staging, files)
                auth.grant(main, channel["value"]["client_id"], target, "add")
                publication_files.rename_exclusive(vault, staging, relative)
            publication_files.verify(vault, relative, files)
            publication["state"] = "published"
            link["state"] = "published"
            with database.transaction(side):
                record(side, identity, batch, row["revision"], row["state"], row["reason"], value)
            return dict(publication)
        finally:
            main.close()
