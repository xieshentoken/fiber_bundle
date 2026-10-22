"""Audited local physical identity rebinding and quarantine after authenticated restoration."""

import json
import shutil
import sqlite3
import time
from contextlib import closing
from typing import Any

from . import database
from .backup_bundle import file_sha, vault_root
from .config import Config, IntegrityError, contained
from .inputs import canonical
from .query_policy_store import write_private


def _quarantine_sidecars(config: Config) -> dict[str, int]:
    """Keep journal evidence but prevent restored admissions and outbound intents replaying."""
    rules: dict[str, dict[str, tuple[str, tuple[str, ...]]]] = {
        "document-creation.sqlite3": {"tasks": ("cancelled", ("prepared",))},
        "moc-dispatch.sqlite3": {"jobs": ("failed", ("queued", "claimed"))},
        "knowledge-graph.sqlite3": {
            "jobs": ("interrupted", ("queued", "running")),
            "generations": ("stale", ("ready",)),
            "writebacks": ("unknown", ("prepared", "applying")),
        },
        "message-channels.sqlite3": {
            "channels": (
                "paused",
                (
                    "running",
                    "pair",
                    "login",
                    "check",
                    "error",
                    "unpaired",
                    "stopping",
                    "unbinding",
                    "removing",
                ),
            ),
            "events": ("unknown", ("sending",)),
            "legacy_channels": (
                "paused",
                ("running", "error", "unpaired", "stopping", "unbinding", "removing"),
            ),
            "channel_operations": ("interrupted", ("pending", "needs_review")),
            "channel_intake_records": ("needs_review", ("waiting", "queued", "archiving")),
        },
        "weixin-inbox.sqlite3": {
            "fiber_expert_commands": ("failed", ("queued", "dispatching")),
            "wx_batches": ("cancelled", ("buffering", "frozen", "selected")),
            "wx_download_attempts": ("interrupted", ("queued", "running")),
            **{
                name: ("unknown", ("pending", "sending"))
                for name in (
                    "wx_confirmation_prompts",
                    "wx_receipt_deliveries",
                    "wx_task_deliveries",
                    "wx_media_terminology_prompts",
                )
            },
        },
    }
    changed = {}
    index_root = contained(config.state_root, "note-search")
    if index_root.exists():
        from .note_changes import identifier
        from .note_search_jobs import read as read_index_job
        from .note_search_jobs import record as record_index_job

        for job_path in sorted(index_root.glob("*.job.json")):
            vault_id = job_path.name.removesuffix(".job.json")
            identifier(vault_id)
            job = read_index_job(config, vault_id)
            if job is not None:
                record_index_job(
                    config,
                    vault_id,
                    "interrupted" if job["state"] == "building" else "stale",
                    reason="restored_explicit_update_required",
                )
        for path in sorted(index_root.glob("*.sqlite3")):
            identifier(path.stem)
            with closing(sqlite3.connect(path)) as index, index:
                index.execute("PRAGMA synchronous=EXTRA")
                index.execute("PRAGMA fullfsync=ON")
                changed["note-search/" + path.name] = index.execute(
                    "UPDATE generation SET state='stale' WHERE state='ready'"
                ).rowcount
    for name, tables in rules.items():
        path = contained(config.state_root, name)
        if not path.exists():
            continue
        with closing(sqlite3.connect(path)) as side, side:
            side.execute("PRAGMA foreign_keys=ON")
            side.execute("PRAGMA synchronous=EXTRA")
            side.execute("PRAGMA fullfsync=ON")
            for table, (terminal, pending) in tables.items():
                if not side.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                ).fetchone():
                    continue
                placeholders = ",".join("?" for _ in pending)
                changed[name + ":" + table] = side.execute(
                    f"UPDATE {table} SET state=? WHERE state IN ({placeholders})",
                    (terminal, *pending),
                ).rowcount
    channel_path = contained(config.state_root, "message-channels.sqlite3")
    if channel_path.exists():
        with closing(sqlite3.connect(channel_path)) as side, side:
            if side.execute("SELECT 1 FROM sqlite_master WHERE name='channel_services'").fetchone():
                side.execute(
                    "UPDATE channel_services SET revision=revision+1,"
                    "value=json_set(value,'$.enabled',json('false'))"
                )
            if side.execute(
                "SELECT 1 FROM sqlite_master WHERE name='channel_intake_policies'"
            ).fetchone():
                side.execute(
                    "UPDATE channel_intake_policies SET revision=revision+1,"
                    "value=json_set(value,'$.enabled',json('false'))"
                )
                side.execute(
                    "UPDATE channel_intake_records "
                    "SET value=json_set(value,'$.extraction','cancelled'),"
                    "reason='intake_restored_confirmation_required'"
                )
            changed["message-channels.sqlite3:pending"] = side.execute(
                "UPDATE events SET state='cancelled' "
                "WHERE state IN ('captured','processing','gui_processing','ready')"
            ).rowcount
    from .suppliers.restore import quarantine as quarantine_suppliers

    if (config.state_root / "supplier-intelligence.sqlite3").exists():
        changed["supplier-intelligence"] = quarantine_suppliers(config)
    return changed


