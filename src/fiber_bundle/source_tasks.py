"""Idempotent GUI source task admission and one explicit, finite execution launch."""

import json
import sqlite3
import subprocess
import sys
import threading
from dataclasses import asdict
from typing import Any

from . import (
    article_bindings,
    document_bindings,
    execution_ledger,
    objects,
    source_processing_contract,
    source_processing_policy,
    source_selection,
    source_task_policy,
)
from .article_worker import CapturePolicy
from .config import Config, InvalidInput, contained, make_directory
from .document_worker import DocumentPolicy
from .execution_config import ExecutionConfig
from .inputs import AssetInput, canonical
from .operations import original
from .query_policy_store import write_private
from .runtime_environment import child_environment
from .source_task_guard import SourceBinding, authorize
from .store import Store


def submit(
    config: Config,
    db: sqlite3.Connection,
    execution: ExecutionConfig | None,
    request: dict[str, Any],
) -> dict[str, Any]:
    old = db.execute(
        "SELECT * FROM source_task_bindings WHERE client_id=? AND submission_id=?",
        (request["client_id"], request["submission_id"]),
    ).fetchone()
    wire = canonical(request)
    if old:
        if canonical(source_task_policy.submission(old["request_json"])) != wire:
            raise InvalidInput("source task submission conflict")
        run = execution_ledger.get_run(db, old["run_id"])
        authorize(config, db, run)
        return {"run_id": run["id"], "state": run["state"], "reused": True}
    policy = source_processing_policy.model(config, execution)
    settings = source_processing_policy.inspect(config, policy)
    if (
        settings["configuration_sha256"] != request["configuration_sha256"]
        or policy.digest != request["model_configuration_sha256"]
    ):
        raise InvalidInput("source processing configuration changed")
    parent = db.execute(
        "SELECT * FROM receipts WHERE job_id=? AND owner_client_id=? AND state='stored'",
        (request["receipt_id"], request["client_id"]),
    ).fetchone()
    if parent is None:
        from .auth import Denied

        raise Denied(404)
    size = db.execute(
        "SELECT a.byte_count FROM assets a JOIN version_assets va ON va.asset_id=a.id "
        "WHERE va.version_id=? AND a.id=? AND a.vault_id=?",
        (parent["source_version_id"], request["asset_id"], request["vault_id"]),
    ).fetchone()
    if size is not None and size[0] > settings["effective_policy"]["limits"]["source_bytes"]:
        raise InvalidInput("processing_source_limit")
    raw = original(
        config,
        db,
        request["client_id"],
        request["vault_id"],
        parent["source_version_id"],
        request["asset_id"],
        8 * 1024 * 1024,
    )
    asset = db.execute(
        "SELECT va.original_name,va.media_type FROM version_assets va WHERE "
        "version_id=? AND asset_id=? AND role!='event'",
        (parent["source_version_id"], request["asset_id"]),
    ).fetchone()
    if asset is None or objects.digest(raw) != request["source_sha256"]:
        raise InvalidInput("source asset changed")
    kind, mime = source_selection.kind(raw, asset["original_name"], asset["media_type"])
    if kind not in {"article", "document"} or kind != request["kind"]:
        raise InvalidInput("unsupported or changed source kind")
    frozen = None
    if kind == "document":
        from .material_contract import MIMES

        frozen = source_processing_contract.freeze(
            settings["value"],
            format=MIMES[mime],
            source_sha256=request["source_sha256"],
            source_bytes=len(raw),
        )
        if not source_processing_contract.capability(frozen)["binding_ready"]:
            raise InvalidInput("source_document_worker_not_configured")
        wire = source_task_policy.envelope(request, frozen)
    else:
        CapturePolicy(
            settings["value"]["article_image"], settings["value"]["timeout_seconds"]
        ).validate()
    manifest = Store(config).verify_receipt_locked(db, request["receipt_id"])
    batch_id, account_id = None, None
    event = objects.read_object(
        config.state_root,
        objects.object_relative(request["vault_id"], manifest["assets"][0]["sha256"]),
        manifest["assets"][0]["sha256"],
        manifest["assets"][0]["byte_count"],
    )
    try:
        metadata = json.loads(event)
    except ValueError:
        metadata = {}
    if isinstance(metadata, dict) and "batch_id" in metadata:
        batch_id = metadata["batch_id"]
        from .admin_batches import journal

        with journal(config) as inbox:
            row = (
                inbox.execute(
                    "SELECT account_id FROM wx_batches WHERE id=?", (batch_id,)
                ).fetchone()
                if inbox
                else None
            )
        if row is None:
            raise InvalidInput("platform batch binding missing")
        account_id = row[0]
    scope = "client:" + request["client_id"]
    identity = "source-task:" + request["client_id"] + ":" + request["submission_id"]
    receipt = Store(config).ingest_locked(
        db,
        ingress_scope=scope,
        event_id=identity,
        vault=manifest["vault"],
        source_key=identity,
        raw_event=canonical(
            {
                "parent_receipt_id": request["receipt_id"],
                "parent_version_id": parent["source_version_id"],
                "parent_asset_id": request["asset_id"],
                "parent_sha256": request["source_sha256"],
                "batch_id": batch_id,
            }
        ).encode(),
        items=(AssetInput(raw, asset["original_name"], mime, "body"),),
        session_scope=scope,
        session_id=identity,
        retention="session",
        owner_client_id=request["client_id"],
    )
    if receipt.state != "stored":
        raise InvalidInput("source task input not stored")
    article, document = None, None
    value = settings["value"]
    if kind == "article":
        article = article_bindings.ArticleInput(
            receipt.job_id, CapturePolicy(value["article_image"], value["timeout_seconds"])
        )
    else:
        source_asset = db.execute(
            "SELECT asset_id FROM version_assets WHERE version_id=? AND role='body'",
            (receipt.source_version_id,),
        ).fetchone()[0]
        document = document_bindings.DocumentInput(
            receipt.job_id,
            source_asset,
            DocumentPolicy(value["document_image"], value["timeout_seconds"]),
        )
    bound = SourceBinding(
        request["receipt_id"],
        parent["source_version_id"],
        request["asset_id"],
        request["source_sha256"],
        batch_id,
        account_id,
        request["submission_id"],
        wire,
    )
    run_id = execution_ledger.create_run(
        config,
        db,
        policy,
        request["client_id"],
        request["vault_id"],
        receipt.source_version_id,
        article=article,
        document=document,
        source_binding=bound,
    )
    return {"run_id": run_id, "state": "queued", "reused": False}


