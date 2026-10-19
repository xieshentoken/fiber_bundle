"""Local operator CLI for isolated execution; no UI or provider credentials in output."""

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

from fiber_bundle import (
    article_bindings,
    auth,
    database,
    document_bindings,
    document_ocr,
    execution,
)
from fiber_bundle import execution_ledger as ledger
from fiber_bundle.article_worker import CapturePolicy
from fiber_bundle.config import StorageError, load_config
from fiber_bundle.document_worker import DocumentPolicy
from fiber_bundle.execution_config import load_execution_config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=(
            "create",
            "create-article",
            "create-document",
            "run",
            "status",
            "cancel",
            "recover",
        ),
    )
    parser.add_argument("--project-root", type=Path, default=Path.cwd())
    parser.add_argument("--config", type=Path, default=Path("config.test.json"))
    parser.add_argument("--execution-config", type=Path)
    parser.add_argument("--token-file", type=Path)
    parser.add_argument("--vault-id")
    parser.add_argument("--version-id")
    parser.add_argument("--run-id")
    parser.add_argument("--prompt-file", type=Path)
    parser.add_argument("--receipt-id")
    parser.add_argument("--capture-image")
    parser.add_argument("--asset-id")
    parser.add_argument("--document-image")
    parser.add_argument("--document-timeout", type=int, default=60)
    parser.add_argument("--document-profile", choices=("native", "ocr"), default="native")
    parser.add_argument("--ocr-runtime", type=Path)
    parser.add_argument("--capture-timeout", type=int, default=240)
    args = parser.parse_args()
    result: dict[str, Any]
    try:
        root = args.project_root.resolve(strict=True)
        config = load_config(args.config if args.config.is_absolute() else root / args.config, root)
        if args.command in ("create", "create-article", "create-document", "run"):
            if args.execution_config is None:
                parser.error("--execution-config required")
            policy = load_execution_config(args.execution_config)
        if args.command in ("create", "create-article", "create-document"):
            if args.command in ("create-article", "create-document") and (
                args.vault_id or args.version_id
            ):
                parser.error("target is bound to receipt; omit --vault-id and --version-id")
            if args.command == "create" and not all(
                (args.token_file, args.vault_id, args.version_id)
            ):
                parser.error("create requires --token-file --vault-id --version-id")
            if args.command == "create-article" and not all(
                (args.token_file, args.receipt_id, args.capture_image)
            ):
                parser.error("create-article requires --token-file --receipt-id --capture-image")
            if args.command == "create-document" and not all(
                (args.token_file, args.receipt_id, args.asset_id, args.document_image)
            ):
                parser.error(
                    "create-document requires --token-file --receipt-id --asset-id --document-image"
                )
            with database.locked(config):
                db = database.connect(config.state_root)
                try:
                    client = auth.client(db, "Bearer " + args.token_file.read_text().strip())
                    article = None
                    document = None
                    if args.command == "create-article":
                        source = article_bindings.receipt_source(
                            config, db, client["id"], args.receipt_id
                        )
                        args.vault_id, args.version_id = source.vault_id, source.version_id
                        article = article_bindings.ArticleInput(
                            args.receipt_id, CapturePolicy(args.capture_image, args.capture_timeout)
                        )
                    if args.command == "create-document":
                        source_pdf = document_bindings.source(
                            config, db, client["id"], args.receipt_id, args.asset_id
                        )
                        args.vault_id, args.version_id = source_pdf.vault_id, source_pdf.version_id
                        document = document_bindings.DocumentInput(
                            args.receipt_id,
                            args.asset_id,
                            DocumentPolicy(args.document_image, args.document_timeout),
                            profile=args.document_profile,
                            runtime=document_ocr.runtime(args.ocr_runtime.absolute())
                            if args.ocr_runtime
                            else None,
                        )
                    run_id = ledger.create_run(
                        config,
                        db,
                        policy,
                        client["id"],
                        args.vault_id,
                        args.version_id,
                        article=article,
                        document=document,
                    )
                finally:
                    db.close()
            result = {"run_id": run_id, "state": "queued"}
        else:
            if not args.run_id:
                parser.error("--run-id required")
            execution.identifier(args.run_id)
            if args.command == "run":
                result = execution.execute(
                    config,
                    policy,
                    args.run_id,
                    args.prompt_file.read_text() if args.prompt_file else "",
                )
            else:
                if args.command == "cancel":
                    execution.cancel(config, args.run_id)
                elif args.command == "recover":
                    execution.recover(config, args.run_id)
                with database.locked(config):
                    db = database.connect(config.state_root)
                    try:
                        row = ledger.get_run(db, args.run_id)
                        result = {
                            "run_id": row["id"],
                            "state": row["state"],
                            "error_code": row["error_code"],
                        }
                        document_task = document_bindings.get(db, args.run_id)
                        if document_task is not None:
                            result["document_extract"] = document_bindings.status(document_task)
                        task = article_bindings.get(db, args.run_id)
                        if task is not None:
                            result["article_capture"] = article_bindings.status(task)
                    finally:
                        db.close()
        print(json.dumps(result))
        return int(result["state"] in ("failed", "interrupted", "waiting_budget"))
    except (StorageError, auth.Denied, OSError, ValueError, sqlite3.Error):
        print(json.dumps({"status": "error", "error_code": "execution_rejected"}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