def prepare(config: Config, manifest: dict[str, Any]) -> dict[str, Any]:
    """Preserve evidence/fees; revoke imported access and stop pending work without replay."""
    db = sqlite3.connect(contained(config.state_root, "metadata.sqlite3"))
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA synchronous=EXTRA")
    db.execute("PRAGMA fullfsync=ON")
    changed: dict[str, int] = {}
    roots = {v["id"]: vault_root(config, v, restore=True) for v in manifest["vaults"]}
    hashes = {(e["section"], e["path"]): e.get("sha256") for e in manifest["entries"]}
    # The backup excludes detached/trashed bodies but retains their historical sidecars.
    excluded_vaults = {
        row[0]
        for row in db.execute(
            "SELECT id FROM vaults WHERE attachment_state IN ('detached','trashed')"
        )
    }
    try:
        if db.execute("PRAGMA user_version").fetchone()[0] != database.SCHEMA_VERSION:
            raise IntegrityError("restore_schema_mismatch")
        with db:
            # Imported metadata previews never authorize operations on restored roots.
            db.execute("DELETE FROM vault_directory_previews")
            changed["vault_save_operations_interrupted"] = db.execute(
                "UPDATE vault_operations SET state='reconcile' WHERE kind='save_policy' "
                "AND state IN ('prepared','creating','checking')"
            ).rowcount
            changed["agent_connections_paused"] = db.execute(
                "UPDATE agent_connections SET state='repair_required',revision=revision+1,"
                "applied_json=NULL,probe_json=NULL,test_json=NULL WHERE state!='revoked'"
            ).rowcount
            db.execute(
                "UPDATE agent_connection_operations SET state='reconcile',"
                "error_code='agent_restored',cancel_requested=1 "
                "WHERE state IN ('queued','running')"
            )
            for vault in manifest["vaults"]:
                info = roots[vault["id"]].stat()
                if vault.get("root_kind", "legacy") != "legacy":
                    db.execute(
                        "UPDATE vaults SET absolute_root=?,root_kind='managed' WHERE id=?",
                        (str(roots[vault["id"]]), vault["id"]),
                    )
                db.execute(
                    "UPDATE vaults SET directory_dev=?,directory_ino=? WHERE id=?",
                    (info.st_dev, info.st_ino, vault["id"]),
                )
            from .publication_file_io import read as layout_read

            for location in db.execute("SELECT * FROM publication_locations").fetchall():
                plan = json.loads(location["value"])
                if location["state"] != "published" or location["vault_id"] in excluded_vaults:
                    db.execute(
                        "UPDATE publication_locations SET state='reconcile' WHERE kind=? AND "
                        "publication_id=?",
                        (location["kind"], location["publication_id"]),
                    )
                    continue
                layout_root = roots[location["vault_id"]]
                for relative in plan["mapping"].values():
                    raw, layout_key = layout_read(layout_root, relative)
                    if file_sha(contained(layout_root, relative)) != hashes.get(
                        ("vault:" + location["vault_id"], relative)
                    ):
                        raise IntegrityError("restore_layout_file_changed")
                    plan["file_keys"][relative] = layout_key
                    plan["claims"][relative] = layout_key[:2]
                plan["root_key"] = [layout_root.stat().st_dev, layout_root.stat().st_ino]
                db.execute(
                    "UPDATE publication_locations SET value=? WHERE kind=? AND publication_id=?",
                    (canonical(plan), location["kind"], location["publication_id"]),
                )
            # Restored execution authorizations cannot replay queued work on new
            # physical roots or with imported credentials, even after re-enabling a client.
            db.execute(
                "UPDATE automatic_jobs SET state='reconcile',reason='automatic_res"
                "tored_requires_new_authorization' WHERE state NOT IN ('complete',"
                "'cancelled')"
            )
            for auto in db.execute("SELECT * FROM automatic_policies").fetchall():
                policy_value = json.loads(auto["value"])
                policy_value["review_mode"] = "manual"
                policy_value["publish_mode"] = "manual"
                if policy_value.get("policy_version") == 2:
                    policy_value["enabled"] = False
                    policy_value["direct_to_inbox"] = False
                db.execute(
                    "UPDATE automatic_policies SET value=?,revision=revision+1 WHERE vault_id=?",
                    (canonical(policy_value), auto["vault_id"]),
                )
            db.execute(
                "UPDATE vault_operations SET state='cancelled' WHERE kind='vault_m"
                "erge' AND state='prepared'"
            )
            db.execute("DELETE FROM vault_admissions")
            # These previews never wrote Vault files; restored roots cannot honor their tokens.
            db.execute(
                "UPDATE vault_operations SET state='cancelled' WHERE state='prepared' AND kind IN "
                "('note_placement','layout_migration','publication_rebind','publication_preview')"
            )
            db.execute(
                "UPDATE vault_operations SET state='interrupted' WHERE kind IN "
                "('expert_authoring','video_link','skill_export') "
                "AND state NOT IN ('complete','cancelled')"
            )
            db.execute(
                "UPDATE vault_operations SET state='reconcile' "
                "WHERE state NOT IN ('complete','completed','cancelled','rolled_back','published')"
            )
            changed["admin_sessions"] = db.execute("DELETE FROM admin_sessions").rowcount
            changed["admin_auth"] = db.execute("DELETE FROM admin_auth").rowcount
            changed["clients_disabled"] = db.execute("UPDATE clients SET active=0").rowcount
            tables = {
                "source_imports": ("interrupted", ("uploading",)),
                "source_expert_tasks": ("interrupted", ("prepared", "running")),
                "source_expert_attempts": ("interrupted", ("running",)),
                "query_jobs": ("cancelled", ("reserved", "running")),
                "execution_runs": ("interrupted", ("queued", "running", "waiting_budget")),
                "local_query_tasks": ("interrupted", ("queued", "running")),
                "article_tasks": ("interrupted", ("queued", "capturing")),
                "document_tasks": ("interrupted", ("queued", "extracting")),
                "media_tasks": ("interrupted", ("queued", "running")),
                "media_processing": ("interrupted", ("queued", "running")),
                "video_tasks": (
                    "interrupted",
                    ("queued", "launching", "running", "waiting_budget"),
                ),
                "media_link_tasks": ("interrupted", ("queued", "running")),
                "document_creation_continuations": ("cancelled", ("queued", "ready")),
                "document_creation_deliveries": ("failed", ("pending",)),
                "document_creation_saves": ("conflict", ("prepared", "publishing")),
                "document_publications": ("conflict", ("prepared", "publishing")),
                "media_publications": ("conflict", ("prepared", "publishing")),
            }
            for table, (terminal, pending) in tables.items():
                placeholders = ",".join("?" for _ in pending)
                changed[table] = db.execute(
                    f"UPDATE {table} SET state=? WHERE state IN ({placeholders})",
                    (terminal, *pending),
                ).rowcount
            changed["document_delivery_unknown"] = db.execute(
                "UPDATE document_creation_deliveries SET state='unknown' WHERE state='sending'"
            ).rowcount
            db.execute(
                "UPDATE document_creation_deliveries SET notice_state='unavailable' "
                "WHERE notice_state IN ('pending','sending')"
            )
            if db.execute("PRAGMA foreign_key_check").fetchall():
                raise IntegrityError("restore_foreign_key_failure")
    finally:
        db.close()
    changed.update(_quarantine_sidecars(config))
    rebindings: list[dict[str, Any]] = []
    physical = {
        (p["section"], tuple(p["key"])): p["path"] for p in manifest.get("physical_files", [])
    }
    hashes = {(e["section"], e["path"]): e.get("sha256") for e in manifest["entries"]}
    identity = contained(config.state_root, "note-identities.sqlite3")
    if identity.exists():
        side = sqlite3.connect(identity)
        side.row_factory = sqlite3.Row
        try:
            with side:
                for row in side.execute("SELECT * FROM registrations").fetchall():
                    root = roots.get(row["vault_id"])
                    if root is None:
                        if row["vault_id"] not in excluded_vaults:
                            raise IntegrityError("restore_identity_vault_missing")
                        # Keep identities, hashes and events; no old directory can resolve them.
                        side.execute(
                            "UPDATE registrations SET vault_dev=0,vault_ino=0,revision=revision+1 "
                            "WHERE vault_id=? AND identity=?",
                            (row["vault_id"], row["identity"]),
                        )
                        rebindings.append(
                            {
                                "vault_id": row["vault_id"],
                                "identity": row["identity"],
                                "before": dict(row),
                                "state": "vault_not_restored_manual_rebind_required",
                            }
                        )
                        continue
                    source_key = (row["file_dev"], row["file_ino"], row["file_birth"])
                    relative = physical.get(("vault:" + row["vault_id"], source_key))
                    if relative is None:
                        # Deleted/replaced originals retain their logical identity and hash;
                        # an impossible current file key keeps path reuse fail-closed.
                        directory = root.stat()
                        side.execute(
                            "UPDATE registrations SET file_dev=0,file_ino=0,file_birth=?,"
                            "vault_dev=?,vault_ino=?,revision=revision+1 "
                            "WHERE vault_id=? AND identity=?",
                            (
                                float(0).hex(),
                                directory.st_dev,
                                directory.st_ino,
                                row["vault_id"],
                                row["identity"],
                            ),
                        )
                        rebindings.append(
                            {
                                "vault_id": row["vault_id"],
                                "identity": row["identity"],
                                "before": dict(row),
                                "state": "original_absent_manual_rebind_required",
                            }
                        )
                        continue
                    path = contained(root, relative)
                    sha = hashes.get(("vault:" + row["vault_id"], relative))
                    if not path.is_file() or file_sha(path) != sha:
                        raise IntegrityError("restore_identity_requires_manual_rebind")
                    info, directory = path.stat(), root.stat()
                    birth = getattr(info, "st_birthtime", None)
                    if birth is None:
                        raise IntegrityError("restore_file_identity_unavailable")
                    old = dict(row)
                    key = (
                        info.st_dev,
                        info.st_ino,
                        float(birth).hex(),
                        directory.st_dev,
                        directory.st_ino,
                    )
                    side.execute(
                        "UPDATE registrations SET relative_path=?,sha256=?,file_dev=?,file_ino=?,"
                        "file_birth=?,vault_dev=?,vault_ino=?,revision=revision+1 "
                        "WHERE vault_id=? AND identity=?",
                        (relative, sha, *key, row["vault_id"], row["identity"]),
                    )
                    rebindings.append(
                        {
                            "vault_id": row["vault_id"],
                            "identity": row["identity"],
                            "before": old,
                            "after_physical_key": key,
                        }
                    )
        finally:
            side.close()
    moc = contained(config.state_root, "moc-associations.sqlite3")
    if moc.exists():
        side = sqlite3.connect(moc)
        side.row_factory = sqlite3.Row
        try:
            with side:
                for row in side.execute("SELECT * FROM mocs").fetchall():
                    root = roots.get(row["vault_id"])
                    if root is None and row["vault_id"] not in excluded_vaults:
                        raise IntegrityError("restore_moc_vault_missing")
                    moc_directory = root.stat() if root is not None else None
                    record = json.loads(row["record_json"])
                    before = dict(record)
                    record.update(
                        vault_dev=moc_directory.st_dev if moc_directory else 0,
                        vault_ino=moc_directory.st_ino if moc_directory else 0,
                    )
                    side.execute(
                        "UPDATE mocs SET record_json=? WHERE vault_id=? AND id=?",
                        (canonical(record), row["vault_id"], row["id"]),
                    )
                    rebindings.append(
                        {
                            "vault_id": row["vault_id"],
                            "moc_id": row["id"],
                            "before": before,
                            "after": record,
                        }
                    )
        finally:
            side.close()
    value = {
        "schema": "restore-rebind/v1",
        "bundle_id": manifest["bundle_id"],
        "time": time.time(),
        "quarantine": changed,
        "rebindings": rebindings,
        "fees_and_historical_events_preserved": True,
        "external_credentials_and_browser_sessions": "reconfigure",
        "historical_physical_ownership": "preserved_evidence_requires_reconfirmation",
    }
    audit = contained(config.state_root, "restore-rebind.json")
    if audit.exists():
        from .config import make_directory

        history_root = contained(config.state_root, "snapshots")
        make_directory(history_root)
        history = contained(history_root, "restore-history-" + manifest["bundle_id"] + ".json")
        if history.exists():
            raise IntegrityError("restore_audit_history_conflict")
        shutil.copyfile(audit, history, follow_symlinks=False)
        history.chmod(0o600)
    write_private(audit, canonical(value).encode())
    return value