def launch(
    config: Config, db: sqlite3.Connection, execution: ExecutionConfig | None, run_id: str
) -> dict[str, str]:
    policy = source_processing_policy.model(config, execution)
    run = execution_ledger.get_run(db, run_id)
    bound = db.execute("SELECT * FROM source_task_bindings WHERE run_id=?", (run_id,)).fetchone()
    if bound is None or run["state"] != "queued" or policy.digest != run["config_digest"]:
        raise InvalidInput("source task cannot launch")
    authorize(config, db, run)
    if bound["launch_state"] != "prepared":
        return {"run_id": run_id, "state": bound["launch_state"]}
    db.execute("UPDATE source_task_bindings SET launch_state='launched' WHERE run_id=?", (run_id,))
    try:
        root = contained(config.state_root, "configs/source-runs")
        if not root.exists():
            make_directory(root)
        path = root / (run_id + ".json")
        write_private(
            path,
            canonical(
                {"storage": config.storage_configuration(), "execution": asdict(policy)}
            ).encode(),
        )
        process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "fiber_bundle.source_job_runner",
                "--project-root",
                str(config.project_root),
                "--configuration",
                str(path),
                "--run-id",
                run_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=child_environment(),
            start_new_session=True,
        )
    except (OSError, InvalidInput):
        db.execute(
            "UPDATE source_task_bindings SET launch_state='unknown' WHERE run_id=?", (run_id,)
        )
        execution_ledger.transition(db, run_id, "interrupted", "source_launch_failed")
        raise
    threading.Thread(target=process.wait, daemon=True).start()
    return {"run_id": run_id, "state": "launched"}
